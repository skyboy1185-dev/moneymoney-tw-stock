import Link from "next/link";
import {
  Activity, BarChart3, Bot, Flame, Landmark, Newspaper, Search,
  SlidersHorizontal, Star, TrendingUp, UsersRound, Waves,
} from "lucide-react";
import { DayTradingDashboard } from "@/components/day-trading/DayTradingDashboard";
import { ElectronicChipFlowTicker } from "@/components/ElectronicChipFlowTicker";
import { LegalTermsButton } from "@/components/LegalTermsGate";
import { PrivateSiteLogoutButton } from "@/components/PrivateSiteLogoutButton";

export const metadata = {
  title: "AI 當沖多空機器人｜Moneymoney 台股分析",
  description: "AI 當沖多空訊號、模擬績效與風險控管。",
};

export default function DayTradingBotPage() {
  return <div className="app-shell">
    <ElectronicChipFlowTicker />
    <header className="topbar enhanced">
      <Link className="brand" href="/?view=analysis" aria-label="回到個股分析">
        <span className="brand-icon"><BarChart3 size={20} /></span>
        <span><strong>Moneymoney</strong><small>台股分析</small></span>
      </Link>
      <form className="search-form" action="/" method="get">
        <Search size={17} />
        <input name="symbol" defaultValue="2330" placeholder="輸入股票代號或名稱" />
        <input type="hidden" name="view" value="analysis" />
        <button type="submit">查詢</button>
      </form>
      <div className="connection-panel">
        <div className="connection-state connected"><Bot size={14} /><span>當沖機器人運作中</span></div>
        <div className="global-update"><span>資料串流</span><strong>LIVE</strong></div>
      </div>
    </header>
    <nav className="main-nav day-trading-nav" aria-label="主要功能">
      <Link href="/?view=analysis"><Activity size={17} />個股分析</Link>
      <Link href="/?view=screener"><SlidersHorizontal size={17} />AI 選股</Link>
      <Link className="active ai-nav" href="/day-trading-bot"><Bot size={17} />當沖機器人<span>LIVE</span></Link>
      <Link href="/?view=ai"><TrendingUp size={17} />AI選股機器人</Link>
      <Link href="/?view=large-holders"><UsersRound size={17} />大戶持股變化榜</Link>
      <Link href="/?view=institutional-investors"><Landmark size={17} />三大法人</Link>
      <Link href="/?view=chip-flow"><Waves size={17} />盤中籌碼</Link>
      <Link href="/?view=portfolio"><Star size={17} />觀察清單</Link>
      <Link href="/?view=industries"><Flame size={17} />產業熱點</Link>
      <Link href="/?view=news"><Newspaper size={17} />新聞</Link>
    </nav>
    <main className="main-content day-trading-main"><DayTradingDashboard /></main>
    <footer>
      <span className="footer-primary">模擬訊號與績效僅供研究參考，不構成投資建議。</span>
      <LegalTermsButton /><PrivateSiteLogoutButton />
    </footer>
  </div>;
}
