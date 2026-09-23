from __future__ import annotations
import asyncio,csv,io,json
from html import escape
from urllib.parse import quote
from datetime import UTC,date,datetime,timedelta
from fastapi import APIRouter,Depends,Header,HTTPException,Query
from fastapi.responses import Response,StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy import func,or_,select
from sqlalchemy.orm import Session
from ..database import SessionLocal,get_db
from ..prepost_models import PrePostAnalysisDay,PrePostAnalysisMessage,PrePostNotificationSetting,PrePostPendingEvent
from ..strong_stock_models import StrongStockMarketRegime,StrongStockRanking
from ..services.prepost_analysis import TAIPEI,add_message,ensure_day,evaluate_prediction,next_trading_day,owner_trade_date,phase_for,prepost_automation,professional_market_analysis,serialize_day,serialize_message,snapshot,trading_day,update_summary
from ..services.international_market_data import international_market_provider
from ..services.post_market_data import fetch_post_market_indexes
from ..services.taifex_market_data import taifex_market_provider

router=APIRouter(prefix="/prepost-analysis",tags=["prepost-analysis"])
def uid(x_user_id:str|None=Header(default=None,min_length=8,max_length=80)):return x_user_id or "demo-user"

def selected_day(value:date|None):
    return value or datetime.now(TAIPEI).date()

def report_snapshot(row:PrePostAnalysisDay, day:date, live_international=None, live_domestic=None):
    try:saved=json.loads(row.market_snapshot_json or "{}")
    except (TypeError,ValueError):saved={}
    if saved:return saved
    if day==datetime.now(TAIPEI).date():
        return {"international":live_international or {},"domestic":live_domestic or {},"data_completeness":None,"missing":["尚未保存完整報告快照"]}
    return {"international":{},"domestic":{},"data_completeness":None,"missing":["歷史日期沒有保存快照，不混用目前行情"]}

@router.get("/dashboard")
def dashboard(trade_date:date|None=None,db:Session=Depends(get_db)):
    day=selected_day(trade_date);row=ensure_day(db,day,datetime.now(UTC));db.commit();result=serialize_day(db,row);result["automation"]=prepost_automation.state;return result

