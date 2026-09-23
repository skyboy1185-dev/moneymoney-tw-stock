from __future__ import annotations
import asyncio, csv, hashlib, io, json, logging
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from ..config import get_settings
from ..database import BackgroundSessionLocal, SessionLocal
from ..prepost_models import PrePostAnalysisDay, PrePostAnalysisMessage, PrePostNotificationSetting, PrePostPendingEvent
from ..strong_stock_models import StrongStockIndustryRanking, StrongStockMarketRegime, StrongStockRanking
from .day_trading_schedule import is_twse_trading_day
from .gmail_messaging import gmail_notification_dispatcher
from .international_market_data import international_market_provider
from .taifex_market_data import taifex_market_provider
from .worker_supervision import supervise

TAIPEI = ZoneInfo("Asia/Taipei")
logger = logging.getLogger(__name__)
IMPORTANCE = {"NORMAL": 0, "NOTICE": 1, "IMPORTANT": 2, "EMERGENCY": 3}
PRE_STAGES = [(time(8,0),"國際市場","08:00 國際市場與隔夜走勢初步分析"),(time(8,15),"國際市場","國際市場與總體經濟更新"),(time(8,25),"美股與期貨","美股、科技股與美國期貨分析"),(time(8,30),"法人籌碼","台指期、匯率、債券與法人籌碼分析"),(time(8,35),"台股產業分析","台股產業、公司消息與重大事件分析"),(time(8,40),"當沖操作建議","盤前操作與風險控制建議"),(time(8,45),"盤前最終結論","08:45 今日盤勢統整與方向")]
POST_STAGES = [(time(13,30),"台股收盤","台股收盤結果與盤前預測驗證"),(time(15,5),"法人籌碼","15:05 三大法人、期貨與選擇權盤後分析"),(time(15,30),"公司重大消息","公司公告、財報與法說會分析"),(time(18,0),"重大國際事件","歐洲市場與國際事件分析"),(time(21,30),"美股與期貨","美股盤中、半導體與總經分析")]

def trading_day(day: date) -> bool:
    holidays=set()
    for value in get_settings().twse_holidays.split(","):
        with suppress(ValueError): holidays.add(date.fromisoformat(value.strip()))
    return is_twse_trading_day(day, holidays)

def next_trading_day(day: date) -> date:
    day += timedelta(days=1)
    while not trading_day(day): day += timedelta(days=1)
    return day

def owner_trade_date(now: datetime) -> date:
    local=now.astimezone(TAIPEI); day=local.date()
    if local.time() < time(6): day -= timedelta(days=1)
    while not trading_day(day): day -= timedelta(days=1)
    return day

def phase_for(now: datetime) -> tuple[date,str,str]:
    local=now.astimezone(TAIPEI); today=local.date(); clock=local.time()
    if clock < time(6): return owner_trade_date(now),"POST_MARKET","POST_MARKET_ANALYZING"
    if not trading_day(today): return today,"BACKGROUND","MARKET_CLOSED"
    if clock < time(8,0): return today,"BACKGROUND","NOT_STARTED"
    if clock <= time(8,45): return today,"PRE_MARKET","PRE_MARKET_ANALYZING"
    if clock < time(9): return today,"PRE_MARKET","PRE_MARKET_COMPLETE"
    if clock < time(13,30): return today,"MARKET","INTRADAY"
    return today,"POST_MARKET","POST_MARKET_ANALYZING"

def ensure_day(db, day: date, now: datetime) -> PrePostAnalysisDay:
    row=db.scalar(select(PrePostAnalysisDay).where(PrePostAnalysisDay.trade_date==day))
    if row: return row
    row=PrePostAnalysisDay(trade_date=day,is_trading_day=trading_day(day),status="NOT_STARTED" if trading_day(day) else "MARKET_CLOSED",current_phase="WAITING",created_at=now,updated_at=now)
    db.add(row); db.flush(); return row

