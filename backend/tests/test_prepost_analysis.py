from datetime import UTC,date,datetime
from app.services.prepost_analysis import owner_trade_date,phase_for,trading_day,add_message,ensure_day,evaluate_prediction,snapshot,analysis_content,professional_market_analysis,sentiment_for,update_summary,serialize_day,POST_STAGES
from app.services.international_market_data import international_market_provider
from app.services.post_market_data import parse_index_history,parse_tpex_index_summary
from app.services.taifex_market_data import parse_taifex
from app.prepost_models import PrePostAnalysisMessage
from app.database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
import pytest


def test_post_market_index_history_fills_report_metrics_for_requested_day():
    stamps=[int(datetime(2026,9,10+i,tzinfo=UTC).timestamp()) for i in range(3)]
    payload={"chart":{"result":[{"timestamp":stamps,"indicators":{"quote":[{
        "close":[100.0,102.0,101.0],"volume":[1000,1200,1100],
    }]}}]}}
    result=parse_index_history(payload,date(2026,9,11))
    assert result is not None
    assert result["close"] == 102.0
    assert result["return_1d"] == 2.0
    assert result["support"] == 100.0
    assert result["resistance"] == 102.0
    assert result["source"] == "Yahoo Finance chart"


def test_post_market_index_history_never_substitutes_another_date():
    stamp=int(datetime(2026,9,10,tzinfo=UTC).timestamp())
    payload={"chart":{"result":[{"timestamp":[stamp],"indicators":{"quote":[{"close":[100.0],"volume":[1000]}]}}]}}
    assert parse_index_history(payload,date(2026,9,11)) is None


def test_official_tpex_summary_fills_otc_close_and_change():
    payload={"date":"20260915","stat":"ok","tables":[{
        "fields":["指數","收市指數","漲跌","漲跌幅度(%)","大盤資訊連結"],
        "data":[["櫃買指數","388.73","-5.94","-1.51",""]],
    }]}
    result=parse_tpex_index_summary(payload,date(2026,9,15))
    assert result is not None
    assert result["close"] == 388.73
    assert result["return_1d"] == -1.51
    assert result["source"] == "TPEx 上櫃股價指數收盤行情"


def test_professional_analysis_covers_market_macro_transmission_and_risk():
    data={"score":66,"bias":"震盪偏多","risk":"中等","position":"25%～40%","strong":["半導體"],"weak":["航運"],"international":{
        "sp500":{"name":"S&P 500 指數","changePct":.8},
        "nasdaq":{"name":"NASDAQ 指數","changePct":1.2},
        "sox":{"name":"費城半導體指數","changePct":1.5},
        "tsm":{"name":"台積電 ADR","changePct":1.1},
        "dxy":{"name":"美元指數","changePct":.2},
        "us10y":{"name":"美國10年期公債殖利率","changePct":.1},
        "oil":{"name":"WTI 原油","changePct":-.4},
    }}
    sections=professional_market_analysis(data,"盤前")
    report="".join(sections)
    assert len(sections) == 8
    assert "全球風險情緒" in report
    assert "資金與總體環境" in report
    assert "原物料與避險觀察" in report
    assert "台股傳導與產業配置" in report
    assert "跨市場數據統計" in report
    assert "情境推演" in report
    assert "停止追價並降低曝險" in report


def test_professional_post_market_analysis_compares_taiex_and_otc():
    data={"score":55,"bias":"區間震盪","risk":"中等","strong":[],"weak":[],"international":{}}
    sections=professional_market_analysis(data,"盤後",{"taiex_return_1d":.3,"otc_return_1d":.8,"volume_ratio_20d":1.2})
    assert "櫃買表現優於加權" in sections[0]
    assert "1.20 倍" in sections[0]


def test_prediction_validation_compares_premarket_bias_with_both_indexes():
    correct=evaluate_prediction({"bias":"震盪偏多"},.8,.4)
    wrong=evaluate_prediction({"bias":"震盪偏多"},-.8,-.4)
    assert correct["status"] == "符合"
    assert wrong["status"] == "未符合"
    assert correct["items"][0]["result"] == "正確"


