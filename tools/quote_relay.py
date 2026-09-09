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
    # Separate clients ensure the relay credential is never sent to the exchange.
    with httpx.Client(timeout=10, headers={"Authorization": "Bearer " + config["token"]}) as cloud, httpx.Client(
        timeout=6, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://mis.twse.com.tw/stock/fibest.jsp"}
    ) as exchange:
        while True:
            started = clock.monotonic()
            now = datetime.now(ZoneInfo("Asia/Taipei"))
            if not args.once and (now.weekday() >= 5 or not time(9) <= now.time() <= time(13, 31)):
                status_path.write_text(json.dumps({"state": "WAITING_MARKET", "updatedAt": now.isoformat()}), encoding="utf-8")
                clock.sleep(30)
                continue
            try:
                response = cloud.get(base + "/targets")
                response.raise_for_status()
                targets = response.json()["items"]
                accepted = rejected = 0
                for offset in range(0, len(targets), 50):
                    batch = targets[offset:offset+50]
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
            clock.sleep(max(1, min(60, 10*max(1, failures)) - (clock.monotonic()-started)))


if __name__ == "__main__":
    main()