def snapshot(db, day: date):
    rankings=list(db.scalars(select(StrongStockRanking).where(StrongStockRanking.trade_date<=day).order_by(StrongStockRanking.trade_date.desc(),StrongStockRanking.rank).limit(100)).all())
    source_day=rankings[0].trade_date if rankings else None
    rankings=[r for r in rankings if r.trade_date==source_day]
    industries=list(db.scalars(select(StrongStockIndustryRanking).where(StrongStockIndustryRanking.trade_date==source_day).order_by(StrongStockIndustryRanking.rank)).all()) if source_day else []
    international=international_market_provider.snapshot(); quotes=international.get("quotes",{})
    domestic=taifex_market_provider.snapshot().get("data",{})
    market_changes=[quotes[k]["changePct"] for k in ("sp500","nasdaq","sox","tsm","nq_future") if k in quotes]
    local_score=round(min(85,max(15,50+(float(rankings[0].total_score)-70)*1.2))) if rankings else None
    international_score=round(min(85,max(15,50+(sum(market_changes)/len(market_changes))*8))) if market_changes else None
    score=round(local_score*.6+international_score*.4) if local_score is not None and international_score is not None else local_score or international_score
    bias="震盪偏多" if score and score>=60 else "震盪偏空" if score and score<45 else "區間震盪" if score else "資料尚未取得"
    risk="中低" if score and score>=68 else "中高" if score and score<48 else "中等" if score else "資料尚未取得"
    strong=[r.industry for r in industries[:4]]
    weak=[r.industry for r in industries[-4:]] if industries else []
    sources=[{"name":"強勢股策略盤後評分","dataTime":source_day.isoformat() if source_day else None}]
    sources += [{"name":q["source"]+"｜"+q["name"],"dataTime":q["dataTime"]} for q in quotes.values()]
    sources += [{"name":q["source"]+"｜"+q["name"],"dataTime":q["dataTime"]} for q in domestic.values()]
    missing=[]
    required={"美國主要指數":("dow","sp500","nasdaq"),"費城半導體指數":("sox",),"台積電ADR":("tsm",),"NVIDIA、AMD及主要科技股":("nvda","amd"),"美國主要期貨":("es_future","nq_future","ym_future"),"美元指數":("dxy",),"美債殖利率":("us10y",),"原油、黃金及原物料":("oil","gold")}
    missing += [label for label,keys in required.items() if not all(key in quotes for key in keys)]
    domestic_required={"台指期夜盤":("tx_night",),"電子期與半導體期貨":("te_night","sof_night"),"美元兌新台幣":("usdtwd",),"外資期貨未平倉":("foreign_futures_oi",),"選擇權Put/Call Ratio":("put_call_ratio",)}
    missing += [label for label,keys in domestic_required.items() if not all(key in domestic for key in keys)]
    missing += ["總經數據、Fed談話與即時重大新聞"]
    expected_items=len(INSTRUMENT_KEYS := ("dow","sp500","nasdaq","sox","tsm","nvda","amd","es_future","nq_future","ym_future","dxy","us10y","oil","gold"))+len(domestic_required)+1
    available_items=sum(key in quotes for key in INSTRUMENT_KEYS)+sum(all(key in domestic for key in keys) for keys in domestic_required.values())+bool(rankings)
    completeness=round(available_items/expected_items*100)
    score_breakdown={
        "localMomentum": local_score,
        "internationalRisk": international_score,
        "technologyChain": round(50+sum(quotes[key]["changePct"] for key in ("sox","tsm","nvda","amd") if key in quotes)/max(1,sum(key in quotes for key in ("sox","tsm","nvda","amd")))*8) if any(key in quotes for key in ("sox","tsm","nvda","amd")) else None,
        "futures": round(50+sum(quotes[key]["changePct"] for key in ("es_future","nq_future","ym_future") if key in quotes)/max(1,sum(key in quotes for key in ("es_future","nq_future","ym_future")))*8) if any(key in quotes for key in ("es_future","nq_future","ym_future")) else None,
        "dataCompleteness": completeness,
    }
    data_times=[datetime.fromisoformat(q["dataTime"]) for q in quotes.values()]
    local_time=datetime.combine(source_day,time(14,30),TAIPEI).astimezone(UTC) if source_day else None
    data_time=max(data_times+([local_time] if local_time else []),default=datetime.now(UTC))
    return {"score":score,"bias":bias,"risk":risk,"strong":strong,"weak":weak,"rankings":rankings,"sources":sources,"missing":missing,"international":quotes,"domestic":domestic,"data_time":data_time,"score_breakdown":score_breakdown,"data_completeness":completeness}

def advice(data):
    if data["score"] is None: return "資料尚未取得，暫不建立方向性部位。", "資料尚未取得，維持低持股並等待可驗證資料。", "0%～20%"
    if data["score"]>=60: return "開盤前15分鐘先觀察，不追第一波；只考慮強勢股拉回不破且量價確認的訊號。", "採分批布局，第一筆不超過預定部位25%，跌破支撐停止加碼。", "25%～40%"
    return "降低出手頻率，沒有量價確認時不進場。", "保留現金，個股跌破支撐時降低持股。", "10%～25%"

def _moves(data, keys):
    quotes=data.get("international",{})
    return "、".join(f"{quotes[key]['name']} {quotes[key]['changePct']:+.2f}%" for key in keys if key in quotes) or "資料尚未取得"

