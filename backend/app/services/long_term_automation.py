from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime, time
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..database import BackgroundSessionLocal as SessionLocal
from ..models import LongTermPortfolioRun
from .adaptive_electronic_automation import fetch_adaptive_scan_payload
from .day_trading_schedule import is_twse_trading_day
from .long_term_selection import (
    LONG_TERM_SELECTION_TIME,
    LONG_TERM_START_DATE,
    benchmark_definitions,
    benchmark_quote_requests,
    long_term_portfolio_has_vacancies,
    replenish_long_term_vacancies,
    run_long_term_selection,
    update_benchmarks,
)
from .long_term_benchmarks import (
    discover_top_ten_year_cagr,
    has_cagr_selection,
    save_cagr_selection,
    stored_stock_directory,
)
from .official_market_data import official_market_data_provider


logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
SELECTION_CLOCK = time.fromisoformat(LONG_TERM_SELECTION_TIME)


class LongTermSelectionAutomation:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._state: dict[str, object] = {
            "status": "stopped",
            "startDate": LONG_TERM_START_DATE.isoformat(),
            "selectionTime": LONG_TERM_SELECTION_TIME,
            "lastRunAt": None,
            "lastResult": None,
            "lastError": None,
        }

    @property
    def state(self) -> dict[str, object]:
        return dict(self._state)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._state["status"] = "running"
        self._task = asyncio.create_task(self._run(), name="long-term-selection-automation")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        self._state["status"] = "stopped"

    async def run_once(self, now: datetime | None = None, *, force: bool = False) -> dict[str, object]:
        current = now or datetime.now(UTC)
        local = current.astimezone(TAIPEI)
        self._state["lastRunAt"] = current.isoformat()
        if local.date() < LONG_TERM_START_DATE:
            result = {"status": "waiting_start", "startDate": LONG_TERM_START_DATE.isoformat()}
        elif not force and (not is_twse_trading_day(local.date()) or local.time() < SELECTION_CLOCK):
            result = {"status": "waiting_session", "selectionTime": LONG_TERM_SELECTION_TIME}
        else:
            with SessionLocal() as db:
                already_ran = db.scalar(select(LongTermPortfolioRun.id).where(
                    LongTermPortfolioRun.trade_date == local.date(),
                ).limit(1))
                needs_cagr_selection = not has_cagr_selection(db, local.date())
            if needs_cagr_selection:
                try:
                    with SessionLocal() as db:
                        fallback_directory = stored_stock_directory(db)
                    candidates = await discover_top_ten_year_cagr(fallback_directory)
                    with SessionLocal() as db:
                        save_cagr_selection(db, local.date(), candidates, current)
                except Exception:
                    logger.warning("Long-term 10-year CAGR benchmark discovery unavailable", exc_info=True)

            with SessionLocal() as db:
                active_benchmarks = benchmark_definitions(db)
                quote_requests = benchmark_quote_requests(db)
            try:
                benchmark_quotes = await official_market_data_provider.get_quotes(quote_requests)
                benchmark_prices = {
                    symbol: quote.price for symbol, quote in benchmark_quotes.items()
                }
            except Exception:
                logger.warning("Long-term benchmark quotes unavailable", exc_info=True)
                benchmark_prices = {}

            if already_ran is not None and not force:
                with SessionLocal() as db:
                    has_vacancies = long_term_portfolio_has_vacancies(db)
                replenished = {"long_only": 0, "focused_long": 0}
                if has_vacancies:
                    payload = await fetch_adaptive_scan_payload()
                    if payload.market.trade_date == local.date():
                        with SessionLocal() as db:
                            replenished = replenish_long_term_vacancies(db, payload, current)
                with SessionLocal() as db:
                    update_benchmarks(
                        db, local.date(), current, benchmark_prices, active_benchmarks,
                    )
                    db.commit()
                result = {
                    "status": "already_ran",
                    "tradeDate": local.date().isoformat(),
                    "benchmarkCount": len(active_benchmarks),
                    "replenished": replenished,
                }
                self._state["lastSuccessAt"] = datetime.now(UTC).isoformat()
            else:
                payload = await fetch_adaptive_scan_payload()
                if not force and payload.market.trade_date != local.date():
                    result = {
                        "status": "waiting_current_quotes",
                        "payloadTradeDate": payload.market.trade_date.isoformat(),
                    }
                else:
                    with SessionLocal() as db:
                        result = run_long_term_selection(
                            db,
                            payload,
                            current,
                            benchmark_prices=benchmark_prices,
                            active_benchmark_definitions=active_benchmarks,
                        )
                    self._state["lastSuccessAt"] = datetime.now(UTC).isoformat()
        self._state.update({"status": "running", "lastResult": result, "lastError": None})
        return result

    async def _run(self) -> None:
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception("Long-term selection automation cycle failed")
                self._state.update({"status": "error", "lastError": str(error)[:500]})
            await asyncio.sleep(300)


long_term_selection_automation = LongTermSelectionAutomation()
