"""Official TAIFEX data used by the pre-market report."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

BASE = "https://openapi.taifex.com.tw/v1"
ENDPOINTS = {
    "futures": "DailyMarketReportFut",
    "put_call": "PutCallRatio",
    "institutions": "MarketDataOfMajorInstitutionalTradersDetailsOfFuturesContractsBytheDate",
    "fx": "DailyForeignExchangeRates",
}

def _number(value: Any) -> float | None:
    try:return float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):return None

def parse_taifex(rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    futures=rows.get("futures",[])
    latest=max((str(row.get("Date", "")) for row in futures),default="")
    names={"TX":"台指期夜盤","TE":"電子期夜盤","SOF":"臺灣永續期貨夜盤"}
    for code,name in names.items():
        candidates=[row for row in futures if str(row.get("Date"))==latest and row.get("Contract")==code and row.get("TradingSession")=="盤後" and _number(row.get("Volume"))]
        if not candidates:continue
        row=min(candidates,key=lambda item:str(item.get("ContractMonth(Week)","999999")))
        change=_number(row.get("%"));price=_number(row.get("Last"));change_points=_number(row.get("Change"))
        if change is not None and price is not None:
            contract=str(row.get("ContractMonth(Week)") or "")
            result[{"TX":"tx_night","TE":"te_night","SOF":"sof_night"}[code]]={"name":name,"price":price,"changePct":change,"changePoints":change_points,"contract":contract,"volume":int(_number(row.get("Volume")) or 0),"dataTime":latest,"source":"臺灣期貨交易所每日行情日報","isRealtime":False}
    pcr=max(rows.get("put_call",[]),key=lambda row:str(row.get("Date","")),default=None)
    if pcr:
        result["put_call_ratio"]={"name":"臺指選擇權 Put/Call Ratio","volumeRatio":_number(pcr.get("PutCallVolumeRatio%")),"openInterestRatio":_number(pcr.get("PutCallOIRatio%")),"dataTime":str(pcr.get("Date")),"source":"臺灣期貨交易所"}
    institutions=rows.get("institutions",[]);institution_day=max((str(row.get("Date","")) for row in institutions),default="")
    foreign=next((row for row in institutions if str(row.get("Date"))==institution_day and row.get("ContractCode")=="臺股期貨" and row.get("Item")=="外資及陸資"),None)
    if foreign:
        result["foreign_futures_oi"]={"name":"外資臺股期貨未平倉淨額","netContracts":int(_number(foreign.get("OpenInterest(Net)")) or 0),"longContracts":int(_number(foreign.get("OpenInterest(Long)")) or 0),"shortContracts":int(_number(foreign.get("OpenInterest(Short)")) or 0),"dataTime":institution_day,"source":"臺灣期貨交易所"}
    fx=max(rows.get("fx",[]),key=lambda row:str(row.get("Date","")),default=None)
    if fx and _number(fx.get("USD/NTD")) is not None:
        result["usdtwd"]={"name":"美元兌新台幣","price":_number(fx.get("USD/NTD")),"dataTime":str(fx.get("Date")),"source":"臺灣期貨交易所每日外幣參考匯率"}
    return result

class TaifexMarketProvider:
    def __init__(self):
        self._snapshot={};self._refreshed_at:datetime|None=None;self._lock=asyncio.Lock()
    def snapshot(self):return self._snapshot
    async def refresh(self,now=None,force=False):
        current=now or datetime.now(UTC)
        if not force and self._refreshed_at and current-self._refreshed_at<timedelta(minutes=10):return self._snapshot
        async with self._lock:
            async with httpx.AsyncClient(timeout=15,headers={"Accept":"application/json","User-Agent":"TWSE pre-market analysis"}) as client:
                responses=await asyncio.gather(*(client.get(f"{BASE}/{path}") for path in ENDPOINTS.values()),return_exceptions=True)
            rows={};errors={}
            for (key,_),response in zip(ENDPOINTS.items(),responses,strict=True):
                if isinstance(response,Exception):errors[key]=str(response)[:180];continue
                try:response.raise_for_status();rows[key]=response.json()
                except (httpx.HTTPError,ValueError) as exc:errors[key]=str(exc)[:180]
            self._snapshot={"data":parse_taifex(rows),"errors":errors,"updatedAt":current.isoformat()};self._refreshed_at=current
            return self._snapshot

taifex_market_provider=TaifexMarketProvider()