def professional_market_analysis(data, phase="盤前", local_market=None):
    """Create an evidence-based market narrative without filling unavailable facts."""
    quotes=data.get("international",{})
    local=local_market or {}
    score=data.get("score")
    bias=data.get("bias") or "資料尚未取得"
    risk=data.get("risk") or "資料尚未取得"
    position=data.get("position") or advice(data)[2]
    def changes(keys):return [float(quotes[key]["changePct"]) for key in keys if key in quotes and quotes[key].get("changePct") is not None]
    def stance(values):
        if not values:return "資料不足，維持中性判讀"
        average=sum(values)/len(values)
        return "風險偏好明顯回升" if average>=1 else "風險偏好溫和回升" if average>=.25 else "風險趨避明顯升高" if average<=-1 else "風險偏好轉弱" if average<=-.25 else "多空訊號分歧"
    broad=changes(("dow","sp500","nasdaq"));technology=changes(("sox","tsm","nvda","amd"));futures=changes(("es_future","nq_future","ym_future"))
    pressure=changes(("dxy","us10y"));commodities=changes(("oil","gold"))
    if phase=="盤後":
        taiex=local.get("taiex_return_1d");otc=local.get("otc_return_1d");volume=local.get("volume_ratio_20d")
        local_line=(f"台股收盤驗證：加權指數 {taiex:+.2f}%" if taiex is not None else "台股收盤驗證：加權指數漲跌資料尚未取得")
        local_line+=(f"、櫃買指數 {otc:+.2f}%" if otc is not None else "、櫃買指數漲跌資料尚未取得")
        local_line+=f"；量能約為20日均量的 {volume:.2f} 倍。" if volume is not None else "；20日量能比較資料尚未取得。"
        if taiex is not None and otc is not None:
            local_line+=("櫃買表現優於加權，資金參與較偏中小型股。" if otc>taiex else "加權表現優於櫃買，盤面較依賴大型權值股。")
    else:
        local_line=f"盤前基準判斷：盤勢為{bias}，AI盤勢分數{score if score is not None else '資料尚未取得'}，風險等級{risk}，建議持股{position}。此判斷仍須由開盤後的加權、櫃買、台積電與成交量同步性確認。"
    equity_line=f"全球風險情緒：美國主要指數呈現{stance(broad)}；科技與半導體訊號呈現{stance(technology)}。主要指數為{_moves(data,['dow','sp500','nasdaq'])}；科技鏈為{_moves(data,['sox','tsm','nvda','amd'])}。若費半、台積電ADR與NASDAQ同向，對台股電子權值的參考性較高；若彼此背離，應降低單一指標的權重。"
    macro_line=f"資金與總體環境：美元與美債殖利率訊號為{stance([-value for value in pressure])}，目前數據為{_moves(data,['dxy','us10y'])}；主要期貨為{_moves(data,['es_future','nq_future','ym_future'])}。美元與殖利率同步上升通常壓縮高本益成長股評價，兩者回落則有利風險資產估值修復。"
    commodity_line=f"原物料與避險觀察：{_moves(data,['oil','gold'])}。目前組合訊號為{stance([-value for value in commodities])}。油價快速上升需留意運輸、高耗能及製造成本；黃金與美元同時走強時，通常代表事件風險或避險需求升高。"
    transmission=f"台股傳導與產業配置：相對強勢產業為{'、'.join(data.get('strong') or []) or '資料尚未取得'}，相對弱勢產業為{'、'.join(data.get('weak') or []) or '資料尚未取得'}。基準劇本為{bias}，風險{risk}，建議持股{position}；只在指數、領漲產業與個股量價同向時增加部位，若關鍵支撐失守、國際期貨轉弱或美元與殖利率急升，應停止追價並降低曝險。"
    all_changes=changes(tuple(quotes))
    positive=sum(value>0 for value in all_changes);negative=sum(value<0 for value in all_changes)
    average=sum(all_changes)/len(all_changes) if all_changes else None
    dispersion=(max(all_changes)-min(all_changes)) if len(all_changes)>=2 else None
    statistics=(f"跨市場數據統計：目前取得 {len(all_changes)} 項可比較漲跌資料，{positive} 項上漲、{negative} 項下跌"
        + (f"，平均變動 {average:+.2f}%，最大與最小變動差 {dispersion:.2f} 個百分點。" if average is not None and dispersion is not None else "；樣本不足，暫不計算平均與離散程度。")
        + "平均值用於辨識整體風險偏好，離散度較大代表資金集中於少數資產，不宜只看單一指數推論全面行情。")
    relative="大型股與中小型股相對強弱："
    taiex=local.get("taiex_return_1d");otc=local.get("otc_return_1d")
    if taiex is not None and otc is not None:
        gap=float(otc)-float(taiex)
        relative+=f"櫃買相對加權差 {gap:+.2f} 個百分點；"+("中小型股風險偏好較強，觀察題材擴散與成交量續航。" if gap>0 else "大型權值相對占優，需防指數上漲但多數個股跟不上。")
    else:relative+="加權與櫃買同日報酬資料尚未完整，開盤後以兩者同步性及漲跌家數確認。"
    scenarios=("情境推演：偏多情境需同時看到主要期貨與科技鏈維持正向、加權守住支撐、櫃買不轉弱及強勢產業量價延續；"
        "中性情境為國際指數與期貨分歧、加權與櫃買不同調，此時降低頻率並等待方向；"
        "偏空情境為科技鏈轉弱、美元或殖利率上升且台股跌破支撐，應停止新增部位並優先處理風險。")
    return [local_line,statistics,equity_line,macro_line,commodity_line,relative,transmission,scenarios]