@router.get("/messages")
def messages(trade_date:date|None=None,start_date:date|None=None,end_date:date|None=None,period:str="",importance:str="",category:str="",keyword:str="",symbol:str="",industry:str="",page:int=Query(1,ge=1),page_size:int=Query(50,ge=1,le=200),db:Session=Depends(get_db)):
    q=select(PrePostAnalysisMessage)
    if trade_date:q=q.where(PrePostAnalysisMessage.trade_date==trade_date)
    if start_date:q=q.where(PrePostAnalysisMessage.trade_date>=start_date)
    if end_date:q=q.where(PrePostAnalysisMessage.trade_date<=end_date)
    if period:q=q.where(PrePostAnalysisMessage.period==period)
    if importance:q=q.where(PrePostAnalysisMessage.importance==importance)
    if category:q=q.where(PrePostAnalysisMessage.category==category)
    if keyword:q=q.where(or_(PrePostAnalysisMessage.title.contains(keyword),PrePostAnalysisMessage.content.contains(keyword)))
    if symbol:q=q.where(PrePostAnalysisMessage.stock_impact_json.contains(symbol))
    if industry:q=q.where(PrePostAnalysisMessage.industry_impact_json.contains(industry))
    count=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows=list(db.scalars(q.order_by(PrePostAnalysisMessage.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all())
    return {"items":[serialize_message(r) for r in rows],"page":page,"pageSize":page_size,"total":count}

@router.get("/history")
def history(start_date:date|None=None,end_date:date|None=None,bias:str="",risk:str="",prediction:str="",keyword:str="",industry:str="",symbol:str="",sort:str="desc",page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),db:Session=Depends(get_db)):
    q=select(PrePostAnalysisDay)
    if start_date:q=q.where(PrePostAnalysisDay.trade_date>=start_date)
    if end_date:q=q.where(PrePostAnalysisDay.trade_date<=end_date)
    if bias:q=q.where(PrePostAnalysisDay.market_bias==bias)
    if risk:q=q.where(PrePostAnalysisDay.risk_level==risk)
    if prediction:q=q.where(PrePostAnalysisDay.prediction_result==prediction)
    if industry:q=q.where(or_(PrePostAnalysisDay.strong_industries_json.contains(industry),PrePostAnalysisDay.weak_industries_json.contains(industry)))
    if keyword:q=q.where(or_(PrePostAnalysisDay.market_bias.contains(keyword),PrePostAnalysisDay.day_trade_advice.contains(keyword),PrePostAnalysisDay.stock_advice.contains(keyword)))
    if symbol:q=q.where(PrePostAnalysisDay.trade_date.in_(select(PrePostAnalysisMessage.trade_date).where(PrePostAnalysisMessage.stock_impact_json.contains(symbol))))
    count=db.scalar(select(func.count()).select_from(q.subquery())) or 0
    order=PrePostAnalysisDay.trade_date.asc() if sort=="asc" else PrePostAnalysisDay.trade_date.desc()
    rows=list(db.scalars(q.order_by(order).offset((page-1)*page_size).limit(page_size)).all())
    items=[]
    for r in rows:
        pre=db.scalar(select(func.count()).select_from(PrePostAnalysisMessage).where(PrePostAnalysisMessage.trade_date==r.trade_date,PrePostAnalysisMessage.period=="PRE_MARKET")) or 0
        post=db.scalar(select(func.count()).select_from(PrePostAnalysisMessage).where(PrePostAnalysisMessage.trade_date==r.trade_date,PrePostAnalysisMessage.period=="POST_MARKET")) or 0
        items.append({"tradeDate":r.trade_date,"weekday":r.trade_date.strftime("%A"),"isTradingDay":r.is_trading_day,"status":r.status,"marketBias":r.market_bias,"marketScore":r.market_score,"riskLevel":r.risk_level,"predictionResult":r.prediction_result,"preMarketMessageCount":pre,"postMarketMessageCount":post,"lastUpdatedAt":r.updated_at})
    return {"items":items,"total":count,"page":page,"pageSize":page_size}

@router.get("/calendar")
def calendar(start_date:date,end_date:date,db:Session=Depends(get_db)):
    rows={r.trade_date:r for r in db.scalars(select(PrePostAnalysisDay).where(PrePostAnalysisDay.trade_date.between(start_date,end_date))).all()};items=[];day=start_date
    while day<=end_date:
        r=rows.get(day);items.append({"date":day,"isTradingDay":trading_day(day),"status":r.status if r else ("NOT_STARTED" if trading_day(day) else "MARKET_CLOSED"),"majorEvent":bool(r and db.scalar(select(func.count()).select_from(PrePostAnalysisMessage).where(PrePostAnalysisMessage.trade_date==day,PrePostAnalysisMessage.importance.in_(["IMPORTANT","EMERGENCY"]))))});day+=timedelta(days=1)
    return {"items":items}

@router.get("/report")
async def post_market_report(trade_date:date|None=None,format:str="pdf",db:Session=Depends(get_db)):
    day=selected_day(trade_date);row=ensure_day(db,day,datetime.now(UTC));db.commit()
    local_now=datetime.now(TAIPEI)
    if day==local_now.date() and local_now.time()<datetime.strptime("15:10","%H:%M").time():
        raise HTTPException(409,"今日盤後報告將於15:10後開放，以等待三大法人與盤後資料")
    messages=list(db.scalars(select(PrePostAnalysisMessage).where(
        PrePostAnalysisMessage.trade_date==day,PrePostAnalysisMessage.period=="POST_MARKET",
    ).order_by(PrePostAnalysisMessage.created_at)).all())
    def text(value):return escape(str(value if value not in (None,"") else "資料尚未取得"))
    def values(raw):
        try:return json.loads(raw or "[]")
        except (TypeError,ValueError):return []
    ranking_rows=list(db.scalars(select(StrongStockRanking).where(StrongStockRanking.trade_date<=day).order_by(StrongStockRanking.trade_date,StrongStockRanking.rank)).all())
    rankings_by_day={}
    for ranking in ranking_rows:rankings_by_day.setdefault(ranking.trade_date,[]).append(ranking)
    tracking={}
    for ranking_day,day_rankings in rankings_by_day.items():
        current={item.symbol:item for item in day_rankings}
        retained={}
        for symbol,item in tracking.items():
            latest=current.get(symbol)
            if latest and float(latest.close_price)>=float(item["support"]):
                retained[symbol]={"row":latest,"first":item["first"],"support":latest.stop_price or latest.pullback_price or latest.close_price,"continued":True}
        tracking=retained
        for latest in day_rankings[:10]:
            previous=tracking.get(latest.symbol)
            tracking[latest.symbol]={"row":latest,"first":previous["first"] if previous else ranking_day,"support":latest.stop_price or latest.pullback_price or latest.close_price,"continued":bool(previous)}
    await international_market_provider.refresh(force=True)
    fetched_market=await fetch_post_market_indexes(day)
    regime=db.scalar(select(StrongStockMarketRegime).where(StrongStockMarketRegime.trade_date==day))
    market={}
    if regime:
        try:market=json.loads(regime.source_snapshot_json or "{}")
        except (TypeError,ValueError):market={}
    taiex=fetched_market["indexes"].get("taiex",{});otc=fetched_market["indexes"].get("otc",{})
    if market.get("taiex_close") is None:market["taiex_close"]=taiex.get("close")
    if market.get("taiex_return_1d") is None:market["taiex_return_1d"]=taiex.get("return_1d")
    if market.get("taiex_above_ma60") is None:market["taiex_above_ma60"]=taiex.get("above_ma60")
    if market.get("otc_close") is None:market["otc_close"]=otc.get("close")
    if market.get("otc_return_1d") is None:market["otc_return_1d"]=otc.get("return_1d")
    if market.get("otc_relative_strength") is None and taiex.get("return_1d") is not None and otc.get("return_1d") is not None:market["otc_relative_strength"]=otc["return_1d"]>taiex["return_1d"]
    if market.get("volume_ratio_20d") is None:market["volume_ratio_20d"]=taiex.get("volume_ratio_20d")
    stock_rows="".join(f"<tr><td><b>{text(item['row'].symbol)}</b> {text(item['row'].name)}</td><td>{text(item['row'].close_price)}</td><td>{text(item['row'].breakout_price or item['row'].entry_high)}</td><td>{text(item['support'])}</td><td><b class='score'>{text(item['row'].total_score)}</b></td><td><span class='status {'keep' if item['continued'] else 'new'}'>{'續留' if item['continued'] else '今日新增'}</span><br><small>自 {item['first']}</small></td></tr>" for item in sorted(tracking.values(),key=lambda value:value['row'].rank)) or "<tr><td colspan='6'>當日強勢股排名資料尚未取得</td></tr>"
    strong_values=values(row.strong_industries_json);weak_values=values(row.weak_industries_json)
    strong="、".join(strong_values) or "資料尚未取得";weak="、".join(weak_values) or "資料尚未取得"
    def post_industry_view(name):
        if any(word in name for word in ("半導體","電子","AI","電腦","光電","通訊")):return ("AI資本支出、先進製程／封裝、高速傳輸","費半、台積電ADR及族群量價延續","殖利率／美元上升、評價修正")
        if "金融" in name:return ("利差、股利與市場成交量","相對大盤轉強且量能增加","信用成本與債券評價波動")
        if any(word in name for word in ("航運","運輸")):return ("運價、旺季與供需變化","報價及營收同步改善","油價、運力供給與地緣風險")
        return ("訂單、報價、庫存與毛利率改善","營收與領漲股量價同步","需求落空與族群跌破支撐")
    post_industry_rows=[["產業","未來催化劑","續強確認","主要風險"]]+[[name,*post_industry_view(name)] for name in strong_values[:6]]
    fetched_support=[f"{label} {quote['support']}" for label,quote in (("加權",taiex),("櫃買",otc)) if quote.get("support") is not None]
    fetched_resistance=[f"{label} {quote['resistance']}" for label,quote in (("加權",taiex),("櫃買",otc)) if quote.get("resistance") is not None]
    support_values=values(row.support_json) or fetched_support
    resistance_values=values(row.resistance_json) or fetched_resistance
    support="、".join(support_values) or "資料尚未取得";resistance="、".join(resistance_values) or "資料尚未取得"
    taiex_close=market.get("taiex_close");taiex_return=market.get("taiex_return_1d");otc_close=market.get("otc_close");otc_return=market.get("otc_return_1d")
    try:pre_prediction=json.loads(row.pre_market_prediction_json or "{}")
    except (TypeError,ValueError):pre_prediction={}
    prediction_check=evaluate_prediction(pre_prediction,taiex_return,otc_return)
    row.prediction_result=prediction_check["status"];row.prediction_details_json=json.dumps(prediction_check,ensure_ascii=False);db.commit()
    taiex_structure="站上季線，中期結構偏多" if market.get("taiex_above_ma60") is True else "跌破季線，中期結構偏弱" if market.get("taiex_above_ma60") is False else "均線資料尚未取得"
    otc_structure="櫃買相對大盤強勢" if market.get("otc_relative_strength") is True else "櫃買未呈現相對強勢" if market.get("otc_relative_strength") is False else "相對強弱資料尚未取得"
    evidence=report_snapshot(row,day,international_market_provider.snapshot().get("quotes",{}))
    international=evidence.get("international",{})
    try:score_breakdown=json.loads(row.score_breakdown_json or "{}")
    except (TypeError,ValueError):score_breakdown={}
    completeness=evidence.get("data_completeness")
    report_data={"score":row.market_score,"bias":row.market_bias,"risk":row.risk_level,"position":row.recommended_position,"strong":values(row.strong_industries_json),"weak":values(row.weak_industries_json),"international":international}
    market_analysis=professional_market_analysis(report_data,"盤後",market)
    international_rows="".join(f"<tr><td>{text(quote['name'])}</td><td>{text(quote['price'])}</td><td class='{('up' if quote['changePct']>0 else 'down' if quote['changePct']<0 else 'neutral')}'>{quote['changePct']:+.2f}%</td><td>{text(datetime.fromisoformat(quote['dataTime']).astimezone(TAIPEI).strftime('%m/%d %H:%M'))}</td></tr>" for quote in international.values()) or "<tr><td colspan='4'>國際行情資料尚未取得</td></tr>"
    breadth=market.get("advance_ratio");limit_up=market.get("limit_up_count");limit_down=market.get("limit_down_count");volume_ratio=market.get("volume_ratio_20d")
    chart_keys=("sp500","nasdaq","sox","tsm","nvda","amd","nq_future","dxy","us10y","oil","gold")
    international_chart_rows=[(international[key]["name"],float(international[key]["changePct"])) for key in chart_keys if key in international and international[key].get("changePct") is not None]
    local_chart_rows=[(label,float(value)) for label,value in (("加權指數",taiex_return),("櫃買指數",otc_return)) if value is not None]
    comparable=[value for _,value in international_chart_rows]
    intl_average=sum(comparable)/len(comparable) if comparable else None
    intl_positive=sum(value>0 for value in comparable);intl_negative=sum(value<0 for value in comparable)
    local_gap=(float(otc_return)-float(taiex_return)) if otc_return is not None and taiex_return is not None else None
    positive_triggers=["加權指數守住重要支撐","櫃買維持相對強勢","強勢產業量價結構未破壞"]
    negative_triggers=["加權或櫃買跌破追蹤支撐","費半、台積電ADR或NASDAQ期貨同步轉弱","美元、殖利率或油價急升"]
    next_plan=f"明日以{row.market_bias}劇本準備，建議持股{row.recommended_position}。開盤先確認大盤與櫃買是否延續今日結構；符合多方條件才分批增加部位，任一風險條件觸發即停止追價並降低持股。"
    if format=="pdf":
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.graphics.shapes import Drawing,Line,Rect,String
        from reportlab.platypus import PageBreak,Paragraph,SimpleDocTemplate,Spacer,Table,TableStyle
        pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"))
        buffer=io.BytesIO();doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=10*mm,leftMargin=10*mm,topMargin=10*mm,bottomMargin=10*mm,title=f"每日盤後市場報告 {day}")
        styles=getSampleStyleSheet();font="MSung-Light"
        title_style=ParagraphStyle("FaxTitle",parent=styles["Title"],fontName=font,fontSize=22,textColor=colors.white,alignment=TA_CENTER,leading=28)
        section_style=ParagraphStyle("Section",parent=styles["Heading2"],fontName=font,fontSize=13,textColor=colors.white,backColor=colors.HexColor("#76262B"),borderPadding=6,spaceBefore=10,spaceAfter=5)
        body_style=ParagraphStyle("Body",parent=styles["BodyText"],fontName=font,fontSize=9.5,leading=14,textColor=colors.HexColor("#30251F"))
        analysis_style=ParagraphStyle("Analysis",parent=body_style,fontSize=11.5,leading=18,textColor=colors.HexColor("#241A17"),backColor=colors.HexColor("#FFF9EF"),borderColor=colors.HexColor("#D8C39A"),borderWidth=.7,borderPadding=10,spaceAfter=5)
        important_style=ParagraphStyle("Important",parent=body_style,fontSize=10.5,leading=16,textColor=colors.HexColor("#C62828"))
        small_style=ParagraphStyle("Small",parent=body_style,fontSize=8,leading=11)
        def p(value,style=body_style):return Paragraph(text(value),style)
        def styled_table(rows,widths=None,header=True):
            table=Table(rows,colWidths=widths,repeatRows=1 if header else 0,hAlign="LEFT")
            commands=[("FONTNAME",(0,0),(-1,-1),font),("FONTSIZE",(0,0),(-1,-1),8),("LEADING",(0,0),(-1,-1),11),("GRID",(0,0),(-1,-1),.45,colors.HexColor("#CDBEA9")),("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]
            if header:commands += [("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EFE4D2")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#5B2426"))]
            for index in range(2 if header else 1,len(rows),2):commands.append(("BACKGROUND",(0,index),(-1,index),colors.HexColor("#FCFAF6")))
            table.setStyle(TableStyle(commands));return table
        def change_chart(items,caption):
            if not items:return p("目前沒有可繪製的漲跌資料。",small_style)
            width=190*mm;height=(17+len(items)*7)*mm;drawing=Drawing(width,height);axis=95*mm;span=78*mm;top=height-12*mm
            scale=max(1,max(abs(value) for _,value in items));drawing.add(String(3*mm,height-5*mm,caption,fontName=font,fontSize=9,fillColor=colors.HexColor("#5B2426")))
            drawing.add(Line(axis,5*mm,axis,top+2*mm,strokeColor=colors.HexColor("#88766B"),strokeWidth=.7))
            for index,(label,value) in enumerate(items):
                y=top-index*7*mm;bar=min(span,abs(value)/scale*span);fill=colors.HexColor("#C62828") if value>0 else colors.HexColor("#087F5B") if value<0 else colors.HexColor("#8A9692")
                drawing.add(String(3*mm,y,label[:18],fontName=font,fontSize=7.5,fillColor=colors.HexColor("#30251F")))
                drawing.add(Rect(axis if value>=0 else axis-bar,y-1.2*mm,bar,3.5*mm,fillColor=fill,strokeColor=None))
                drawing.add(String(axis+bar+2*mm if value>=0 else max(55*mm,axis-bar-12*mm),y,f"{value:+.2f}%",fontName=font,fontSize=7,fillColor=fill))
            return drawing
        story=[]
        masthead=Table([[p("每日盤後市場報告",title_style)],[p(f"劉宗元｜交易日期：{day}　｜　資料截至：{text(row.data_as_of.astimezone(TAIPEI).strftime('%Y/%m/%d %H:%M') if row.data_as_of else None)}",ParagraphStyle("Meta",parent=small_style,textColor=colors.HexColor("#F8E8C7"),alignment=TA_CENTER))]],colWidths=[190*mm])
        masthead.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#76262B")),("BOX",(0,0),(-1,-1),2,colors.HexColor("#D5AE62")),("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]));story += [masthead,Spacer(1,5*mm)]
        decision=Table([[p("今日核心結論",important_style),p(f"<b>{text(row.market_bias)}</b>　AI {text(row.market_score)} 分　風險 {text(row.risk_level)}　建議持股 {text(row.recommended_position)}",important_style)]],colWidths=[32*mm,158*mm]);decision.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#FFF7E8")),("BOX",(0,0),(-1,-1),1.5,colors.HexColor("#C62828")),("FONTNAME",(0,0),(-1,-1),font),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("PADDING",(0,0),(-1,-1),8)]));story += [decision]
        validation_rows=[["驗證項目","盤前預測","實際結果","判定"]]+[[item["name"],item["predicted"],item["actual"],item["result"]] for item in prediction_check.get("items",[])]
        score_names={"localMomentum":"台股動能","internationalRisk":"國際風險偏好","technologyChain":"半導體科技鏈","futures":"國際期貨","dataCompleteness":"資料完整度"}
        score_rows=[["評分構成","分數"]]+[[score_names.get(key,key),text(value)] for key,value in score_breakdown.items()]
        story += [p("AI 分數與資料完整度",section_style),styled_table(score_rows if len(score_rows)>1 else [["評分拆解尚未保存","—"]],[95*mm,95*mm]),p(f"本報告資料完整度：{text(f'{completeness}%' if completeness is not None else None)}。缺失資料不納入分數。",small_style),p("盤前預測驗證",section_style),styled_table(validation_rows if len(validation_rows)>1 else [["資料不足","—","—",prediction_check["reason"]]],[35*mm,45*mm,70*mm,40*mm])]
        story += [p("收盤資訊",section_style),styled_table([["加權收盤","加權漲跌","櫃買收盤","櫃買漲跌"],[text(taiex_close),text(f'{taiex_return:+.2f}%' if taiex_return is not None else None),text(otc_close),text(f'{otc_return:+.2f}%' if otc_return is not None else None)],["上漲家數比","漲停／跌停","量能比","市場結構"],[text(f'{breadth*100:.1f}%' if breadth is not None and breadth<=1 else breadth),f"{text(limit_up)}／{text(limit_down)}",text(volume_ratio),p(f"{text(taiex_structure)}；{text(otc_structure)}",small_style)]],[35*mm,35*mm,35*mm,85*mm])]
        story += [p("操作策略",section_style),styled_table([["項目","建議"],["市場部位",p(f"建議持股 {text(row.recommended_position)}；支撐 {text(support)}；壓力 {text(resistance)}",small_style)],["當沖策略",p(text(row.day_trade_advice),small_style)],["現股策略",p(text(row.stock_advice),small_style)]],[30*mm,160*mm])]
        story += [p("盤勢與國際局勢分析",section_style),*[p(text(paragraph),analysis_style) for paragraph in market_analysis],p("加權與櫃買相對強弱圖",section_style),change_chart(local_chart_rows,"中央為 0 軸；紅色上漲、綠色下跌"),p("全球資產漲跌比較圖",section_style),change_chart(international_chart_rows,"依同一組資料的最大絕對漲跌幅縮放"),p("量化摘要",section_style),styled_table([["指標","數值","判讀"],["國際資產樣本",f"{len(comparable)} 項",f"上漲 {intl_positive}／下跌 {intl_negative}"],["國際平均變動",text(f'{intl_average:+.2f}%' if intl_average is not None else None),"只代表已取得樣本，不等同全球市場報酬"],["櫃買－加權",text(f'{local_gap:+.2f} 個百分點' if local_gap is not None else None),"正值代表中小型股相對強；負值代表權值股相對強"],["量能／20日均量",text(f'{volume_ratio:.2f} 倍' if volume_ratio is not None else None),"大於 1 為放量，小於 1 為量縮"]],[45*mm,45*mm,100*mm]),p("國際市場重點",section_style)]
        intl_pdf=[["市場／資產","最新值","漲跌幅","資料時間"]]+[[p(text(q["name"]),small_style),text(q["price"]),f"{q['changePct']:+.2f}%",datetime.fromisoformat(q["dataTime"]).astimezone(TAIPEI).strftime("%m/%d %H:%M")] for q in international.values()]
        event_pdf=[["時間／類別","事件與分析","影響","操作含義"]]+[[p(f"{m.created_at.astimezone(TAIPEI):%H:%M}<br/>{text(m.category)}",small_style),p(f"<b>{text(m.title)}</b><br/>{text(m.content)}",small_style),p(text(m.market_impact),small_style),p(text(m.operation_advice),small_style)] for m in messages[-10:]]
        story += [styled_table(intl_pdf or [["資料尚未取得"]],[70*mm,35*mm,35*mm,50*mm]),p("重大事件與盤後更新時間軸",section_style),styled_table(event_pdf if len(event_pdf)>1 else [["暫無事件","資料尚未取得","—","—"]],[30*mm,82*mm,24*mm,54*mm]),p("產業強弱",section_style),styled_table([["相對強勢",p(text(strong),small_style)],["相對弱勢",p(text(weak),small_style)]],[30*mm,160*mm],False),p("強勢產業未來展望",section_style),styled_table([[p(text(cell),small_style) for cell in industry_row] for industry_row in post_industry_rows] if len(post_industry_rows)>1 else [["產業展望資料尚未取得","—","—","—"]],[34*mm,56*mm,52*mm,48*mm])]
        story += [p("明日操作計畫",section_style),p(text(next_plan),important_style),styled_table([["偏多確認條件","降低部位條件"],[p("<br/>".join(f"• {text(item)}" for item in positive_triggers),small_style),p("<br/>".join(f"• {text(item)}" for item in negative_triggers),important_style)]],[95*mm,95*mm])]
        tracking_pdf=[["股票","收盤","壓力／突破價","追蹤支撐","分數","狀態"]]+[[p(f"<b>{text(item['row'].symbol)}</b> {text(item['row'].name)}",small_style),text(item['row'].close_price),text(item['row'].breakout_price or item['row'].entry_high),text(item['support']),text(item['row'].total_score),p(f"{'續留' if item['continued'] else '今日新增'}<br/>自 {item['first']}",small_style)] for item in sorted(tracking.values(),key=lambda value:value['row'].rank)]
        story += [p("持股追蹤",section_style),styled_table(tracking_pdf or [["資料尚未取得"]],[42*mm,25*mm,34*mm,30*mm,22*mm,37*mm]),Spacer(1,4*mm),p("本報告由具來源時間的資料自動產生。內容為市場分析，不保證獲利，請自行判斷並做好風險控制。",small_style)]
        doc.build(story);content=buffer.getvalue()
        chinese_filename=f"劉宗元大神盤後報告＋{day}.pdf"
        disposition=f"attachment; filename=post-market-report-{day}.pdf; filename*=UTF-8''{quote(chinese_filename)}"
        return Response(content,media_type="application/pdf",headers={"Content-Disposition":disposition})
    if format!="html":raise HTTPException(422,"format 必須是 pdf 或 html")
    def html_chart(items):
        scale=max([1]+[abs(value) for _,value in items])
        return "".join(f"<div class='bar-row'><span>{text(label)}</span><div class='bar-track'><i class='zero'></i><b class='{'upbar' if value>=0 else 'downbar'}' style='width:{max(2,abs(value)/scale*50):.1f}%'></b></div><strong class='{'up' if value>0 else 'down' if value<0 else 'neutral'}'>{value:+.2f}%</strong></div>" for label,value in items) or "<p>目前沒有可繪製的漲跌資料。</p>"
    local_chart_html=html_chart(local_chart_rows);international_chart_html=html_chart(international_chart_rows)
    html=f"""<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><title>{day} 劉宗元大神盤後稿</title><style>
    @page{{size:A4;margin:11mm}}*{{box-sizing:border-box;-webkit-print-color-adjust:exact;print-color-adjust:exact}}body{{font-family:'Noto Serif TC','Microsoft JhengHei',serif;color:#2b211d;margin:0;font-size:12px;background:#f6f1e8;line-height:1.55}}.sheet{{background:#fff;max-width:900px;margin:auto;padding:18px 22px;box-shadow:0 8px 35px #4a241733}}.masthead{{margin:-18px -22px 14px;padding:20px 24px 14px;background:linear-gradient(135deg,#541b20,#8e2e2e 65%,#b78938);color:#fff;border-bottom:5px solid #d5ae62}}h1{{text-align:center;letter-spacing:.16em;margin:0;font-size:25px}}.meta{{text-align:center;margin:5px 0 0;color:#f8e8c7}}h2{{font-size:15px;margin:18px 0 7px;padding:6px 10px;color:#fff;background:linear-gradient(90deg,#6f2228,#9e3b36);border-left:6px solid #d9b35f;border-radius:3px}}table{{border-collapse:separate;border-spacing:0;width:100%;table-layout:fixed;border:1px solid #ccbda8;border-radius:6px;overflow:hidden;background:#fff}}th,td{{border-right:1px solid #d8cec0;border-bottom:1px solid #d8cec0;padding:7px;vertical-align:top;word-break:break-word}}tr:last-child td{{border-bottom:0}}th:last-child,td:last-child{{border-right:0}}th{{background:#efe4d2;color:#5b2426;font-weight:800}}tr:nth-child(even) td{{background:#fcfaf6}}.summary td{{text-align:center;font-size:14px;font-weight:700}}.decision{{border:2px solid #b78a39;border-left:8px solid #7a252a;background:linear-gradient(90deg,#fff7e8,#fff);padding:12px 14px;margin:10px 0;border-radius:7px;box-shadow:0 3px 10px #5b342214}}.decision strong{{font-size:20px;color:#a1222d}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.grid>div{{background:#fbf6ed;border:1px solid #d9c8aa;border-radius:6px;padding:8px}}ul{{margin:5px 0;padding-left:20px}}.up{{color:#c62828;font-weight:800}}.down{{color:#087f5b;font-weight:800}}.neutral{{color:#64748b;font-weight:800}}.score{{display:inline-block;min-width:34px;text-align:center;padding:2px 6px;border-radius:10px;background:#f5e6c8;color:#78262b}}.status{{display:inline-block;padding:2px 7px;border-radius:10px;color:#fff;font-size:11px;font-weight:800}}.status.new{{background:#b7791f}}.status.keep{{background:#256b52}}small{{color:#76685d}}.note{{margin-top:18px;border-top:2px solid #cda95e;padding-top:8px;color:#6d6258}}button{{position:fixed;z-index:2;right:18px;top:18px;padding:10px 16px;border:0;border-radius:20px;background:#76262b;color:#fff;box-shadow:0 3px 12px #0003;cursor:pointer}}@media print{{body{{background:#fff}}.sheet{{box-shadow:none;padding:0}}button{{display:none}}}}
    .important{{color:#c62828;font-weight:800}}.bar-row{{display:grid;grid-template-columns:145px 1fr 62px;gap:8px;align-items:center;margin:7px 0}}.bar-track{{height:10px;background:#eee7dc;border-radius:8px;position:relative;overflow:hidden}}.bar-track .zero{{position:absolute;left:50%;width:1px;height:100%;background:#75695f;z-index:2}}.bar-track b{{position:absolute;height:100%}}.bar-track .upbar{{left:50%;background:#c62828}}.bar-track .downbar{{right:50%;background:#087f5b}}.analysis-paragraph{{padding:9px 11px;background:#fff9ef;border-left:4px solid #cda95e;border-radius:3px}}
    </style></head><body><button onclick='window.print()'>列印／另存 PDF</button><main class='sheet'><header class='masthead'><h1>每日盤後市場報告</h1><p class='meta'>劉宗元｜交易日期：{day}　｜　資料截至：{text(row.data_as_of.astimezone(TAIPEI).strftime('%Y/%m/%d %H:%M') if row.data_as_of else None)}</p></header>
    <div class='decision'>今日核心結論：<strong>{text(row.market_bias)}</strong>　AI {text(row.market_score)} 分　風險 {text(row.risk_level)}　建議持股 {text(row.recommended_position)}</div>
    <h2>AI 分數與資料完整度</h2><table><tr><th>台股動能</th><th>國際風險偏好</th><th>半導體科技鏈</th><th>國際期貨</th><th>資料完整度</th></tr><tr><td>{text(score_breakdown.get('localMomentum'))}</td><td>{text(score_breakdown.get('internationalRisk'))}</td><td>{text(score_breakdown.get('technologyChain'))}</td><td>{text(score_breakdown.get('futures'))}</td><td>{text(f'{completeness}%' if completeness is not None else None)}</td></tr></table>
    <h2>盤前預測驗證</h2><table><tr><th>驗證項目</th><th>盤前預測</th><th>實際結果</th><th>判定</th></tr>{''.join(f'<tr><td>{text(item["name"])}</td><td>{text(item["predicted"])}</td><td>{text(item["actual"])}</td><td>{text(item["result"])}</td></tr>' for item in prediction_check.get('items',[])) or f'<tr><td colspan="4">{text(prediction_check["reason"])}</td></tr>'}</table>
    <h2>收盤資訊</h2><table class='summary'><tr><th>加權指數收盤</th><th>加權漲跌幅</th><th>櫃買收盤</th><th>櫃買漲跌幅</th></tr><tr><td>{text(taiex_close)}</td><td>{text(f'{taiex_return:+.2f}%' if taiex_return is not None else None)}</td><td>{text(otc_close)}</td><td>{text(f'{otc_return:+.2f}%' if otc_return is not None else None)}</td></tr><tr><th>上漲家數比</th><th>漲停／跌停</th><th>量能／20日均量</th><th>市場結構</th></tr><tr><td>{text(f'{breadth*100:.1f}%' if breadth is not None and breadth<=1 else breadth)}</td><td>{text(limit_up)}／{text(limit_down)}</td><td>{text(volume_ratio)}</td><td>{text(taiex_structure)}；{text(otc_structure)}</td></tr></table>
    <h2>操作策略</h2><table class='important'><tr><th>市場部位</th><td>建議持股 {text(row.recommended_position)}；支撐 {text(support)}；壓力 {text(resistance)}</td></tr><tr><th>當沖策略</th><td>{text(row.day_trade_advice)}</td></tr><tr><th>現股策略</th><td>{text(row.stock_advice)}</td></tr></table>
    <h2>盤勢與國際局勢分析</h2>{''.join(f'<p class="analysis-paragraph">{text(paragraph)}</p>' for paragraph in market_analysis)}
    <h2>加權與櫃買相對強弱圖</h2>{local_chart_html}
    <h2>全球資產漲跌比較圖</h2>{international_chart_html}
    <h2>量化摘要</h2><table><tr><th>國際樣本</th><th>上漲／下跌</th><th>平均變動</th><th>櫃買－加權</th><th>量能比</th></tr><tr><td>{len(comparable)} 項</td><td>{intl_positive}／{intl_negative}</td><td>{text(f'{intl_average:+.2f}%' if intl_average is not None else None)}</td><td>{text(f'{local_gap:+.2f} 個百分點' if local_gap is not None else None)}</td><td>{text(f'{volume_ratio:.2f} 倍' if volume_ratio is not None else None)}</td></tr></table>
    <h2>國際市場重點</h2><table><tr><th>市場／資產</th><th>最新值</th><th>漲跌幅</th><th>資料時間</th></tr>{international_rows}</table>
    <h2>重大事件與盤後更新時間軸</h2><table><tr><th>時間／類別</th><th>事件與分析</th><th>影響</th><th>操作含義</th></tr>{''.join(f'<tr><td>{m.created_at.astimezone(TAIPEI):%H:%M}<br>{text(m.category)}</td><td><b>{text(m.title)}</b><br>{text(m.content)}</td><td>{text(m.market_impact)}</td><td>{text(m.operation_advice)}</td></tr>' for m in messages[-10:]) or '<tr><td colspan="4">暫無盤後事件資料</td></tr>'}</table>
    <h2>產業強弱</h2><table><tr><th>相對強勢</th><td>{text(strong)}</td></tr><tr><th>相對弱勢</th><td>{text(weak)}</td></tr></table>
    <h2>強勢產業未來展望</h2><table><tr><th>產業</th><th>未來催化劑</th><th>續強確認</th><th>主要風險</th></tr>{''.join(f'<tr><td>{text(item[0])}</td><td>{text(item[1])}</td><td>{text(item[2])}</td><td>{text(item[3])}</td></tr>' for item in post_industry_rows[1:]) or '<tr><td colspan="4">產業展望資料尚未取得</td></tr>'}</table>
    <h2>明日操作計畫</h2><p class='important'>{text(next_plan)}</p><div class='grid'><div><b>偏多確認條件</b><ul>{''.join(f'<li>{text(item)}</li>' for item in positive_triggers)}</ul></div><div class='important'><b>降低部位條件</b><ul>{''.join(f'<li>{text(item)}</li>' for item in negative_triggers)}</ul></div></div>
    <h2>持股追蹤</h2><table><tr><th>股票</th><th>收盤</th><th>壓力／突破價</th><th>追蹤支撐</th><th>強度分數</th><th>狀態</th></tr>{stock_rows}</table>
    <p class='note'>本報告由系統保存且具來源時間的資料自動產生；缺少來源的數值不以推估值代替。內容為市場分析，不保證獲利，投資人仍需自行判斷並做好風險控制。</p></main></body></html>"""
    chinese_filename=f"劉宗元大神盤後報告＋{day}.html"
    disposition=f"attachment; filename=post-market-report-{day}.html; filename*=UTF-8''{quote(chinese_filename)}"
    return Response(html,media_type="text/html; charset=utf-8",headers={"Content-Disposition":disposition})

@router.get("/pre-report")
async def pre_market_report(trade_date:date|None=None,db:Session=Depends(get_db)):
    day=selected_day(trade_date);row=ensure_day(db,day,datetime.now(UTC));db.commit();local_now=datetime.now(TAIPEI)
    if day==local_now.date() and local_now.time()<datetime.strptime("08:00","%H:%M").time():
        raise HTTPException(409,"今日盤前報告將於08:00完成後開放")
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle,getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.graphics.shapes import Drawing,Line,Rect,String
    from reportlab.platypus import PageBreak,Paragraph,SimpleDocTemplate,Spacer,Table,TableStyle
    pdfmetrics.registerFont(UnicodeCIDFont("MSung-Light"));font="MSung-Light";styles=getSampleStyleSheet()
    body=ParagraphStyle("PreBody",parent=styles["BodyText"],fontName=font,fontSize=10.5,leading=16,textColor=colors.HexColor("#24201C"))
    small=ParagraphStyle("PreSmall",parent=body,fontSize=8.5,leading=12);heading=ParagraphStyle("PreHeading",parent=styles["Heading2"],fontName=font,fontSize=13,textColor=colors.white,backColor=colors.HexColor("#234E52"),borderPadding=6,spaceBefore=10,spaceAfter=5)
    title=ParagraphStyle("PreTitle",parent=styles["Title"],fontName=font,fontSize=22,textColor=colors.white,alignment=TA_CENTER,leading=28)
    def p(value,style=body):return Paragraph(escape(str(value if value not in (None,"") else "資料尚未取得")),style)
    def table(rows,widths,header=True):
        result=Table(rows,colWidths=widths,repeatRows=1 if header else 0,hAlign="LEFT")
        commands=[("FONTNAME",(0,0),(-1,-1),font),("FONTSIZE",(0,0),(-1,-1),8.5),("GRID",(0,0),(-1,-1),.45,colors.HexColor("#B8C9C3")),("VALIGN",(0,0),(-1,-1),"TOP"),("PADDING",(0,0),(-1,-1),5)]
        if header:commands += [("BACKGROUND",(0,0),(-1,0),colors.HexColor("#DCEBE5")),("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#173F43"))]
        for index in range(2 if header else 1,len(rows),2):commands.append(("BACKGROUND",(0,index),(-1,index),colors.HexColor("#F6FAF8")))
        result.setStyle(TableStyle(commands));return result
    def change_chart(items,title_text):
        rows=[(str(label),float(change)) for label,change in items if change is not None]
        if not rows:return p("目前沒有可繪製的漲跌資料。",small)
        width=190*mm;height=(18+len(rows)*8)*mm;drawing=Drawing(width,height)
        label_x=3*mm;axis_x=53*mm;chart_width=122*mm;top=height-13*mm
        scale=max(1.0,max(abs(change) for _,change in rows))
        drawing.add(String(label_x,height-6*mm,title_text,fontName=font,fontSize=10,fillColor=colors.HexColor("#173F43")))
        drawing.add(Line(axis_x,6*mm,axis_x,top+3*mm,strokeColor=colors.HexColor("#708A82"),strokeWidth=.7))
        for index,(label,change) in enumerate(rows):
            y=top-index*8*mm
            drawing.add(String(label_x,y,label[:14],fontName=font,fontSize=8,fillColor=colors.HexColor("#24201C")))
            bar_width=min(chart_width,abs(change)/scale*chart_width)
            fill=colors.HexColor("#C62828") if change>0 else colors.HexColor("#087F5B") if change<0 else colors.HexColor("#8A9692")
            if change>=0:drawing.add(Rect(axis_x,y-1.5*mm,bar_width,4*mm,fillColor=fill,strokeColor=None))
            else:drawing.add(Rect(axis_x-bar_width,y-1.5*mm,bar_width,4*mm,fillColor=fill,strokeColor=None))
            value_x=min(width-14*mm,max(1*mm,axis_x+(bar_width if change>=0 else -bar_width)+(2*mm if change>=0 else -12*mm)))
            drawing.add(String(value_x,y,f"{change:+.2f}%",fontName=font,fontSize=7.5,fillColor=fill))
        return drawing
    def score_gauge(score_value,position_text,risk_text):
        score_number=max(0.0,min(100.0,float(score_value or 0)))
        width=190*mm;height=25*mm;drawing=Drawing(width,height);x=5*mm;y=10*mm;bar_width=130*mm
        drawing.add(String(x,height-5*mm,"AI 盤勢分數與建議曝險",fontName=font,fontSize=10,fillColor=colors.HexColor("#173F43")))
        drawing.add(Rect(x,y,bar_width*.45,5*mm,fillColor=colors.HexColor("#BFE3D2"),strokeColor=None))
        drawing.add(Rect(x+bar_width*.45,y,bar_width*.15,5*mm,fillColor=colors.HexColor("#EFE1A8"),strokeColor=None))
        drawing.add(Rect(x+bar_width*.60,y,bar_width*.40,5*mm,fillColor=colors.HexColor("#EDB7B4"),strokeColor=None))
        marker_x=x+bar_width*score_number/100
        drawing.add(Line(marker_x,y-2*mm,marker_x,y+7*mm,strokeColor=colors.HexColor("#172B2D"),strokeWidth=2))
        drawing.add(String(x+bar_width+5*mm,y+1*mm,f"{score_number:.0f} 分",fontName=font,fontSize=11,fillColor=colors.HexColor("#76262B")))
        drawing.add(String(x,y-5*mm,f"風險：{risk_text}　建議持股：{position_text}",fontName=font,fontSize=8.5,fillColor=colors.HexColor("#4C5F59")))
        return drawing
    await international_market_provider.refresh(force=True)
    await taifex_market_provider.refresh(force=True)
    evidence=report_snapshot(row,day,international_market_provider.snapshot().get("quotes",{}),taifex_market_provider.snapshot().get("data",{}))
    international=evidence.get("international",{});domestic=evidence.get("domestic",{})
    try:pre_score_breakdown=json.loads(row.score_breakdown_json or "{}")
    except (TypeError,ValueError):pre_score_breakdown={}
    pre_completeness=evidence.get("data_completeness")
    def quote_change(*keys):
        for key in keys:
            quote_row=international.get(key)
            if quote_row and quote_row.get("changePct") is not None:return float(quote_row["changePct"])
        return None
    def tone(change):
        if change is None:return "資料尚未取得"
        if change>=1:return "明顯偏多"
        if change>0:return "偏多"
        if change<=-1:return "明顯偏空"
        if change<0:return "偏空"
        return "中性"
    intl_rows=[["市場／資產","最新值","漲跌幅","台股解讀","資料時間"]]+[[p(q["name"],small),str(q["price"]),f"{q['changePct']:+.2f}%",tone(float(q["changePct"])),datetime.fromisoformat(q["dataTime"]).astimezone(TAIPEI).strftime("%m/%d %H:%M")] for q in international.values()]
    chart_order=("sp500","nasdaq","sox","tsm","nvda","amd","nq_future","dxy","us10y","oil","gold")
    international_chart=change_chart([
        (international[key]["name"],international[key].get("changePct"))
        for key in chart_order if key in international
    ],"紅色上漲／綠色下跌；長度依本次資料最大漲跌幅縮放")
    ranking_day=db.scalar(select(func.max(StrongStockRanking.trade_date)).where(StrongStockRanking.trade_date<=day))
    watch=list(db.scalars(select(StrongStockRanking).where(StrongStockRanking.trade_date==ranking_day).order_by(StrongStockRanking.rank).limit(10)).all()) if ranking_day else []
    watch_rows=[["股票","產業","參考收盤","觀察區間","突破價","風控支撐","分數"]]+[[p(f"{item.symbol} {item.name}",small),p(item.industry,small),str(item.close_price),f"{item.entry_low or '—'}～{item.entry_high or '—'}",str(item.breakout_price or "—"),str(item.stop_price or "—"),str(item.total_score)] for item in watch]
    strong="、".join(json.loads(row.strong_industries_json or "[]")) or "資料尚未取得";weak="、".join(json.loads(row.weak_industries_json or "[]")) or "資料尚未取得"
    def industry_view(name):
        if any(word in name for word in ("半導體","電子","AI","電腦","光電","通訊")):return ("AI資本支出、先進製程／封裝、高速傳輸與新品週期","費半、台積電ADR、NASDAQ期貨及台股成交量同向","美元與美債殖利率走高、評價過高、領漲股跌破支撐")
        if "金融" in name:return ("利差、股利政策、資產品質與市場成交活絡","金融指數相對大盤轉強且成交量增加","債券評價損失、信用成本上升與利差收窄")
        if any(word in name for word in ("航運","運輸")):return ("運價、供需、旺季與地緣事件","運價與族群營收同步改善","油價上升、運力供給增加與報價反轉")
        if any(word in name for word in ("生技","醫療")):return ("臨床／取證、授權、產品上市與營收落地","里程碑具正式公告且量價確認","事件未達標、現金流與單一產品風險")
        return ("訂單能見度、報價、庫存循環與毛利率","營收趨勢、產業成交量與領漲股同步改善","需求不如預期、庫存回升與量價結構轉弱")
    strong_names=json.loads(row.strong_industries_json or "[]")
    industry_rows=[["產業","未來催化劑","需要確認","主要風險"]]+[[p(name,small),*[p(value,small) for value in industry_view(name)]] for name in strong_names[:6]]
    nasdaq=quote_change("nasdaq");sox=quote_change("sox");tsm=quote_change("tsm");nvidia=quote_change("nvda");amd=quote_change("amd")
    futures=quote_change("nq_future","es_future");dxy=quote_change("dxy");yield10=quote_change("us10y");oil=quote_change("oil");gold=quote_change("gold")
    direction=f"今日方向為{row.market_bias}，AI盤勢分數{row.market_score if row.market_score is not None else '資料尚未取得'}，風險等級{row.risk_level}，預估開盤型態為{row.opening_pattern}，建議持股比例{row.recommended_position}。08:00版本已整合目前可取得的完整隔夜資料；尚未公布的台股盤前資料會明確標為資料尚未取得。"
    tech_line=f"NASDAQ {tone(nasdaq)}、費城半導體 {tone(sox)}、台積電ADR {tone(tsm)}、NVIDIA {tone(nvidia)}、AMD {tone(amd)}；科技訊號是今日電子權值、AI伺服器與半導體族群的第一層風向。"
    macro_line=f"美國期貨 {tone(futures)}、美元指數 {tone(dxy)}、美債殖利率 {tone(yield10)}、原油 {tone(oil)}、黃金 {tone(gold)}。若美元與殖利率同步走高，電子成長股評價容易受壓；油價急升則留意運輸與高耗能族群成本。"
    focus=f"{tech_line} {macro_line} 今日相對強勢產業為{strong}；相對弱勢產業為{weak}。開盤必須同時確認加權指數、櫃買指數、台積電與市場量能，方向一致才提高出手比重。"
    risk_on=sum(change is not None and change>0 for change in (nasdaq,sox,tsm,nvidia,amd,futures))
    risk_off=sum(change is not None and change<0 for change in (nasdaq,sox,tsm,nvidia,amd,futures))
    cross_asset=f"跨市場訊號統計：科技與期貨六項指標中，{risk_on} 項偏多、{risk_off} 項偏空。若費半、台積電 ADR、NASDAQ 期貨三者同向，訊號可信度較高；若方向分歧，開盤前結論只能作為基準劇本，必須等待台股量價確認。"
    pre_analysis=professional_market_analysis({"score":row.market_score,"bias":row.market_bias,"risk":row.risk_level,"position":row.recommended_position,"strong":json.loads(row.strong_industries_json or "[]"),"weak":json.loads(row.weak_industries_json or "[]"),"international":international},"盤前")
    reference_day=day-timedelta(days=1)
    while not trading_day(reference_day):reference_day-=timedelta(days=1)
    reference_indexes=(await fetch_post_market_indexes(reference_day)).get("indexes",{})
    taiex_reference=reference_indexes.get("taiex",{});otc_reference=reference_indexes.get("otc",{})
    reference_chart=change_chart([("前一日加權",taiex_reference.get("return_1d")),("前一日櫃買",otc_reference.get("return_1d"))],"前一交易日加權／櫃買漲跌幅")
    taiex_ref_return=taiex_reference.get("return_1d");otc_ref_return=otc_reference.get("return_1d")
    reference_gap=(float(otc_ref_return)-float(taiex_ref_return)) if taiex_ref_return is not None and otc_ref_return is not None else None
    reference_volume=taiex_reference.get("volume_ratio_20d")
    reference_read=(f"前一交易日加權 {taiex_ref_return:+.2f}%、櫃買 {otc_ref_return:+.2f}%，櫃買相對加權差 {reference_gap:+.2f} 個百分點。" if reference_gap is not None else "前一交易日加權與櫃買同日漲跌資料尚未完整。")
    reference_read+=(f" 加權量能約為20日均量 {float(reference_volume):.2f} 倍；" if reference_volume is not None else " 20日量能比較尚未取得；")
    reference_read+=("中小型股相對占優，今日觀察題材是否持續擴散。" if reference_gap is not None and reference_gap>0 else "權值股相對占優，今日需確認櫃買與漲跌家數是否跟上。" if reference_gap is not None else "開盤後以加權、櫃買與成交量同步性確認。")
    fallback_support=[f"{label} {quote['support']}" for label,quote in (("加權",taiex_reference),("櫃買",otc_reference)) if quote.get("support") is not None]
    fallback_resistance=[f"{label} {quote['resistance']}" for label,quote in (("加權",taiex_reference),("櫃買",otc_reference)) if quote.get("resistance") is not None]
    support="、".join(json.loads(row.support_json or "[]") or fallback_support) or "資料尚未取得";resistance="、".join(json.loads(row.resistance_json or "[]") or fallback_resistance) or "資料尚未取得"
    messages=list(db.scalars(select(PrePostAnalysisMessage).where(PrePostAnalysisMessage.trade_date==day,PrePostAnalysisMessage.period=="PRE_MARKET").order_by(PrePostAnalysisMessage.created_at)).all())
    message_rows=[["時間／類別","分析重點","影響","操作建議"]]+[[p(f"{message.created_at.astimezone(TAIPEI):%H:%M} {message.category}",small),p(f"{message.title}：{message.content}",small),p(message.market_impact,small),p(message.operation_advice,small)] for message in messages[-10:]]
    source_names=[]
    for message in messages:
        try:
            for source in json.loads(message.sources_json or "[]"):
                name=source.get("name") or source.get("title") or source.get("url")
                if name and name not in source_names:source_names.append(str(name))
        except (TypeError,ValueError):pass
    buffer=io.BytesIO();doc=SimpleDocTemplate(buffer,pagesize=A4,rightMargin=10*mm,leftMargin=10*mm,topMargin=10*mm,bottomMargin=10*mm,title=f"每日盤前策略報告 {day}");story=[]
    mast=Table([[p("每日盤前策略報告",title)],[p(f"劉宗元｜交易日期：{day}　｜　資料截至：{row.data_as_of.astimezone(TAIPEI).strftime('%Y/%m/%d %H:%M') if row.data_as_of else '資料尚未取得'}",ParagraphStyle("PreMeta",parent=small,textColor=colors.HexColor("#E2F2EB"),alignment=TA_CENTER))]],colWidths=[190*mm]);mast.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#234E52")),("BOX",(0,0),(-1,-1),2,colors.HexColor("#C69B45")),("PADDING",(0,0),(-1,-1),8)]));story += [mast,Spacer(1,4*mm)]
    decision=Table([[p("今日盤勢",small),p(direction,body)]],colWidths=[30*mm,160*mm]);decision.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#FFF8E7")),("BOX",(0,0),(-1,-1),1.4,colors.HexColor("#C69B45")),("VALIGN",(0,0),(-1,-1),"MIDDLE"),("PADDING",(0,0),(-1,-1),8)]));story += [decision]
    domestic_rows=[["臺灣盤前指標","數值","資料日期"]]
    for key in ("tx_night","te_night","sof_night"):
        item=domestic.get(key)
        if item:domestic_rows.append([f"{item['name']} {item.get('contract','')}",f"{item['price']}（{(item.get('changePoints') or 0):+g} 點／{item['changePct']:+.2f}%）",item["dataTime"]+" 日報（非即時）"])
    if domestic.get("usdtwd"):domestic_rows.append(["美元兌新台幣",str(domestic["usdtwd"]["price"]),domestic["usdtwd"]["dataTime"]])
    if domestic.get("foreign_futures_oi"):domestic_rows.append(["外資臺股期貨未平倉淨額",f"{domestic['foreign_futures_oi']['netContracts']:,} 口",domestic["foreign_futures_oi"]["dataTime"]])
    if domestic.get("put_call_ratio"):domestic_rows.append(["Put/Call未平倉比",f"{domestic['put_call_ratio']['openInterestRatio']:.2f}%",domestic["put_call_ratio"]["dataTime"]])
    domestic_chart=change_chart([
        (f"{domestic[key]['name']} {domestic[key].get('contract','')}",domestic[key].get("changePct"))
        for key in ("tx_night","te_night","sof_night") if key in domestic
    ],"台灣夜盤期貨漲跌幅（最近公布日報，非即時）")
    gauge=score_gauge(row.market_score,row.recommended_position,row.risk_level)
    story += [
        p("今日一頁作戰摘要",heading),gauge,p(f"資料完整度：{f'{pre_completeness}%' if pre_completeness is not None else '資料尚未取得'}；缺失資料不納入評分。",small),p(focus),p(cross_asset,small),
        p("AI 盤勢分數拆解",heading),table([["台股動能","國際風險偏好","半導體科技鏈","國際期貨","資料完整度"],[pre_score_breakdown.get("localMomentum","—"),pre_score_breakdown.get("internationalRisk","—"),pre_score_breakdown.get("technologyChain","—"),pre_score_breakdown.get("futures","—"),pre_score_breakdown.get("dataCompleteness","—")]],[38*mm,38*mm,38*mm,38*mm,38*mm]),
        p("盤勢與國際局勢專業分析",heading),*[p(paragraph) for paragraph in pre_analysis],
        p("全球資產漲跌比較圖",heading),international_chart,
        p("隔夜國際市場與台股傳導",heading),table(intl_rows,[49*mm,27*mm,27*mm,37*mm,50*mm]),
        p("臺灣期貨漲跌比較圖",heading),domestic_chart,
        p("臺灣期貨、籌碼與匯率",heading),table(domestic_rows,[75*mm,65*mm,50*mm]),
        p("前一交易日加權與櫃買結構",heading),reference_chart,p(reference_read),table([["市場","收盤","單日漲跌","支撐","壓力","量能／20日均量"],["加權指數",taiex_reference.get("close","—"),f"{taiex_ref_return:+.2f}%" if taiex_ref_return is not None else "—",taiex_reference.get("support","—"),taiex_reference.get("resistance","—"),f"{float(reference_volume):.2f} 倍" if reference_volume is not None else "—"],["櫃買指數",otc_reference.get("close","—"),f"{otc_ref_return:+.2f}%" if otc_ref_return is not None else "—",otc_reference.get("support","—"),otc_reference.get("resistance","—"),"—"]],[34*mm,28*mm,28*mm,32*mm,32*mm,36*mm]),
        p("國際局勢判讀",heading),table([["觀察軸","目前判讀","今日台股要注意什麼"],["科技風險偏好",p(tech_line,small),p("費半、台積電ADR與NASDAQ同向時參考性較高；若彼此背離，降低追價。",small)],["利率與美元",p(f"美元指數 {tone(dxy)}；美債殖利率 {tone(yield10)}。",small),p("兩者同步轉強時，優先控制高本益比電子股部位。",small)],["原物料與避險",p(f"原油 {tone(oil)}；黃金 {tone(gold)}。",small),p("油價異常上漲留意成本壓力；黃金與美元同升時提高事件風險警覺。",small)]],[34*mm,76*mm,80*mm]),
        p("台股今日關鍵位置與產業",heading),table([["項目","今日觀察"],["重要支撐",p(support,small)],["重要壓力",p(resistance,small)],["相對強勢產業",p(strong,small)],["相對弱勢產業",p(weak,small)]],[36*mm,154*mm]),
        p("強勢產業未來展望",heading),table(industry_rows if len(industry_rows)>1 else [["產業展望資料尚未取得","等待產業排名完成後更新","—","—"]],[30*mm,58*mm,52*mm,50*mm]),
        p("三種開盤情境與行動",heading),table([["情境","確認訊號","操作方式"],["開高續強",p("加權與櫃買同步上漲，台積電穩住、強勢股量價延續。",small),p("等第一次拉回不破再布局，不追開盤瞬間急拉。",small)],["開高走低",p("權值股轉弱、漲家數下降、強勢股跌破開盤低點。",small),p("停止追多、縮小部位，等待重新站回均價與關鍵價。",small)],["開低轉強",p("指數守住支撐，櫃買先翻紅，領漲族群出現承接。",small),p("只做率先轉強且有量股票，分批進場並以支撐停損。",small)]],[31*mm,82*mm,77*mm]),
        p("開盤操作劇本",heading),table([["時段","觀察與行動"],["09:00～09:05",p("看開盤缺口、台積電、加權與櫃買方向及成交量，不下方向不明的第一筆。",small)],["09:05～09:15",p("確認領漲產業是否延續、個股是否拉回量縮且守住開盤低點。",small)],["09:15後",p(row.day_trade_advice,small)],["現股布局",p(row.stock_advice,small)],["部位控制",p(f"建議持股 {row.recommended_position}；未確認大盤與個股站穩前不一次買滿。",small)]],[32*mm,158*mm]),
        p("盤勢失效與風險條件",heading),table([["偏多確認","降低部位／停止新增"],[p("加權與櫃買同向轉強；強勢產業量價延續；觀察股拉回不破支撐。",small),p("大盤跌破重要支撐；費半、台積電ADR或NASDAQ期貨同步轉弱；美元、殖利率或油價急升。",small)]],[95*mm,95*mm]),
        p("盤前事件與分析更新時間軸",heading),table(message_rows if len(message_rows)>1 else [["暫無盤前事件","資料尚未取得","—","—"]],[32*mm,88*mm,24*mm,46*mm]),
        PageBreak(),p("盤前10檔觀察名單",heading),p(f"依 {ranking_day or '資料尚未取得'} 可驗證的強勢股排名選出。必須等價格觸發與市場確認，屬觀察名單，不代表直接買進。",small),table(watch_rows,[35*mm,27*mm,24*mm,35*mm,23*mm,23*mm,23*mm]),
        p("資料來源與完整性",heading),p("資料來源："+("、".join(source_names) if source_names else "盤前訊息尚無可列示來源；國際行情由免費公開行情來源即時取得")+"。所有數值均附資料時間；缺失資料不納入推論。",small),Spacer(1,4*mm),p("本報告為市場分析，不保證獲利。請依自身風險承受度判斷，設定停損並控制總部位。",small),
    ]
    doc.build(story);content=buffer.getvalue();filename=f"劉宗元大神盤前報告＋{day}.pdf";disposition=f"attachment; filename=pre-market-report-{day}.pdf; filename*=UTF-8''{quote(filename)}"
    return Response(content,media_type="application/pdf",headers={"Content-Disposition":disposition})

@router.post("/reanalyze")
async def reanalyze(_:str=Depends(uid)):return await prepost_automation.analyze(force=True)

class EventBody(BaseModel):
    title:str=Field(min_length=2,max_length=160);content:str=Field(min_length=5,max_length=10000);category:str="重大國際事件";importance:str="IMPORTANT";data_time:datetime;event_key:str=Field(min_length=4,max_length=160);sources:list[dict]=[];market_impact:str="資料尚未取得";industries:list[str]=[];stocks:list[dict]=[]

@router.post("/events",status_code=201)
async def event(body:EventBody,db:Session=Depends(get_db)):
    if body.importance not in {"NORMAL","NOTICE","IMPORTANT","EMERGENCY"}:raise HTTPException(422,"無效重要程度")
    now=datetime.now(UTC);day,period,status=phase_for(now)
    if period=="BACKGROUND":
        target=day if trading_day(day) and now.astimezone(TAIPEI).time().hour<9 else next_trading_day(day)
        existing=db.scalar(select(PrePostPendingEvent).where(PrePostPendingEvent.event_key==body.event_key))
        if existing:return {"duplicate":True,"queued":True}
        db.add(PrePostPendingEvent(event_key=body.event_key,target_trade_date=target,payload_json=body.model_dump_json(),data_time=body.data_time,created_at=now));db.commit()
        return {"duplicate":False,"queued":True,"targetTradeDate":target}
    row=ensure_day(db,day,now);data=snapshot(db,day);data.update(sources=body.sources,data_time=body.data_time,strong=body.industries or data["strong"]);msg=add_message(db,row,period,body.category,body.title,data,now,importance=body.importance,event_key=body.event_key,content=body.content)
    if not msg:return {"duplicate":True}
    msg.market_impact=body.market_impact;msg.industry_impact_json=json.dumps(body.industries,ensure_ascii=False);msg.stock_impact_json=json.dumps(body.stocks,ensure_ascii=False);update_summary(row,data,now,status,period);db.commit();return {"duplicate":False,"item":serialize_message(msg)}

class PatchMessage(BaseModel):marked_important:bool|None=None;followed:bool|None=None
@router.patch("/messages/{message_id}")
def patch_message(message_id:str,body:PatchMessage,db:Session=Depends(get_db)):
    row=db.scalar(select(PrePostAnalysisMessage).where(PrePostAnalysisMessage.message_id==message_id))
    if not row:raise HTTPException(404,"找不到分析訊息")
    if body.marked_important is not None:row.is_marked_important=body.marked_important
    if body.followed is not None:row.is_followed=body.followed
    row.updated_at=datetime.now(UTC);db.commit();return serialize_message(row)

@router.post("/messages/{message_id}/mail")
async def mail(message_id:str,db:Session=Depends(get_db)):
    from ..services.prepost_analysis import notify
    from ..services.gmail_messaging import gmail_notification_dispatcher
    row=db.scalar(select(PrePostAnalysisMessage).where(PrePostAnalysisMessage.message_id==message_id))
    if not row:raise HTTPException(404,"找不到分析訊息")
    sent=await notify(row,force=True);return {"sent":bool(sent),"configured":gmail_notification_dispatcher.configured}

@router.get("/settings")
def settings(user:str=Depends(uid),db:Session=Depends(get_db)):
    row=db.get(PrePostNotificationSetting,user)
    if not row:row=PrePostNotificationSetting(user_id=user,updated_at=datetime.now(UTC));db.add(row);db.commit()
    return {"preMarket":row.pre_market,"postMarket":row.post_market,"importantEvents":row.important_events,"emergencyEvents":row.emergency_events,"mailEnabled":row.mail_enabled,"minimumImportance":row.minimum_importance}

class SettingsBody(BaseModel):preMarket:bool=True;postMarket:bool=True;importantEvents:bool=True;emergencyEvents:bool=True;mailEnabled:bool=False;minimumImportance:str="IMPORTANT"
@router.patch("/settings")
def save_settings(body:SettingsBody,user:str=Depends(uid),db:Session=Depends(get_db)):
    row=db.get(PrePostNotificationSetting,user) or PrePostNotificationSetting(user_id=user,updated_at=datetime.now(UTC));db.add(row)
    row.pre_market=body.preMarket;row.post_market=body.postMarket;row.important_events=body.importantEvents;row.emergency_events=body.emergencyEvents;row.mail_enabled=body.mailEnabled;row.minimum_importance=body.minimumImportance;row.updated_at=datetime.now(UTC);db.commit();return settings(user,db)

@router.get("/export")
def export(start_date:date|None=None,end_date:date|None=None,format:str="csv",db:Session=Depends(get_db)):
    q=select(PrePostAnalysisMessage)
    if start_date:q=q.where(PrePostAnalysisMessage.trade_date>=start_date)
    if end_date:q=q.where(PrePostAnalysisMessage.trade_date<=end_date)
    rows=db.scalars(q.order_by(PrePostAnalysisMessage.trade_date,PrePostAnalysisMessage.created_at)).all();out=io.StringIO();w=csv.writer(out);w.writerow(["分析日期","期間","時間","分類","重要程度","標題","內容","市場影響","操作建議","信心分數","來源"])
    for r in rows:w.writerow([r.trade_date,r.period,r.created_at,r.category,r.importance,r.title,r.content,r.market_impact,r.operation_advice,r.confidence_score,r.sources_json])
    # Excel opens UTF-8 CSV directly. XLSX is intentionally not faked.
    if format not in {"csv","excel"}:raise HTTPException(422,"format 必須是 csv 或 excel")
    return Response("\ufeff"+out.getvalue(),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="prepost-analysis.{"csv" if format=="csv" else "xls"}"'})

@router.get("/accuracy")
def accuracy(db:Session=Depends(get_db)):
    rows=list(db.scalars(select(PrePostAnalysisDay).where(PrePostAnalysisDay.prediction_result.in_(["完全符合","部分符合","不符合"])).order_by(PrePostAnalysisDay.trade_date.desc())).all())
    def score(items):
        if not items:return {"sampleSize":0,"accuracy":None}
        points=sum(1 if r.prediction_result=="完全符合" else .5 if r.prediction_result=="部分符合" else 0 for r in items)
        return {"sampleSize":len(items),"accuracy":round(points/len(items)*100,2)}
    return {"last20":score(rows[:20]),"last60":score(rows[:60]),"all":score(rows)}

@router.get("/stream")
async def stream(trade_date:date|None=None):
    async def events():
        last=""
        while True:
            with SessionLocal() as db:
                day=selected_day(trade_date);row=ensure_day(db,day,datetime.now(UTC));db.commit();payload=json.dumps(serialize_day(db,row),default=str,ensure_ascii=False)
            if payload!=last:yield f"event: analysis\ndata: {payload}\n\n";last=payload
            else:yield ": keepalive\n\n"
            await asyncio.sleep(10)
    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"},
    )
