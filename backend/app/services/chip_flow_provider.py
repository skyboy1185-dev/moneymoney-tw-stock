from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import logging
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from .chip_flow_types import NormalizedTradeTick, TradeSession


TAIPEI = ZoneInfo("Asia/Taipei")
UTC = ZoneInfo("UTC")
logger = logging.getLogger(__name__)
FUGLE_MAX_TRADES_PAGE_SIZE = 500


@dataclass(frozen=True, slots=True)
class RealtimeTradeCapabilities:
    source: str
    available: bool
    complete_intraday_ticks: bool
    has_trade_id: bool
    has_bid_ask_at_trade: bool
    has_source_side: bool
    reason: str
    missing_fields: tuple[str, ...]


class RealtimeTradeProvider(Protocol):
    @property
    def capabilities(self) -> RealtimeTradeCapabilities: ...

    async def get_trade_ticks(
        self,
        stock_id: str,
        trade_date: date,
    ) -> list[NormalizedTradeTick]: ...

    def subscribe(
        self,
        stock_id: str,
        callback: Callable[[NormalizedTradeTick], None],
    ) -> None: ...

    def unsubscribe(self, stock_id: str) -> None: ...


class TwseMisSnapshotTradeProvider:
    """Capability adapter for the existing MIS source.

    MIS currently supplies the latest quote/order-book snapshot and cumulative
    volume, not a replayable lossless trade-tick stream. It must never be
    converted into synthetic trade ticks.
    """

    capabilities = RealtimeTradeCapabilities(
        source="TWSE MIS 五檔快照",
        available=False,
        complete_intraday_ticks=False,
        has_trade_id=False,
        has_bid_ask_at_trade=False,
        has_source_side=False,
        reason=(
            "目前行情來源只有最新成交／五檔快照與累積成交量，"
            "沒有可回補且可去重的完整逐筆成交，無法可靠計算大小單。"
        ),
        missing_fields=(
            "完整逐筆成交時間序列",
            "每筆成交股數",
            "穩定唯一成交 ID",
            "成交當下買一／賣一",
            "逐筆內外盤或成交方向",
        ),
    )

    async def get_trade_ticks(
        self,
        stock_id: str,
        trade_date: date,
    ) -> list[NormalizedTradeTick]:
        del stock_id, trade_date
        return []

    def subscribe(
        self,
        stock_id: str,
        callback: Callable[[NormalizedTradeTick], None],
    ) -> None:
        del stock_id, callback

    def unsubscribe(self, stock_id: str) -> None:
        del stock_id


@dataclass(slots=True)
class _FugleCursor:
    offset: int
    ticks: dict[str, NormalizedTradeTick]