def evaluate_prediction(prediction, taiex_return, otc_return):
    actual=[float(value) for value in (taiex_return,otc_return) if value is not None]
    if not prediction or not actual:return {"status":"資料不足","items":[],"reason":"缺少盤前預測或加權／櫃買收盤漲跌"}
    average=sum(actual)/len(actual);bias=str(prediction.get("bias") or "")
    expected=1 if "多" in bias else -1 if "空" in bias else 0
    actual_direction=1 if average>.15 else -1 if average<-.15 else 0
    correct=expected==actual_direction or (expected==0 and abs(average)<=.35)
    return {"status":"符合" if correct else "未符合","items":[{"name":"盤勢方向","predicted":bias,"actual":f"加權／櫃買平均 {average:+.2f}%","result":"正確" if correct else "偏差"}],"reason":"以加權與櫃買同日平均漲跌驗證方向；±0.15%內視為震盪"}

def sentiment_for(category, title, data):
    quotes=data.get("international",{})
    if title.startswith("08:00"): keys=("dow","sp500","nasdaq","sox","tsm","nvda","amd")
    elif category=="國際市場": keys=("dow","sp500","nasdaq","sox","tsm")
    elif category=="美股與期貨": keys=("sox","tsm","nvda","amd","es_future","nq_future","ym_future")
    elif category=="法人籌碼":
        pressure=[quotes[key]["changePct"] for key in ("dxy","us10y","oil") if key in quotes]
        value=-(sum(pressure)/len(pressure)) if pressure else 0
        return "利多" if value>=.3 else "利空" if value<=-.3 else "中性"
    elif category in {"台股產業分析","當沖操作建議","盤前最終結論","盤後最終摘要"}:
        score=data.get("score")
        return "利多" if score is not None and score>=60 else "利空" if score is not None and score<45 else "中性"
    else: keys=tuple(quotes)
    changes=[quotes[key]["changePct"] for key in keys if key in quotes]
    average=sum(changes)/len(changes) if changes else 0
    return "利多" if average>=.3 else "利空" if average<=-.3 else "中性"

