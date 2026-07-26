"use client";

import { useEffect, useState } from "react";
import { Activity, ChevronRight, Gauge, ShieldAlert, Sparkles, Target, Zap } from "lucide-react";
import type { PowerScoreResult } from "@/lib/power-score";
import { formatPercent, safeNumber, valueClass } from "@/lib/format";
import type { LeaderPowerResponse } from "@/services/leader-power-service";

function yesNo(value: boolean, yes: string, no: string) {
  return <span className={value ? "power-yes" : "power-no"}>{value ? yes : no}</span>;
}

function zone(value: { min: number; max: number } | null) {
  return value ? `${safeNumber(value.min)}～${safeNumber(value.max)}` : "暫不提供";
}

export function LeaderPowerPanel({
  currentSymbol,
  currentName,
  score,
  onSelectStock,
}: {
  currentSymbol: string;
  currentName: string;
  score: PowerScoreResult;
  onSelectStock?: (symbol: string) => void;
}) {
  const [leaders, setLeaders] = useState<LeaderPowerResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setError("");
    void fetch("/api/leader-power")
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error ?? "馬力榜讀取失敗");
        return payload as LeaderPowerResponse;
      })
      .then((payload) => { if (!cancelled) setLeaders(payload); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : "馬力榜讀取失敗"); });
    return () => { cancelled = true; };
  }, []);

  return (
    <section className="leader-power-section">
      <div className="current-power-card">
        <div className="power-card-heading">
          <div><Sparkles size={17} /><span>AI 龍頭馬力評分</span><small>{currentSymbol} {currentName}</small></div>
          <div className="power-main-score"><strong>{score.powerValue}</strong><span>／17 馬力</span></div>
        </div>
        <div className="power-score-strip">
          <div><Gauge size={15} /><span>AI 健康度</span><strong>{score.healthScore}</strong><small>／100</small></div>
          <div className="power-stars" aria-label={`${score.stars} 星評級`}>{score.starLabel}</div>
          <div className="power-coverage">資料覆蓋 {score.dataCoverage}%</div>
        </div>
        <div className="power-level-grid">
          <div><span>支撐價</span><strong>{safeNumber(score.support)}</strong></div>
          <div><span>壓力價</span><strong>{safeNumber(score.resistance)}</strong></div>
          <div><span>買點</span><strong>{zone(score.buyPoint)}</strong></div>
          <div><span>加碼點</span><strong>{safeNumber(score.addPoint)}</strong></div>
          <div><span>停損</span><strong className="down">{safeNumber(score.stopLoss)}</strong></div>
          <div><span>停利</span><strong className="up">{safeNumber(score.takeProfit)}</strong></div>
        </div>
        <div className="power-status-grid">
          <div><span>是否突破</span>{yesNo(score.isBreakout, "已突破", "尚未突破")}</div>
          <div><span>多方攻擊</span>{yesNo(score.isBullAttack, "成立", "未成立")}</div>
          <div><span>是否可買</span>{yesNo(score.canBuy, "可分批觀察", "暫不建議")}</div>
          <div><span>是否可加碼</span>{yesNo(score.canAdd, "可等待確認", "不可加碼")}</div>
          <div><span>是否需停利</span>{yesNo(score.needsTakeProfit, "需提高保護", "暫不需要")}</div>
          <div><span>是否需停損</span>{yesNo(score.needsStopLoss, "優先風控", "尚未觸發")}</div>
        </div>
        <div className="power-suggestion"><Zap size={15} /><div><strong>操作建議</strong><p>{score.suggestion}</p></div></div>
        <div className="power-section-bars">
          {score.sections.map((section) => (
            <div key={section.name}>
              <span>{section.name}</span><i><b style={{ width: `${section.maxScore ? section.score / section.maxScore * 100 : 0}%` }} /></i>
              <strong>{section.score}/{section.maxScore}</strong>
            </div>
          ))}
        </div>
        <details className="power-deductions">
          <summary><ShieldAlert size={13} />查看扣分原因（{score.deductions.length}）</summary>
          <ul>{score.deductions.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        </details>
      </div>

      <div className="weighted-power-board">
        <div className="weighted-board-heading">
          <div><Activity size={17} /><span>前 15 大權值股馬力榜</span></div>
          <small>{leaders ? `權重資料 ${leaders.sourceDate}` : "讀取中…"}</small>
        </div>
        {error ? <div className="power-board-state error">{error}</div> : !leaders ? (
          <div className="power-board-state"><span className="spinner small" />正在計算馬力值…</div>
        ) : (
          <>
            <div className="weighted-table-wrap">
              <table className="weighted-power-table">
                <thead><tr><th>排名</th><th>股票</th><th>權重</th><th>現價</th><th>漲跌</th><th>馬力</th><th>健康度</th><th>評級</th><th /></tr></thead>
                <tbody>
                  {leaders.rows.map((row) => (
                    <tr key={row.symbol} className={row.symbol === currentSymbol ? "current" : ""} onClick={() => onSelectStock?.(row.symbol)}>
                      <td>{row.rank}</td>
                      <td><strong>{row.symbol}</strong><span>{row.name}</span></td>
                      <td>{row.weight.toFixed(2)}%</td>
                      <td className="power-quote-cell" title={`報價時間：${row.quoteTime}`}>
                        <strong>{safeNumber(row.price)}</strong>
                        <small>{row.quoteSource}</small>
                      </td>
                      <td className={valueClass(row.changePercent)}>{formatPercent(row.changePercent)}</td>
                      <td><b className="table-power-value">{row.score.powerValue}</b><small>/17</small></td>
                      <td>{row.score.healthScore}</td>
                      <td className="table-stars">{row.score.starLabel}</td>
                      <td><button aria-label={`查看 ${row.name} 分析`}><ChevronRight size={13} /></button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="power-board-note">
              <Target size={12} /><span>{leaders.dataNotice}</span>
              <a href={leaders.sourceUrl} target="_blank" rel="noreferrer">權值來源</a>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
