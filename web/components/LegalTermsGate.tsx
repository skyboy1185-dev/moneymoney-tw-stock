"use client";

import { useEffect, useState } from "react";
import { Scale, ShieldAlert, X } from "lucide-react";

const TERMS_VERSION = "2026-08-03";
const STORAGE_KEY = `moneymoney-legal-terms:${TERMS_VERSION}`;
const OPEN_EVENT = "moneymoney:open-legal-terms";

export function openLegalTerms() {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

export function LegalTermsGate() {
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [previouslyAccepted, setPreviouslyAccepted] = useState(false);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    if (window.location.pathname === "/login") {
      setReady(true);
      return;
    }
    const accepted = localStorage.getItem(STORAGE_KEY) === "accepted";
    setPreviouslyAccepted(accepted);
    setOpen(!accepted);
    setReady(true);
    const show = () => {
      setChecked(accepted);
      setOpen(true);
    };
    window.addEventListener(OPEN_EVENT, show);
    return () => window.removeEventListener(OPEN_EVENT, show);
  }, []);

  useEffect(() => {
    if (!ready) return;
    const previousOverflow = document.body.style.overflow;
    if (open) document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open, ready]);

  const accept = () => {
    localStorage.setItem(STORAGE_KEY, "accepted");
    localStorage.setItem(`${STORAGE_KEY}:accepted-at`, new Date().toISOString());
    setPreviouslyAccepted(true);
    setOpen(false);
  };

  const leave = () => {
    if (window.history.length > 1) window.history.back();
    else window.location.replace("about:blank");
  };

  if (!ready || !open) return null;

  return (
    <div className="legal-terms-backdrop" role="presentation">
      <section className="legal-terms-dialog" role="dialog" aria-modal="true" aria-labelledby="legal-terms-title">
        <header>
          <span className="legal-terms-icon"><Scale size={22} /></span>
          <div>
            <small>TESTING &amp; EXPERIMENTAL USE ONLY</small>
            <h1 id="legal-terms-title">免責聲明與使用者條款</h1>
          </div>
          {previouslyAccepted && <button className="legal-terms-close" type="button" onClick={() => setOpen(false)} aria-label="關閉條款"><X size={18} /></button>}
        </header>

        <div className="legal-terms-body">
          <div className="legal-terms-warning">
            <ShieldAlert size={18} />
            <p>本站目前處於演算法與系統開發之<strong>測試、實驗階段</strong>。</p>
          </div>
          <p>本網站（下稱「本站」）所呈現之所有內容（包含但不限於由人工智慧、演算法自動產出之數據、模型推估、圖表、分數、歷史回測或任何模擬訊號），僅供學術研究、技術測試、教學與個人作品展示之用，絕不構成任何形式的證券投資顧問業務服務、專業投資建議、或任何有價證券之買賣推介與勸誘。</p>
          <p>使用者在使用本站資訊時，應知悉並同意以下事項：</p>
          <ol>
            <li>本站非屬金管會核准之證券投資顧問事業，亦無無照經營投顧業務之意圖。</li>
            <li>本站所展示之所有數據與演算法結果，皆不保證其即時性、準確性、完整性或未來之獲利性。任何模擬之買賣點或數據皆為歷史資料回測或模型實驗之客觀呈現，不代表對未來市場走勢的預測。</li>
            <li>金融市場投資具有極高風險，投資人進行任何投資決策前，應依據自身財務狀況與風險承受度，獨立進行思考與評估。</li>
            <li>使用者因參考本站任何資訊所進行之投資行為，其產生之所有利益、損失或衍生之民刑事法律責任，均須由使用者<strong>自行判斷並自負盈虧</strong>，本站及開發團隊不承擔任何直接或間接之法律與損害賠償責任。</li>
          </ol>
          <p>一旦您進入、瀏覽或使用本站，即視為您已充分閱讀、理解並完全同意本免責聲明之所有內容。如果您不同意上述條款，請立即離開本站。</p>
        </div>

        <footer>
          <label>
            <input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />
            <span>我已完整閱讀、理解並同意以上免責聲明與使用者條款。</span>
          </label>
          <div>
            {!previouslyAccepted && <button className="secondary" type="button" onClick={leave}>不同意並離開本站</button>}
            <button className="primary" type="button" disabled={!checked} onClick={accept}>同意並進入網站</button>
          </div>
        </footer>
      </section>
    </div>
  );
}

export function LegalTermsButton() {
  return <button className="legal-terms-button" type="button" onClick={openLegalTerms}>免責聲明與使用者條款</button>;
}
