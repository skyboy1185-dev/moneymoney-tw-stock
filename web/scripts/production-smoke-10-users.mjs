#!/usr/bin/env node

const baseUrl = (process.env.SMOKE_BASE_URL ?? "").replace(/\/+$/, "");
const username = process.env.SMOKE_USERNAME ?? "";
const password = process.env.SMOKE_PASSWORD ?? "";
const userCount = positiveInt(process.env.SMOKE_USERS, 10);
const requestTimeoutMs = positiveInt(process.env.SMOKE_REQUEST_TIMEOUT_MS, 15_000);
const sseTimeoutMs = positiveInt(process.env.SMOKE_SSE_TIMEOUT_MS, 15_000);
const jsonP95LimitMs = positiveInt(process.env.SMOKE_JSON_P95_LIMIT_MS, 8_000);
const longP95LimitMs = positiveInt(process.env.SMOKE_LONG_P95_LIMIT_MS, 15_000);

if (!baseUrl || !username || !password) {
  console.error("Missing required env: SMOKE_BASE_URL, SMOKE_USERNAME, SMOKE_PASSWORD");
  process.exit(2);
}

const pageChecks = [
  { name: "page:home", path: "/?symbol=2330" },
  { name: "page:day-trading-bot", path: "/day-trading-bot" },
  { name: "page:pattern-robot", path: "/pattern-robot" },
];

const jsonChecks = [
  { name: "api:runtime", path: "/api/runtime" },
  { name: "api:stocks-search", path: "/api/stocks?q=2330", long: true },
  { name: "api:chip-flow-top10", path: (user) => `/api/chip-flow/electronic-alerts?clientId=${encodeURIComponent(user.clientId)}` },
  { name: "api:market-index-defense", path: "/api/market-index/defense", long: true },
  { name: "api:quotes", path: "/api/market-data/quotes", method: "POST", body: { items: [] } },
  { name: "api:daytrading-regime", path: "/api/day-trading/market-regime" },
  { name: "api:daytrading-signals", path: "/api/day-trading/signals" },
  { name: "api:daytrading-signals-today", path: "/api/day-trading/signals/today" },
  { name: "api:daytrading-rankings", path: "/api/day-trading/rankings" },
  { name: "api:daytrading-replay", path: "/api/day-trading/candidate-replay/today" },
  { name: "api:daytrading-positions", path: "/api/day-trading/positions" },
  { name: "api:daytrading-alerts", path: "/api/day-trading/alerts" },
  { name: "api:daytrading-performance", path: "/api/day-trading/performance" },
  { name: "api:limitup-status", path: "/api/limit-up-ai/status" },
  { name: "api:limitup-dashboard", path: "/api/limit-up-ai/dashboard", long: true },
  { name: "api:limitup-replay", path: "/api/limit-up-ai/replay/today", long: true },
  { name: "api:limitup-notifications", path: "/api/limit-up-ai/notifications" },
  { name: "api:adaptive-status", path: "/api/adaptive-electronic/status" },
  { name: "api:adaptive-candidates", path: "/api/adaptive-electronic/candidates?minimumScore=75" },
  { name: "api:adaptive-performance", path: "/api/adaptive-electronic/performance" },
  { name: "api:adaptive-notifications", path: "/api/adaptive-electronic/notifications?source=SUPER_AI_DAYTRADE&limit=20" },
  { name: "api:rocket-dashboard", path: "/api/rocket-radar/dashboard", long: true },
  { name: "api:rocket-notifications", path: "/api/rocket-radar/notifications?period=today&limit=20" },
  { name: "api:pattern-status", path: "/api/pattern-robot/status" },
  { name: "api:pattern-detections", path: "/api/pattern-robot/detections?pageSize=300", long: true },
  { name: "api:pattern-performance", path: "/api/pattern-robot/performance" },
  { name: "api:pattern-orders", path: "/api/pattern-robot/orders?pageSize=50" },
  { name: "api:longterm-events-long-only", path: "/api/long-term/events?mode=long_only&afterId=0&limit=20" },
  { name: "api:longterm-events-focused", path: "/api/long-term/events?mode=focused_long&afterId=0&limit=20" },
];

const sseChecks = [
  { name: "sse:market-stream", path: "/api/stream?auto=1" },
  { name: "sse:daytrading-stream", path: "/api/day-trading/stream" },
];

const users = Array.from({ length: userCount }, (_, index) => ({
  index: index + 1,
  userId: `smoke-user-${index + 1}`,
  clientId: `smoke-${Date.now()}-${index + 1}`,
}));

const allResults = [];
const startedAt = Date.now();

console.log(`Production smoke started: users=${userCount}, base=${baseUrl}`);

await Promise.all(users.map(async (user) => {
  const login = await loginUser(user);
  allResults.push(login);
  if (!login.ok || !login.cookie) return;

  for (const check of pageChecks) {
    allResults.push(await httpCheck(user, login.cookie, check, "page"));
  }
  for (const check of jsonChecks) {
    allResults.push(await httpCheck(user, login.cookie, check, check.long ? "long-json" : "json"));
  }
  for (const check of sseChecks) {
    allResults.push(await sseCheck(user, login.cookie, check));
  }
}));

