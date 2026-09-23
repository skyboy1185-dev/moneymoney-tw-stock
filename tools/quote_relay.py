"""Local official MIS transport. Credentials stay in a local private file.

Run with --config PATH. The cloud selects symbols and validates exchange time.
No trading commands are available to this process.
"""
import argparse
from datetime import datetime, time
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time as clock
from zoneinfo import ZoneInfo

import httpx


def scheduled_batches(targets, attempts, now):
    """All held/priority symbols plus one fair rotating baseline batch."""
    targets = list({r["symbol"]: r for r in targets}.values())
    priority = [r for r in targets if r.get("priority", False)]
    baseline = [r for r in targets if not r.get("priority", False)]
    allowed = {r["symbol"] for r in baseline}
    for symbol in list(attempts):
        if symbol not in allowed:
            del attempts[symbol]
    batch = sorted(baseline, key=lambda r: attempts.get(r["symbol"], float("-inf")))[:200]
    for row in batch:
        attempts[row["symbol"]] = now
    return [priority[i:i+50] for i in range(0, len(priority), 50)] + [batch[i:i+50] for i in range(0, len(batch), 50)]


def industry_targets(client):
    """Build the full listed/OTC equity universe where MIS is reachable."""
    targets = []
    sources = (
        ("https://openapi.twse.com.tw/v1/opendata/t187ap03_L", "公司代號", "公司簡稱", "產業別", "上市"),
        ("https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O", "SecuritiesCompanyCode", "CompanyAbbreviation", "SecuritiesIndustryCode", "上櫃"),
    )
    for url, symbol_key, name_key, industry_key, market in sources:
        response = client.get(url)
        response.raise_for_status()
        for row in response.json():
            symbol = str(row.get(symbol_key) or "").strip()
            if symbol.isdigit() and len(symbol) == 4 and str(row.get(industry_key) or "").strip():
                targets.append({"symbol": symbol, "name": str(row.get(name_key) or symbol).strip(),
                                "market": market, "priority": False})
    return targets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    # A second login/manual start must not double the exchange request rate.
    lock_file = args.config.with_suffix(".lock").open("a+b")
    if os.name == "nt" and not args.once:
        import msvcrt
        lock_file.seek(0)
        lock_file.write(b"1")
        lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
    config = json.loads(args.config.read_text(encoding="utf-8"))
    log = logging.getLogger("quote-relay")
    log.setLevel(logging.INFO)
    handler = RotatingFileHandler(args.config.with_suffix(".log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(handler)
    status_path = args.config.with_suffix(".status.json")
    base = config["backend"].rstrip("/") + "/api/v1/market-data/relay"
    failures = 0
    baseline_attempts = {}
    # Separate clients ensure the relay credential is never sent to the exchange.
    with httpx.Client(timeout=10, headers={"Authorization": "Bearer " + config["token"]}) as cloud, httpx.Client(
        timeout=6, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/fibest.jsp"}
    ) as exchange:
        try:
            full_market_targets = industry_targets(exchange)
        except Exception:
            full_market_targets = []
        while True:
            started = clock.monotonic()
            now = datetime.now(ZoneInfo("Asia/Taipei"))
            if not args.once and (now.weekday() >= 5 or not time(9) <= now.time() <= time(13, 31)):
                status_path.write_text(json.dumps({"state": "WAITING_MARKET", "updatedAt": now.isoformat(), "targetIntervalSeconds": 5, "baselineBatchSize": 200}), encoding="utf-8")
                clock.sleep(30)
                continue
            try:
                response = cloud.get(base + "/targets")
                response.raise_for_status()
                targets = list({row["symbol"]: row for row in [*full_market_targets, *response.json()["items"]]}.values())
                accepted = rejected = 0
                batches = scheduled_batches(targets, baseline_attempts, clock.monotonic())
                for batch in batches:
                    channels = "|".join(("tse" if r["market"] == "上市" else "otc") + "_" + r["symbol"] + ".tw" for r in batch)
                    response = exchange.get("https://mis.twse.com.tw/stock/api/getStockInfo.jsp",
                        params={"ex_ch": channels, "json": "1", "delay": "0", "_": str(int(clock.time()*1000))})
                    response.raise_for_status()
                    rows = response.json().get("msgArray", [])
                    if rows:
                        response = cloud.post(base + "/quotes", json={"rows": rows})
                        response.raise_for_status()
                        result = response.json()
                        accepted += result["accepted"]
                        rejected += result["rejected"]
                    clock.sleep(.5)
                status = {"state": "RELAYING" if accepted else "NO_FRESH_QUOTES", "updatedAt": now.isoformat(),
                          "targets": len(targets), "accepted": accepted, "rejected": rejected,
                          "priorityTargets": sum(bool(r.get("priority")) for r in targets),
                          "scheduledTargets": sum(len(batch) for batch in batches), "targetIntervalSeconds": 5,
                          "cycleSeconds": round(clock.monotonic()-started, 2)}
                status_path.write_text(json.dumps(status), encoding="utf-8")
                log.info("cycle targets=%s accepted=%s rejected=%s", len(targets), accepted, rejected)
                failures = 0
            except Exception as exc:
                failures += 1
                # Exception URLs and headers are deliberately not logged.
                http_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                log.warning("cycle failed: %s status=%s", type(exc).__name__, http_status)
                status_path.write_text(json.dumps({"state": "ERROR", "errorType": type(exc).__name__,
                    "httpStatus": http_status, "updatedAt": now.isoformat(), "consecutiveFailures": failures}), encoding="utf-8")
            if args.once:
                break
            clock.sleep(max(.1, min(60, 5*max(1, failures)) - (clock.monotonic()-started)))


if __name__ == "__main__":
    main()