def test_taifex_parser_fills_premarket_derivatives_and_position_data():
    rows={
        "futures":[
            {"Date":"20260915","Contract":"TX","ContractMonth(Week)":"202609","Last":"45556","%":"-0.48%","Volume":"28527","TradingSession":"盤後"},
            {"Date":"20260915","Contract":"TE","ContractMonth(Week)":"202609","Last":"2874.15","%":"-0.14%","Volume":"95","TradingSession":"盤後"},
            {"Date":"20260915","Contract":"SOF","ContractMonth(Week)":"202609","Last":"21000","%":"0.25%","Volume":"10","TradingSession":"盤後"},
        ],
        "put_call":[{"Date":"20260915","PutCallVolumeRatio%":"106.44","PutCallOIRatio%":"85.89"}],
        "institutions":[{"Date":"20260915","ContractCode":"臺股期貨","Item":"外資及陸資","OpenInterest(Long)":"10863","OpenInterest(Short)":"94086","OpenInterest(Net)":"-83223"}],
        "fx":[{"Date":"20260915","USD/NTD":"32.438"}],
    }
    result=parse_taifex(rows)
    assert result["tx_night"]["changePct"] == -.48
    assert result["tx_night"]["changePoints"] is None
    assert result["tx_night"]["contract"] == "202609"
    assert result["tx_night"]["isRealtime"] is False
    assert result["te_night"]["price"] == 2874.15
    assert result["put_call_ratio"]["openInterestRatio"] == 85.89
    assert result["foreign_futures_oi"]["netContracts"] == -83223
    assert result["usdtwd"]["price"] == 32.438

@pytest.fixture
def db():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session: yield session


def test_cross_midnight_post_market_belongs_to_previous_trade_day():
    day,period,status=phase_for(datetime(2026,9,15,3,0,tzinfo=UTC)) # 11:00 Taipei, not overnight
    assert period == "MARKET"
    day,period,status=phase_for(datetime(2026,9,14,18,30,tzinfo=UTC)) # 02:30 Taipei next day
    assert day == date(2026,9,14)
    assert period == "POST_MARKET"


def test_weekend_is_market_closed():
    day,period,status=phase_for(datetime(2026,9,13,2,0,tzinfo=UTC))
    assert not trading_day(day)
    assert status == "MARKET_CLOSED"


def test_taipei_schedule_boundaries():
    # UTC+8: midnight UTC is 08:00 Taipei.
    assert phase_for(datetime(2026, 9, 14, 0, 0, tzinfo=UTC))[1:] == ("PRE_MARKET", "PRE_MARKET_ANALYZING")
    assert phase_for(datetime(2026, 9, 14, 0, 30, tzinfo=UTC))[1:] == ("PRE_MARKET", "PRE_MARKET_ANALYZING")
    assert phase_for(datetime(2026, 9, 14, 0, 45, tzinfo=UTC))[1:] == ("PRE_MARKET", "PRE_MARKET_ANALYZING")
    assert phase_for(datetime(2026, 9, 14, 0, 46, tzinfo=UTC))[1:] == ("PRE_MARKET", "PRE_MARKET_COMPLETE")
    assert phase_for(datetime(2026, 9, 14, 1, 0, tzinfo=UTC))[1:] == ("MARKET", "INTRADAY")
    assert phase_for(datetime(2026, 9, 14, 5, 30, tzinfo=UTC))[1:] == ("POST_MARKET", "POST_MARKET_ANALYZING")
    assert next(stage for stage in POST_STAGES if stage[1] == "法人籌碼")[0].strftime("%H:%M") == "15:05"


def test_message_dedupe_preserves_original(db):
    now=datetime(2026,9,14,0,31,tzinfo=UTC)
    day=ensure_day(db,date(2026,9,14),now)
    data={"score":50,"bias":"區間震盪","risk":"中等","strong":[],"weak":[],"rankings":[],"sources":[],"missing":[],"data_time":now}
    first=add_message(db,day,"PRE_MARKET","國際市場","測試",data,now,event_key="same")
    db.commit()
    assert first is not None
    assert add_message(db,day,"PRE_MARKET","國際市場","變更標題",data,now,event_key="same") is None
    assert db.query(PrePostAnalysisMessage).count()==1


def test_message_ids_do_not_collide_between_trade_dates(db):
    now=datetime(2026,9,15,5,31,tzinfo=UTC)
    first_day=ensure_day(db,date(2026,9,14),now)
    second_day=ensure_day(db,date(2026,9,15),now)
    data={"score":50,"bias":"區間震盪","risk":"中等","strong":[],"weak":[],"rankings":[],"sources":[],"missing":[],"international":{},"data_time":now}
    first=add_message(db,first_day,"POST_MARKET","台股收盤","測試",data,now,event_key="first-day-event")
    second=add_message(db,second_day,"POST_MARKET","台股收盤","測試",data,now,event_key="second-day-event")
    db.commit()
    assert first.message_id != second.message_id
    assert first.message_id.startswith("AM-20260914-")
    assert second.message_id.startswith("AM-20260915-")


