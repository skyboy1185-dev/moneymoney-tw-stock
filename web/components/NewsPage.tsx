"use client";

import { useCallback, useEffect, useState } from "react";
import { Clock3, Newspaper, Search, Sparkles } from "lucide-react";
import type { NewsItem } from "@/services/content-service";

const sentimentLabel = { positive: "偏正向", neutral: "中性", negative: "需留意" };

export function NewsPage({ onSelectStock }: { onSelectStock: (symbol: string) => void }) {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [keyword, setKeyword] = useState("");
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async (nextCategory = category, nextKeyword = keyword) => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ category: nextCategory, keyword: nextKeyword });
      const response = await fetch(`/api/news?${query}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error ?? "新聞載入失敗");
      setItems(payload.items ?? []);
      setCategories(payload.categories ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "新聞載入失敗");
    } finally {
      setLoading(false);
    }
  }, [category, keyword]);

  useEffect(() => { void load("", ""); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const selectCategory = (value: string) => {
    setCategory(value);
    void load(value, keyword);
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    setKeyword(draft.trim());
    void load(category, draft.trim());
  };

  return (
    <div className="content-page">
      <div className="content-page-heading">
        <div><p className="section-kicker">MARKET STORIES</p><h1><Newspaper size={24} />市場新聞</h1><p>依產業與股票代號快速篩選；目前所有內容皆明確標示為展示新聞。</p></div>
        <span className="demo-badge"><Sparkles size={14} />展示新聞／非即時</span>
      </div>
      <div className="news-controls">
        <form className="content-search" onSubmit={submit}><Search size={15} /><input value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="搜尋標題、關鍵字或股票代號" /><button>搜尋</button></form>
        <div className="content-tabs"><button className={!category ? "active" : ""} onClick={() => selectCategory("")}>全部</button>{categories.map((item) => <button key={item} className={category === item ? "active" : ""} onClick={() => selectCategory(item)}>{item}</button>)}</div>
      </div>
      {error && <div className="error-banner">{error}</div>}
      {loading ? <div className="page-loading"><span className="spinner" /><p>正在整理市場新聞…</p></div>
        : !items.length ? <div className="empty-state"><Newspaper size={32} /><h2>沒有符合條件的新聞</h2><p>請嘗試其他關鍵字或分類。</p></div>
          : <div className="news-list">{items.map((item) => (
            <article className="news-card" key={item.id}>
              <div className="news-meta"><span>{item.category}</span><span className={`sentiment-${item.sentiment}`}>{sentimentLabel[item.sentiment]}</span><small><Clock3 size={11} />{new Date(item.publishedAt).toLocaleString("zh-TW", { hour12: false })}</small></div>
              <h2>{item.title}</h2>
              <p>{item.summary}</p>
              <div className="news-footer"><div>{item.symbols.map((symbol) => <button key={symbol} onClick={() => onSelectStock(symbol)}>{symbol} 查看分析</button>)}</div><span>{item.source}</span></div>
            </article>
          ))}</div>}
      <p className="content-disclaimer">新聞內容為 MVP 展示資料，不代表真實事件、即時資訊或任何投資建議。</p>
    </div>
  );
}
