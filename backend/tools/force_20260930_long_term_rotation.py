from datetime import UTC, date, datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database import SessionLocal
from app.models import LongTermPosition, LongTermTradeEvent
from app.services.long_term_selection import force_historical_long_term_rotations


TRADE_DATE = date(2026, 9, 30)
EXECUTED_AT = datetime(2026, 9, 30, 1, 26, tzinfo=UTC)

TARGETS = {
    "2409": ("友達", "上市", "光電", 38.55),
    "3529": ("力旺", "上櫃", "半導體", 3540.0),
    "6533": ("晶心科", "上市", "半導體", 333.5),
    "6207": ("雷科", "上櫃", "電子零組件", 151.0),
    "6672": ("騰輝電子-KY", "上市", "電子零組件", 369.0),
    "6933": ("AMAX-KY", "上市", "電腦及週邊設備", 382.5),
    "8040": ("九暘", "上櫃", "半導體", 99.7),
    "8227": ("巨有科技", "上櫃", "半導體", 370.5),
    "3094": ("聯傑", "上市", "半導體", 61.2),
    "3580": ("友威科", "上櫃", "光電", 114.0),
}

PLANS = {
    "focused_long": [
        ("6426", "2409", 356.0),
        ("6668", "3529", 47.6),
        ("6510", "6533", 3190.0),
    ],
    "long_only": [
        ("3443", "2409", 8100.0),
        ("2454", "6533", 5065.0),
        ("2330", "6207", 2500.0),
        ("6668", "6672", 47.6),
        ("6426", "6933", 356.0),
        ("6510", "8040", 3190.0),
        ("7717", "8227", 599.0),
        ("6173", "3094", 303.0),
        ("8064", "3580", 146.5),
    ],
}


def replacement_spec(old_symbol: str, new_symbol: str, exit_price: float) -> dict[str, object]:
    name, market, industry, entry_price = TARGETS[new_symbol]
    return {
        "fromSymbol": old_symbol,
        "toSymbol": new_symbol,
        "name": name,
        "market": market,
        "industry": industry,
        "exitPrice": exit_price,
        "entryPrice": entry_price,
    }


def main() -> None:
    results = []
    for mode, plan in PLANS.items():
        with SessionLocal() as db:
            results.append(force_historical_long_term_rotations(
                db,
                mode,  # type: ignore[arg-type]
                TRADE_DATE,
                EXECUTED_AT,
                [replacement_spec(*item) for item in plan],
            ))

    with SessionLocal() as db:
        verification = {}
        for mode, expected_count in (("focused_long", 3), ("long_only", 10)):
            positions = list(db.scalars(select(LongTermPosition).where(
                LongTermPosition.portfolio_mode == mode,
                LongTermPosition.status == "open",
            )).all())
            if len(positions) != expected_count:
                raise RuntimeError(f"{mode} open position count is {len(positions)}, expected {expected_count}")
            events = list(db.scalars(select(LongTermTradeEvent).where(
                LongTermTradeEvent.portfolio_mode == mode,
                LongTermTradeEvent.trade_date == TRADE_DATE,
            )).all())
            verification[mode] = {
                "symbols": sorted(item.stock_code for item in positions),
                "weight": round(sum(float(item.allocation_weight_pct) for item in positions), 4),
                "todayBuys": sum(item.event_type == "BUY" for item in events),
                "todaySells": sum(item.event_type == "SELL" for item in events),
            }
    print({"results": results, "verification": verification})


if __name__ == "__main__":
    main()