def analysis_content(category, title, data):
    missing="；".join(data["missing"])
    risk="若盤勢或強勢族群失守，停止新增部位並降低持股。"
    if title.startswith("08:00"):
        return f"隔夜美股收盤與主要資產：{_moves(data,['dow','sp500','nasdaq','sox','tsm','nvda','amd'])}。半導體與台積電ADR相對大盤較強，通常有利台股電子權值；若相對弱勢，開盤追價風險提高。目前初判為{data['bias']}、{data['score'] if data['score'] is not None else '資料尚未取得'}分。風險控制：{risk}"
    if category=="國際市場":
        return f"08:30最新國際變化：美股指數為{_moves(data,['dow','sp500','nasdaq'])}；美元、利率與原物料為{_moves(data,['dxy','us10y','oil','gold'])}。美元、殖利率或油價快速上升會提高台股估值與成本壓力。本輪資料不足項目：{missing}。風險控制：{risk}"
    if category=="美股與期貨":
        return f"科技與期貨焦點：{_moves(data,['sox','tsm','nvda','amd','es_future','nq_future','ym_future'])}。費半、台積電ADR與NASDAQ期貨同向偏強時，半導體及AI供應鏈較有支撐；走勢分歧時先觀察開盤確認，不追第一波。風險控制：{risk}"
    if category=="法人籌碼":
        domestic=data.get("domestic",{});tx=domestic.get("tx_night",{});te=domestic.get("te_night",{});foreign=domestic.get("foreign_futures_oi",{});pcr=domestic.get("put_call_ratio",{});fx=domestic.get("usdtwd",{})
        tx_text=f"{tx['changePct']:+.2f}%" if tx.get("changePct") is not None else "尚未公布"
        te_text=f"{te['changePct']:+.2f}%" if te.get("changePct") is not None else "尚未公布"
        pcr_text=f"{pcr['openInterestRatio']:.2f}%" if pcr.get("openInterestRatio") is not None else "尚未公布"
        return f"匯率、債券與籌碼觀察：{_moves(data,['dxy','us10y'])}。台指期夜盤 {tx_text}，電子期夜盤 {te_text}，美元兌新台幣 {fx.get('price','尚未公布')}，外資臺股期貨未平倉淨額 {foreign.get('netContracts','尚未公布')} 口，Put/Call未平倉比 {pcr_text}。現階段風險為{data['risk']}。風險控制：{risk}"
    if category=="台股產業分析":
        return f"依可驗證的台股評分，今日相對強勢產業為{'、'.join(data['strong']) or '資料尚未取得'}，相對弱勢產業為{'、'.join(data['weak']) or '資料尚未取得'}。國際半導體參考：{_moves(data,['sox','tsm','nvda','amd'])}。只觀察量價同步且未跌破支撐的個股。風險控制：{risk}"
    if category=="當沖操作建議":
        daytrade,stock,position=advice(data)
        return f"當沖建議：{daytrade} 現股建議：{stock} 建議持股比例：{position}。目前判斷為{data['bias']}、AI分數{data['score'] if data['score'] is not None else '資料尚未取得'}、風險{data['risk']}。風險控制：{risk}"
    if category in {"盤前最終結論","盤後最終摘要"}:
        daytrade,stock,position=advice(data)
        return f"綜合結論為{data['bias']}，AI盤勢分數{data['score'] if data['score'] is not None else '資料尚未取得'}，風險等級{data['risk']}，建議持股{position}。國際重點：{_moves(data,['sp500','nasdaq','sox','tsm','nq_future','dxy','us10y','oil'])}。當沖：{daytrade} 現股：{stock} 失效條件與風險控制：{risk}"
    return f"本階段可驗證資料判斷為{data['bias']}，AI盤勢分數{data['score'] if data['score'] is not None else '資料尚未取得'}，風險{data['risk']}。國際市場：{_moves(data,list(data.get('international',{})))}。尚未取得：{missing}。風險控制：{risk}"

async def notify(message: PrePostAnalysisMessage, *, force: bool = False):
    with SessionLocal() as db:
        settings=list(db.scalars(select(PrePostNotificationSetting).where(PrePostNotificationSetting.mail_enabled.is_(True))).all())
    if not settings: return 0
    if not force:
        eligible=[]
        for setting in settings:
            period_enabled=(message.period=="PRE_MARKET" and setting.pre_market) or (message.period=="POST_MARKET" and setting.post_market)
            threshold=IMPORTANCE.get(setting.minimum_importance,2)
            if period_enabled and IMPORTANCE.get(message.importance,0)>=threshold: eligible.append(setting)
        if not eligible:return 0
    sent=await gmail_notification_dispatcher.dispatch(event_type="prepost_analysis",action="重大市場事件" if message.importance=="EMERGENCY" else ("今日盤前操作建議" if message.period=="PRE_MARKET" else "盤後市場更新"),message=message.content,dedupe_key=f"prepost:{message.dedupe_key}",channel_name="盤前盤後分析")
    if sent:
        with SessionLocal() as db:
            row=db.get(PrePostAnalysisMessage,message.id)
            if row: row.mail_sent=True;row.notification_sent=True;row.notification_sent_at=datetime.now(UTC);db.commit()
    return sent

def add_message(db, dayrow, period, category, title, data, now, *, importance="NORMAL", event_key=None, content=None, parent=None):
    day=dayrow.trade_date; key=event_key or f"scheduled:{day}:{period}:{category}:{title}"
    digest=hashlib.sha256(key.encode()).hexdigest()[:24]
    if db.scalar(select(PrePostAnalysisMessage.id).where(PrePostAnalysisMessage.dedupe_key==digest)): return None
    daytrade,stock,position=advice(data)
    body=content or analysis_content(category,title,data)
    prefix="PM" if period=="PRE_MARKET" else "AM"
    # The message id must stay unique even when a prior failed transaction,
    # concurrent worker, or historical correction already used a sequence.
    message_id=f"{prefix}-{day:%Y%m%d}-{digest[:10].upper()}"
    row=PrePostAnalysisMessage(message_id=message_id,trade_date=day,period=period,category=category,importance=importance,title=title,content=body,market_impact=sentiment_for(category,title,data),industry_impact_json=json.dumps(data["strong"],ensure_ascii=False),stock_impact_json=json.dumps([{"symbol":r.symbol,"name":r.name} for r in data["rankings"][:5]],ensure_ascii=False),operation_advice=daytrade if period=="PRE_MARKET" else stock,confidence_score=float(data["score"] or 0),sources_json=json.dumps(data["sources"],ensure_ascii=False),data_time=data["data_time"],dedupe_key=digest,version=1,is_update=bool(parent),parent_message_id=parent,notification_sent=importance in {"IMPORTANT","EMERGENCY"},created_at=now,updated_at=now)
    db.add(row);db.flush();return row

