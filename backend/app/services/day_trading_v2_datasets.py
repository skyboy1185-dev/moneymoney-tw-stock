from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
from typing import Iterable

from .day_trading_v2 import MinuteBar


REQUIRED_COLUMNS = {
    "symbol", "timestamp", "open", "high", "low", "close", "volume", "sector",
    "index_price", "index_vwap", "index_trend_1m_pct", "index_trend_5m_pct",
    "market_breadth_pct", "market_relative_volume", "quote_coverage_pct",
}
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


class DatasetValidationError(ValueError):
    pass


def data_directory() -> Path | None:
    raw = os.getenv("DTV2_OPTIMIZATION_DATA_DIR", "").strip()
    if not raw:
        from ..config import get_settings
        raw = get_settings().dtv2_optimization_data_dir.strip()
    return Path(raw).resolve() if raw else None


def _validate_row(row: dict[str, object]) -> tuple[str, datetime, MinuteBar, str]:
    missing = REQUIRED_COLUMNS - set(row)
    if missing:
        raise DatasetValidationError(f"缺少欄位：{', '.join(sorted(missing))}")
    timestamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise DatasetValidationError("timestamp 必須包含時區")
    symbol = str(row["symbol"]).strip().upper()
    sector = str(row["sector"]).strip()
    if not symbol or not sector:
        raise DatasetValidationError("symbol 與 sector 不可空白")
    bar = MinuteBar(
        timestamp=timestamp, open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])), close=Decimal(str(row["close"])), volume=int(row["volume"]),
    )
    if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0 or bar.high < bar.low:
        raise DatasetValidationError("OHLCV 數值無效")
    return symbol, timestamp, bar, sector


def parse_dataset(content: bytes, data_format: str):
    from .day_trading_v2_controller import MarketInputs, classify_market
    fmt = data_format.upper()
    try:
        if fmt == "CSV":
            reader: Iterable[dict[str, object]] = csv.DictReader(StringIO(content.decode("utf-8-sig")))
        elif fmt == "PARQUET":
            import pyarrow.parquet as parquet  # type: ignore[import-not-found]
            table = parquet.read_table(BytesIO(content))
            reader = table.to_pylist()
        else:
            raise DatasetValidationError("只支援 CSV 或 PARQUET")
    except DatasetValidationError:
        raise
    except Exception as exc:
        raise DatasetValidationError(f"{fmt}檔案無法解析：{str(exc)[:300]}") from exc
    datasets: dict[str, list[MinuteBar]] = {}
    sectors: dict[str, str] = {}
    regimes: dict[datetime, str] = {}
    days = set()
    rows = 0
    for row_number, raw in enumerate(reader, start=2):
        try:
            symbol, timestamp, bar, sector = _validate_row(dict(raw))
            regime = classify_market(MarketInputs(
                index_price=Decimal(str(raw["index_price"])), index_vwap=Decimal(str(raw["index_vwap"])),
                trend_1m_pct=Decimal(str(raw["index_trend_1m_pct"])), trend_5m_pct=Decimal(str(raw["index_trend_5m_pct"])),
                breadth_pct=Decimal(str(raw["market_breadth_pct"])), relative_volume=Decimal(str(raw["market_relative_volume"])),
                quote_coverage_pct=Decimal(str(raw["quote_coverage_pct"])), data_normal=True,
                strong_sector_count=int(raw.get("strong_sector_count") or 0), weak_sector_count=int(raw.get("weak_sector_count") or 0),
            )).effective
        except DatasetValidationError as exc:
            raise DatasetValidationError(f"第{row_number}列：{exc}") from exc
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise DatasetValidationError(f"第{row_number}列數值或時間格式無效：{str(exc)[:200]}") from exc
        datasets.setdefault(symbol, []).append(bar)
        sectors[symbol] = sector
        if timestamp in regimes and regimes[timestamp] != regime:
            raise DatasetValidationError("同一時間的市場狀態欄位不一致")
        regimes[timestamp] = regime
        days.add(timestamp.date())
        rows += 1
    if not rows:
        raise DatasetValidationError("資料集沒有任何分鐘行情")
    for bars in datasets.values():
        bars.sort(key=lambda item: item.timestamp)
        if len({bar.timestamp for bar in bars}) != len(bars):
            raise DatasetValidationError("同股票包含重複 timestamp")
    quality = {
        "rowCount": rows, "symbolCount": len(datasets), "tradingDayCount": len(days),
        "startDate": min(days).isoformat(), "endDate": max(days).isoformat(),
        "requiredColumns": sorted(REQUIRED_COLUMNS), "timezoneVerified": True, "marketContextVerified": True,
    }
    return datasets, sectors, regimes, quality


def persist_dataset(content: bytes, *, dataset_id: str, data_format: str) -> tuple[Path, str]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise DatasetValidationError("資料檔超過512MB限制")
    root = data_directory()
    if root is None:
        raise DatasetValidationError("尚未設定 DTV2_OPTIMIZATION_DATA_DIR 持久化資料目錄")
    root.mkdir(parents=True, exist_ok=True)
    suffix = ".csv" if data_format.upper() == "CSV" else ".parquet"
    target = (root / f"{dataset_id}{suffix}").resolve()
    if root not in target.parents:
        raise DatasetValidationError("資料集路徑無效")
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return target, sha256(content).hexdigest()


def load_dataset(path: str, expected_checksum: str, data_format: str):
    target = Path(path).resolve()
    root = data_directory()
    if root is None or root not in target.parents:
        raise DatasetValidationError("資料集不在設定的持久化目錄")
    content = target.read_bytes()
    if sha256(content).hexdigest() != expected_checksum:
        raise DatasetValidationError("資料集雜湊不符，已停止優化")
    return parse_dataset(content, data_format)


def quality_json(value: dict[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