def test_international_market_changes_are_included_without_fabricating_missing(db):
    international_market_provider._snapshot={"quotes":{
        "sp500":{"name":"S&P 500 指數","changePct":1.2,"source":"Yahoo Finance chart","dataTime":"2026-09-14T20:00:00+00:00"},
        "nasdaq":{"name":"NASDAQ 指數","changePct":1.5,"source":"Yahoo Finance chart","dataTime":"2026-09-14T20:00:00+00:00"},
        "sox":{"name":"費城半導體指數","changePct":2.0,"source":"Yahoo Finance chart","dataTime":"2026-09-14T20:00:00+00:00"},
        "tsm":{"name":"台積電 ADR","changePct":1.8,"source":"Yahoo Finance chart","dataTime":"2026-09-14T20:00:00+00:00"},
        "nq_future":{"name":"NASDAQ 期貨","changePct":.5,"source":"Yahoo Finance chart","dataTime":"2026-09-14T20:00:00+00:00"},
    },"errors":{},"updatedAt":"2026-09-14T20:00:00+00:00"}
    data=snapshot(db,date(2026,9,15))
    assert data["international"]["tsm"]["changePct"] == 1.8
    assert data["score"] > 50
    assert "美元指數" in data["missing"]
    assert any("台積電 ADR" in source["name"] for source in data["sources"])
    assert data["score_breakdown"]["internationalRisk"] is not None
    assert 0 <= data["data_completeness"] <= 100


def test_daily_snapshot_is_frozen_for_historical_dashboard(db):
    now=datetime(2026,9,15,0,30,tzinfo=UTC)
    row=ensure_day(db,date(2026,9,15),now)
    data={"score":64,"bias":"震盪偏多","risk":"中等","strong":[],"weak":[],"rankings":[],"sources":[],"missing":[],"international":{"nasdaq":{"name":"NASDAQ","changePct":1.2}},"domestic":{},"data_time":now,"score_breakdown":{"internationalRisk":60,"dataCompleteness":50},"data_completeness":50}
    update_summary(row,data,now,"PRE_MARKET_COMPLETE","PRE_MARKET")
    db.commit()
    international_market_provider._snapshot={"quotes":{"nasdaq":{"name":"NASDAQ","changePct":-9.9}}}
    payload=serialize_day(db,row)
    assert payload["marketCharts"]["international"]["nasdaq"]["changePct"] == 1.2
    assert payload["scoreBreakdown"]["internationalRisk"] == 60
    assert payload["dataCompleteness"] == 50


def test_each_premarket_stage_has_distinct_analysis_content():
    data={"score":55,"bias":"區間震盪","risk":"中等","strong":["半導體"],"weak":["航運"],"missing":["台指期夜盤"],"international":{
        "nasdaq":{"name":"NASDAQ 指數","changePct":1.1},"sox":{"name":"費城半導體指數","changePct":1.5},
        "tsm":{"name":"台積電 ADR","changePct":.8},"dxy":{"name":"美元指數","changePct":.2},
    }}
    stages=[("國際市場","08:00 國際市場與隔夜走勢初步分析"),("國際市場","國際市場與總體經濟更新"),("美股與期貨","美股、科技股與美國期貨分析"),("法人籌碼","台指期、匯率、債券與法人籌碼分析"),("台股產業分析","台股產業、公司消息與重大事件分析"),("當沖操作建議","盤前操作與風險控制建議"),("盤前最終結論","08:45 今日盤勢統整與方向")]
    contents=[analysis_content(category,title,data) for category,title in stages]
    assert len(set(contents)) == len(stages)
    assert "隔夜美股收盤" in contents[0]
    assert "匯率、債券與籌碼" in contents[3]


def test_sentiment_uses_stage_specific_market_effect():
    data={"score":62,"international":{
        "sox":{"changePct":1.2},"tsm":{"changePct":.8},"nvda":{"changePct":1.0},
        "dxy":{"changePct":.7},"us10y":{"changePct":.5},"oil":{"changePct":.6},
    }}
    assert sentiment_for("美股與期貨","科技分析",data) == "利多"
    assert sentiment_for("法人籌碼","籌碼分析",data) == "利空"
    assert sentiment_for("盤前最終結論","最終",data) == "利多"