def update_summary(dayrow,data,now,status,phase):
    old=(dayrow.market_bias,dayrow.market_score,dayrow.risk_level,dayrow.recommended_position)
    dt,stock,pos=advice(data)
    dayrow.status=status;dayrow.current_phase=phase;dayrow.market_bias=data["bias"];dayrow.market_score=data["score"];dayrow.risk_level=data["risk"];dayrow.opening_pattern="小幅開高後震盪" if data["score"] and data["score"]>=60 else "區間震盪，等待確認";dayrow.strong_industries_json=json.dumps(data["strong"],ensure_ascii=False);dayrow.weak_industries_json=json.dumps(data["weak"],ensure_ascii=False);dayrow.day_trade_advice=dt;dayrow.stock_advice=stock;dayrow.recommended_position=pos;dayrow.data_as_of=data["data_time"];dayrow.score_breakdown_json=json.dumps(data.get("score_breakdown",{}),ensure_ascii=False);dayrow.market_snapshot_json=json.dumps({key:data.get(key) for key in ("international","domestic","missing","sources","data_completeness")},ensure_ascii=False,default=str)
    if phase=="PRE_MARKET":dayrow.pre_market_prediction_json=json.dumps({"bias":data["bias"],"score":data["score"],"risk":data["risk"],"position":pos,"asOf":data["data_time"]},ensure_ascii=False,default=str)
    dayrow.updated_at=now
    return old,(dayrow.market_bias,dayrow.market_score,dayrow.risk_level,dayrow.recommended_position)

def serialize_message(m):
    sentiment={"震盪偏多":"利多","震盪偏空":"利空","區間震盪":"中性"}.get(m.market_impact,m.market_impact)
    return {"messageId":m.message_id,"tradeDate":m.trade_date,"period":m.period,"createdAt":m.created_at,"dataTime":m.data_time,"category":m.category,"importance":m.importance,"title":m.title,"content":m.content,"marketImpact":sentiment,"industryImpact":json.loads(m.industry_impact_json),"stockImpact":json.loads(m.stock_impact_json),"operationAdvice":m.operation_advice,"confidenceScore":m.confidence_score,"sources":json.loads(m.sources_json),"notificationSent":m.notification_sent,"notificationSentAt":m.notification_sent_at,"mailSent":m.mail_sent,"version":m.version,"isUpdate":m.is_update,"parentMessageId":m.parent_message_id,"markedImportant":m.is_marked_important,"followed":m.is_followed}

def _industry_outlook(industry, score, percentile, member_count, details, quotes):
    technology_keys=("sox","tsm","nvda","amd","nq_future")
    technology=[float(quotes[key]["changePct"]) for key in technology_keys if key in quotes and quotes[key].get("changePct") is not None]
    tech_average=sum(technology)/len(technology) if technology else None
    name=str(industry)
    tech_sector=any(word in name for word in ("半導體","電子","電腦","光電","通訊","AI","伺服器"))
    catalysts=[]
    if tech_sector:
        catalysts += ["AI伺服器與高效運算資本支出", "先進製程、封裝與高速傳輸升級"]
        if tech_average is not None:catalysts.append(f"海外科技鏈平均 {tech_average:+.2f}%")
    elif "金融" in name:catalysts += ["利差與資產品質變化", "股利政策與資本水準"]
    elif any(word in name for word in ("航運","運輸")):catalysts += ["運價與供需變化", "油價及地緣事件"]
    elif any(word in name for word in ("生技","醫療")):catalysts += ["新藥里程碑與授權", "營收落地及現金流"]
    else:catalysts += ["訂單能見度與營收成長", "報價、庫存與毛利率趨勢"]
    risks=["族群量能退潮或領漲股跌破支撐", "評價升高但獲利未同步上修"]
    if tech_sector:risks.append("美債殖利率與美元同步走高")
    stance="優先追蹤" if float(percentile)>=75 else "正向觀察" if float(percentile)>=55 else "等待轉強"
    return {"industry":name,"score":round(float(score),2),"percentile":round(float(percentile),2),"memberCount":member_count,"stance":stance,"catalysts":catalysts,"risks":risks,"confirmation":"產業指數、成交量與至少兩檔領漲股同步轉強","details":details}

