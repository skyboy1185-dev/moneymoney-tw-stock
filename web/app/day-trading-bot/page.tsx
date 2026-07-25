import Link from "next/link";
import {
  Activity, BarChart3, Bot, BrainCircuit, Flame, Newspaper, Search, SlidersHorizontal, Star,
} from "lucide-react";
import { DayTradingDashboard } from "@/components/day-trading/DayTradingDashboard";

export const metadata = {
  title: "AI 當沖多空機器人｜Moneymoney 台股分析",
  description: "展示模式的台股即時多空訊號、模擬持倉與風控儀表板",
};

export default function DayTradingBotPage() {
  return <div className="app-shell">
    <header className="topbar enhanced">
      <Link className="brand" href="/?view=analysis" aria-label="回到個股分析">
        <span className="brand-icon"><BarChart3 size={20} /></span>
        <span><strong>Moneymoney</strong><small>台股分析</small></span>
      </Link>
      <form className="search-form" action="/" method="get">
        <Search size={17} /><input name="symbol" defaultValue="2330" placeholder="股票代號或名稱" />
        <input type="hidden" name="view" value="analysis" /><button type="submit">查詢</button>
      </form>
      <div className="connection-panel"><div className="connection-state connected"><Bot size={14} /><span>當沖機器人展示模式</span></div><div className="global-update"><span>資料來源</span><strong>MOCK SSE</strong></div></div>
    </header>
    <nav className="main-nav" aria-label="主要功能">
      <Link href="/?view=analysis"><Activity size={17} />個股分析</Link>
      <Link href="/?view=screener"><SlidersHorizontal size={17} />AI 選股</Link>
      <Link className="active ai-nav" href="/day-trading-bot"><Bot size={17} />當沖機器人<span>LIVE</span></Link>
      <Link href="/?view=ai"><BrainCircuit size={17} />大盤多空方向</Link>
      <Link href="/?view=portfolio"><Star size={17} />觀察清單</Link>
      <Link href="/?view=industries"><Flame size={17} />產業熱點</Link>
      <Link href="/?view=news"><Newspaper size={17} />新聞</Link>
    </nav>
    <main className="main-content day-trading-main"><DayTradingDashboard /></main>
    <footer><span className="footer-primary">本網站資訊與選股結果僅供研究參考，不構成任何投資建議。</span><span>展示模式，非即時行情；所有交易均由使用者自行確認。</span></footer>
  </div>;
}