const elapsedMs = Date.now() - startedAt;
const failures = allResults.filter((result) => !result.ok);
const grouped = groupByName(allResults);

console.log("");
console.log("Endpoint summary");
for (const [name, results] of grouped) {
  const latencies = results.filter((result) => result.ok).map((result) => result.ms);
  const failed = results.length - latencies.length;
  const p95 = latencies.length ? percentile(latencies, 95) : 0;
  const statusText = failed ? "FAIL" : "OK";
  console.log(`${statusText} ${name} count=${results.length} failed=${failed} p95=${Math.round(p95)}ms`);
  for (const failure of results.filter((result) => !result.ok).slice(0, 3)) {
    console.log(`  user=${failure.user} status=${failure.status ?? "n/a"} error=${failure.error}`);
  }
}

const jsonLatencies = allResults
  .filter((result) => result.ok && (result.kind === "json" || result.kind === "page"))
  .map((result) => result.ms);
const longLatencies = allResults
  .filter((result) => result.ok && (result.kind === "long-json" || result.kind === "sse"))
  .map((result) => result.ms);
const jsonP95 = jsonLatencies.length ? percentile(jsonLatencies, 95) : 0;
const longP95 = longLatencies.length ? percentile(longLatencies, 95) : 0;

console.log("");
console.log(`Total: checks=${allResults.length}, failures=${failures.length}, elapsed=${Math.round(elapsedMs)}ms`);
console.log(`Latency: json/page p95=${Math.round(jsonP95)}ms limit=${jsonP95LimitMs}ms; long/sse p95=${Math.round(longP95)}ms limit=${longP95LimitMs}ms`);

if (failures.length || jsonP95 > jsonP95LimitMs || longP95 > longP95LimitMs) {
  process.exit(1);
}

console.log("Production smoke passed.");

async function loginUser(user) {
  return timed("auth:login", user, "json", async (signal) => {
    const response = await fetch(`${baseUrl}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
      signal,
    });
    const cookie = extractCookie(response);
    if (!response.ok) throw httpError(response, "login failed");
    if (!cookie) throw new Error("login succeeded but no session cookie was returned");
    return { cookie, status: response.status };
  });
}

async function httpCheck(user, cookie, check, kind) {
  return timed(check.name, user, kind, async (signal) => {
    const path = typeof check.path === "function" ? check.path(user) : check.path;
    const headers = {
      Accept: kind === "page" ? "text/html,*/*" : "application/json",
      Cookie: cookie,
      "x-user-id": user.userId,
    };
    if (check.method === "POST") headers["Content-Type"] = "application/json";
    const response = await fetch(`${baseUrl}${path}`, {
      method: check.method ?? "GET",
      headers,
      body: check.body ? JSON.stringify(check.body) : undefined,
      signal,
    });
    const text = await response.text().catch(() => "");
    if (!response.ok) throw httpError(response, text.slice(0, 180));
    if (kind !== "page" && text) JSON.parse(text);
    return { status: response.status };
  }, check.long ? requestTimeoutMs : Math.min(requestTimeoutMs, 12_000));
}

async function sseCheck(user, cookie, check) {
  return timed(check.name, user, "sse", async (signal) => {
    const response = await fetch(`${baseUrl}${check.path}`, {
      headers: {
        Accept: "text/event-stream",
        Cookie: cookie,
        "x-user-id": user.userId,
      },
      signal,
    });
    if (!response.ok) throw httpError(response, "stream failed");
    if (!response.body) throw new Error("stream response has no body");
    const reader = response.body.getReader();
    try {
      const { value } = await reader.read();
      if (!value || value.length === 0) throw new Error("stream opened but no data was received");
      return { status: response.status };
    } finally {
      await reader.cancel().catch(() => undefined);
    }
  }, sseTimeoutMs);
}

async function timed(name, user, kind, callback, timeoutMs = requestTimeoutMs) {
  const controller = new AbortController();
  const started = performance.now();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const value = await callback(controller.signal);
    return {
      ok: true,
      name,
      kind,
      user: user.index,
      ms: performance.now() - started,
      status: value?.status,
      cookie: value?.cookie,
    };
  } catch (error) {
    return {
      ok: false,
      name,
      kind,
      user: user.index,
      ms: performance.now() - started,
      status: error?.status,
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    clearTimeout(timeout);
    controller.abort();
  }
}

function extractCookie(response) {
  const setCookie = response.headers.get("set-cookie");
  if (!setCookie) return "";
  return setCookie.split(",")
    .map((cookie) => cookie.split(";")[0].trim())
    .filter(Boolean)
    .join("; ");
}

function httpError(response, fallback) {
  const error = new Error(`HTTP ${response.status}: ${fallback}`);
  error.status = response.status;
  return error;
}

function groupByName(results) {
  const grouped = new Map();
  for (const result of results) {
    const items = grouped.get(result.name) ?? [];
    items.push(result);
    grouped.set(result.name, items);
  }
  return grouped;
}

function percentile(values, p) {
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(sorted.length - 1, index))];
}

function positiveInt(value, fallback) {
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : fallback;
}