def serialize_day(db,d):
    msgs=list(db.scalars(select(PrePostAnalysisMessage).where(PrePostAnalysisMessage.trade_date==d.trade_date).order_by(PrePostAnalysisMessage.created_at)).all())
    try:saved=json.loads(d.market_snapshot_json or "{}")
    except (TypeError,ValueError):saved={}
    live=snapshot(db,d.trade_date) if not saved else saved; quotes=live.get("international",{}); domestic=live.get("domestic",{})
    industry_rows=list(db.scalars(select(StrongStockIndustryRanking).where(StrongStockIndustryRanking.trade_date<=d.trade_date).order_by(StrongStockIndustryRanking.trade_date.desc(),StrongStockIndustryRanking.rank).limit(20)).all())
    industry_day=industry_rows[0].trade_date if industry_rows else None
    industry_rows=[row for row in industry_rows if row.trade_date==industry_day][:8]
    regime=db.scalar(select(StrongStockMarketRegime).where(StrongStockMarketRegime.trade_date==d.trade_date))
    try:local_market=json.loads(regime.source_snapshot_json or "{}") if regime else {}
    except (TypeError,ValueError):local_market={}
    post_indexes={}
    for key,label in (("taiex","加權指數"),("otc","櫃買指數")):
        close=local_market.get(f"{key}_close");change=local_market.get(f"{key}_return_1d")
        if close is not None or change is not None:post_indexes[key]={"name":label,"price":close,"changePct":change,"dataTime":str(d.trade_date),"source":"盤後市場快照"}
    outlook=[]
    for row in industry_rows:
        try:details=json.loads(row.details_json or "{}")
        except (TypeError,ValueError):details={}
        outlook.append(_industry_outlook(row.industry,row.score,row.percentile,row.member_count,details,quotes))
    return {"tradeDate":d.trade_date,"isTradingDay":d.is_trading_day,"status":d.status,"currentPhase":d.current_phase,"latestMarketBias":d.market_bias,"marketScore":d.market_score,"scoreBreakdown":json.loads(d.score_breakdown_json or "{}"),"dataCompleteness":live.get("data_completeness"),"riskLevel":d.risk_level,"openingPattern":d.opening_pattern,"strongIndustries":json.loads(d.strong_industries_json),"weakIndustries":json.loads(d.weak_industries_json),"dayTradeAdvice":d.day_trade_advice,"stockAdvice":d.stock_advice,"recommendedPosition":d.recommended_position,"support":json.loads(d.support_json),"resistance":json.loads(d.resistance_json),"dataAsOf":d.data_as_of,"lastUpdatedAt":d.updated_at,"analysisStatus":"FAILED" if d.analysis_error else "RUNNING" if "ANALYZING" in d.status else "COMPLETED" if d.status in {"FULL_DAY_COMPLETE","PRE_MARKET_COMPLETE"} else "WAITING","error":d.analysis_error,"predictionResult":d.prediction_result,"predictionDetails":json.loads(d.prediction_details_json),"preMarketMessageCount":sum(m.period=="PRE_MARKET" for m in msgs),"postMarketMessageCount":sum(m.period=="POST_MARKET" for m in msgs),"preMarketMessages":[serialize_message(m) for m in msgs if m.period=="PRE_MARKET"],"postMarketMessages":[serialize_message(m) for m in msgs if m.period=="POST_MARKET"],"marketCharts":{"international":quotes,"domestic":domestic,"updatedAt":d.data_as_of,"futuresNote":"台指期夜盤取自期交所最近已公布盤後日報；美國期貨取自 Yahoo Finance 即時圖表，兩者商品與漲跌基準不同。"},"postMarketCharts":{"indexes":post_indexes,"advanceRatio":local_market.get("advance_ratio"),"limitUpCount":local_market.get("limit_up_count"),"limitDownCount":local_market.get("limit_down_count"),"volumeRatio20d":local_market.get("volume_ratio_20d"),"otcRelativeStrength":local_market.get("otc_relative_strength"),"dataTime":str(d.trade_date)},"industryOutlook":{"dataDate":industry_day,"items":outlook}}

