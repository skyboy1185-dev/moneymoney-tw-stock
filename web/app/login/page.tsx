"use client";

import { FormEvent, useState } from "react";
import { BarChart3, KeyRound, LockKeyhole, UserRound } from "lucide-react";

function safeDestination(): string {
  const value = new URLSearchParams(window.location.search).get("next") ?? "/";
  return value.startsWith("/") && !value.startsWith("//") ? value : "/";
}
export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error ?? "登入失敗。");
      window.location.replace(safeDestination());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "登入失敗，請稍後再試。");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="private-login-page">
      <section className="private-login-card">
        <header>
          <span><BarChart3 size={24} /></span>
          <div><strong>Moneymoney</strong><small>PRIVATE ACCESS</small></div>
        </header>
        <div className="private-login-heading">
          <LockKeyhole size={26} />
          <h1>非公開網站</h1>
          <p>此系統僅供授權使用者進入，請輸入帳號與密碼。</p>
        </div>
        <form onSubmit={submit}>
          <label><span><UserRound size={15} />帳號</span><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required autoFocus /></label>
          <label><span><KeyRound size={15} />密碼</span><input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
          {error && <p className="private-login-error" role="alert">{error}</p>}
          <button type="submit" disabled={loading}>{loading ? "驗證中…" : "登入系統"}</button>
        </form>
        <footer>未經授權請勿嘗試存取。本網站內容僅供研究、測試與個人作品展示。</footer>
      </section>
    </main>
  );
}