class FugleRealtimeTradeProvider:
    """Normalizes Fugle intraday trades into lossless chip-flow ticks.

    Regular-session ``size`` is reported in board lots and is converted to
    shares. The ``oddlot`` endpoint reports shares and is fetched separately.
    Pagination cursors are kept per stock, date and session type so routine
    refreshes only request newly appended rows.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.fugle.tw/marketdata/v1.0",
        page_size: int = FUGLE_MAX_TRADES_PAGE_SIZE,
        max_pages: int = 100,
        include_odd_lot: bool = True,
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._page_size = min(FUGLE_MAX_TRADES_PAGE_SIZE, max(1, page_size))
        self._max_pages = max(1, max_pages)
        self._include_odd_lot = include_odd_lot
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._cursors: dict[tuple[str, date, str], _FugleCursor] = {}
        self._callbacks: dict[str, set[Callable[[NormalizedTradeTick], None]]] = {}

    @property
    def capabilities(self) -> RealtimeTradeCapabilities:
        configured = bool(self._api_key)
        return RealtimeTradeCapabilities(
            source="Fugle 即時成交明細" if configured else "TWSE MIS 五檔快照",
            available=configured,
            complete_intraday_ticks=configured,
            has_trade_id=configured,
            has_bid_ask_at_trade=configured,
            has_source_side=False,
            reason=(
                "已串接 Fugle 當日成交明細，成交方向由成交價、買一、賣一與 Tick Rule 推估。"
                if configured
                else (
                    "目前 TWSE MIS 僅有最新五檔快照與累積量，缺少可回放的逐筆成交。"
                    "請設定 FUGLE_MARKETDATA_API_KEY 後啟用正式盤中籌碼。"
                )
            ),
            missing_fields=(
                ()
                if configured
                else (
                    "逐筆成交時間與成交股數",
                    "行情來源成交流水號",
                    "成交當下買一與賣一",
                )
            ),
        )

    async def get_trade_ticks(
        self,
        stock_id: str,
        trade_date: date,
    ) -> list[NormalizedTradeTick]:
        if not self._api_key:
            return []
        if trade_date != datetime.now(TAIPEI).date():
            return self._cached_ticks(stock_id, trade_date)

        session_types = ("regular", "oddlot") if self._include_odd_lot else ("regular",)
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={"X-API-KEY": self._api_key},
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for session_type in session_types:
                await self._refresh_session(client, stock_id, trade_date, session_type)
        return self._cached_ticks(stock_id, trade_date)

    async def _refresh_session(
        self,
        client: httpx.AsyncClient,
        stock_id: str,
        trade_date: date,
        session_type: str,
    ) -> None:
        key = (stock_id, trade_date, session_type)
        cursor = self._cursors.setdefault(key, _FugleCursor(offset=0, ticks={}))
        offset = cursor.offset
        for _ in range(self._max_pages):
            params: dict[str, str | int] = {
                "offset": offset,
                "limit": self._page_size,
                "sort": "asc",
            }
            if session_type == "oddlot":
                params["type"] = "oddlot"
            response = await client.get(f"/stock/intraday/trades/{stock_id}", params=params)
            response.raise_for_status()
            payload = response.json()
            payload_date = payload.get("date")
            if payload_date and payload_date != trade_date.isoformat():
                return
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise ValueError("Fugle trades response is missing data array")

            for row_index, row in enumerate(rows):
                tick = self._normalize_row(
                    stock_id,
                    trade_date,
                    session_type,
                    row,
                    row_index=offset + row_index,
                )
                if tick is None:
                    continue
                is_new = tick.id not in cursor.ticks
                cursor.ticks[tick.id] = tick
                if is_new:
                    for callback in self._callbacks.get(stock_id, ()):
                        callback(tick)

            offset += len(rows)
            cursor.offset = offset
            if len(rows) < self._page_size:
                return

        raise RuntimeError(
            f"Fugle trades pagination exceeded {self._max_pages} pages "
            f"for {stock_id} ({session_type})"
        )

    @staticmethod
    def _normalize_row(
        stock_id: str,
        trade_date: date,
        session_type: str,
        row: object,
        *,
        row_index: int,
    ) -> NormalizedTradeTick | None:
        if not isinstance(row, dict):
            logger.warning(
                "skipping malformed Fugle trade row",
                extra={"stock_id": stock_id, "row_index": row_index},
            )
            return None
        try:
            serial = str(row["serial"]).strip()
            timestamp = FugleRealtimeTradeProvider._parse_timestamp(row["time"])
            price = Decimal(str(row["price"]))
            raw_size = int(row["size"])
            volume_shares = raw_size if session_type == "oddlot" else raw_size * 1_000
            if (
                not serial
                or timestamp.astimezone(TAIPEI).date() != trade_date
                or price <= 0
                or volume_shares <= 0
            ):
                raise ValueError("invalid serial, date, price or size")
            bid_price = FugleRealtimeTradeProvider._optional_price(row.get("bid"))
            ask_price = FugleRealtimeTradeProvider._optional_price(row.get("ask"))
        except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            logger.warning(
                "skipping invalid Fugle trade row",
                extra={
                    "stock_id": stock_id,
                    "trade_date": trade_date.isoformat(),
                    "session_type": session_type,
                    "row_index": row_index,
                },
            )
            return None

        return NormalizedTradeTick(
            id=f"fugle:{stock_id}:{trade_date.isoformat()}:{session_type}:{serial}",
            stock_id=stock_id,
            trade_date=trade_date,
            timestamp=timestamp.astimezone(TAIPEI),
            price=price,
            volume_shares=volume_shares,
            bid_price=bid_price,
            ask_price=ask_price,
            session=(
                TradeSession.ODD_LOT
                if session_type == "oddlot"
                else TradeSession.REGULAR
            ),
        )

    @staticmethod
    def _parse_timestamp(value: object) -> datetime:
        raw = int(str(value))
        if raw >= 100_000_000_000_000:
            seconds = raw / 1_000_000
        elif raw >= 100_000_000_000:
            seconds = raw / 1_000
        else:
            seconds = raw
        return datetime.fromtimestamp(seconds, tz=UTC)

    @staticmethod
    def _optional_price(value: object) -> Decimal | None:
        if value is None:
            return None
        price = Decimal(str(value))
        return price if price > 0 else None

    def _cached_ticks(
        self,
        stock_id: str,
        trade_date: date,
    ) -> list[NormalizedTradeTick]:
        ticks: dict[str, NormalizedTradeTick] = {}
        for (item_stock, item_date, _), cursor in self._cursors.items():
            if item_stock == stock_id and item_date == trade_date:
                ticks.update(cursor.ticks)
        return sorted(ticks.values(), key=lambda item: (item.timestamp, item.id))

    def subscribe(
        self,
        stock_id: str,
        callback: Callable[[NormalizedTradeTick], None],
    ) -> None:
        self._callbacks.setdefault(stock_id, set()).add(callback)

    def unsubscribe(self, stock_id: str) -> None:
        self._callbacks.pop(stock_id, None)