class PrePostAutomation:
    def __init__(self): self._task=None;self._stopping=False;self.state={"status":"stopped","lastRunAt":None,"lastError":None}
    async def start(self):
        if self._task and not self._task.done(): return
        self._stopping=False;self._task=asyncio.create_task(supervise(self._run,self.state,stopping=lambda:self._stopping))
    async def stop(self):
        self._stopping=True
        if self._task:self._task.cancel()
        if self._task:
            with suppress(asyncio.CancelledError):await self._task
    async def analyze(self, now=None, force=False):
        now=now or datetime.now(UTC);day,period,status=phase_for(now); created=[]
        await international_market_provider.refresh(now,force=force)
        await taifex_market_provider.refresh(now,force=force)
        with BackgroundSessionLocal() as db:
            row=ensure_day(db,day,now);data=snapshot(db,day);old,new=update_summary(row,data,now,status,period)
            local=now.astimezone(TAIPEI)
            stages=[]
            if period=="PRE_MARKET": stages=[x for x in PRE_STAGES if force or local.time()>=x[0]]
            elif period=="POST_MARKET":
                stages=[x for x in POST_STAGES if local.date()==day and (force or local.time()>=x[0])]
                if local.date()>day and local.time()<time(5): stages=POST_STAGES+[(time(0),"美股與期貨","美股盤中與隔日台股影響更新")]
                elif local.date()>day and local.time()<time(6): stages=POST_STAGES+[(time(5),"盤後最終摘要","美股收盤與隔日台股初步分析")]
            for clock,cat,title in stages:
                msg=add_message(db,row,period,cat,title,data,now,importance="IMPORTANT" if cat in {"盤前最終結論","盤後最終摘要"} else "NORMAL")
                if msg: created.append(msg)
            if period=="PRE_MARKET":
                pending=list(db.scalars(select(PrePostPendingEvent).where(PrePostPendingEvent.target_trade_date==day,PrePostPendingEvent.consumed_at.is_(None))).all())
                for event in pending:
                    body=json.loads(event.payload_json);event_data={**data,"sources":body.get("sources",[]),"data_time":event.data_time,"strong":body.get("industries") or data["strong"]}
                    msg=add_message(db,row,period,body.get("category","重大國際事件"),body["title"],event_data,now,importance=body.get("importance","IMPORTANT"),event_key=event.event_key,content=body["content"])
                    event.consumed_at=now
                    if msg: created.append(msg)
            if old[1] is not None and old!=new:
                content=f"盤前判斷由{old[0]}（{old[1]}分、風險{old[2]}、持股{old[3]}）調整為{new[0]}（{new[1]}分、風險{new[2]}、持股{new[3]}）。修改原因：新一輪具來源與時間戳的資料改變綜合判斷；未取得資料未納入。"
                msg=add_message(db,row,period,"盤前判斷更新" if period=="PRE_MARKET" else "重大事件更新","盤前判斷更新" if period=="PRE_MARKET" else "盤後判斷更新",data,now,importance="IMPORTANT",event_key=f"update:{day}:{period}:{old}:{new}",content=content)
                if msg:created.append(msg)
            db.commit();ids=[m.id for m in created]
        for mid in ids:
            with SessionLocal() as db:
                msg=db.get(PrePostAnalysisMessage,mid)
                if msg:await notify(msg)
        self.state.update(status="running",lastRunAt=now.isoformat(),lastError=None,created=len(ids));return {"created":len(ids),"tradeDate":day,"status":status}
    async def archive_due(self,now):
        local=now.astimezone(TAIPEI)
        if local.time()>=time(6,0) and local.time()<time(6,2):
            day=owner_trade_date(now-timedelta(minutes=2))
            with BackgroundSessionLocal() as db:
                row=ensure_day(db,day,now)
                if not row.archived_at:
                    data=snapshot(db,day)
                    add_message(db,row,"POST_MARKET","盤後最終摘要","盤後分析時段結束與隔日初步影響",data,now,importance="IMPORTANT",event_key=f"archive:{day}")
                    row.status="FULL_DAY_COMPLETE";row.current_phase="ARCHIVED";row.archived_at=now;row.prediction_result="資料不足";row.prediction_details_json=json.dumps({"status":"資料不足","items":[],"reason":"需有完整開收盤、產業與策略結果才進行分項驗證"},ensure_ascii=False);row.updated_at=now;db.commit()
    async def _run(self):
        while not self._stopping:
            try:
                now=datetime.now(UTC)
                with BackgroundSessionLocal() as db:
                    ensure_day(db,now.astimezone(TAIPEI).date(),now);db.commit()
                await self.archive_due(now);await self.analyze(now)
            except Exception as e:self.state.update(status="error",lastError=str(e)[:500]);logger.exception("pre/post analysis cycle failed")
            await asyncio.sleep(30)

prepost_automation=PrePostAutomation()
