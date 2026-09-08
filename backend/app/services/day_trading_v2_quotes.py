"""Pure quote validation shared by V2 entry gates and status projections."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .quote_quality import quote_time, trusted_quote


TAIPEI = ZoneInfo("Asia/Taipei")
QUOTE_NOTICES = frozenset({
    "行情中斷或延遲，已禁止建立新部位",
    "行情來源尚未就緒，開盤後會持續重試並禁止新交易",
})


def fresh_quote(quote, now: datetime, timeout: int) -> bool:
    return trusted_quote(quote, now, timeout)


def quote_health(quotes: dict, now: datetime, timeout: int, diagnostics: dict | None = None) -> dict:
    diagnostics = diagnostics or {}
    fresh = sum(fresh_quote(quote, now, timeout) for quote in quotes.values())
    tracked = max(len(quotes), int(diagnostics.get("trackedCount") or 0))
    result = {"observedAt": now.isoformat(), "lastReceivedAt": diagnostics.get("lastReceivedAt"),
            "trackedCount": tracked, "freshCount": fresh, "staleCount": tracked - fresh,
            "overCapacity": diagnostics.get("overCapacity", False)}
    # Public projection is an explicit allowlist: never expose adapter errors,
    # endpoint URLs, headers, or credentials from its internal diagnostics.
    modes = {"shadow", "primary", "degraded", "mis_only"}
    sources = {"FUGLE_WS", "FUGLE_REST", "TWSE_MIS", "MIXED", "NONE"}
    if diagnostics.get("providerMode") in modes:
        result["providerMode"] = diagnostics["providerMode"]
    if diagnostics.get("activeSource") in sources:
        result["activeSource"] = diagnostics["activeSource"]
    if isinstance(diagnostics.get("ready"), bool):
        result["ready"] = diagnostics["ready"]
    if isinstance(diagnostics.get("entitlementReady"), bool):
        result["entitlementReady"] = diagnostics["entitlementReady"]
    if diagnostics.get("providerMode") == "shadow":
        result["entitlementReason"] = "備援驗證中，尚未啟用 Fugle 正式報價"
    elif diagnostics.get("entitlementReason"):
        result["entitlementReason"] = "Fugle 方案或訂閱權限尚未就緒"
    elif "entitlementReason" in diagnostics:
        result["entitlementReason"] = None
    for key in ("sourceSwitchCount", "fugleShadowFreshCount"):
        if isinstance(diagnostics.get(key), int) and diagnostics[key] >= 0:
            result[key] = diagnostics[key]
    for key in ("lastSourceSwitchAt", "fugleShadowObservedAt"):
        raw = diagnostics.get(key)
        if raw is None:
            continue
        try:
            result[key] = datetime.fromisoformat(str(raw)).isoformat()
        except (TypeError, ValueError):
            pass
    health = diagnostics.get("sourceHealth")
    if isinstance(health, dict):
        if "entitlementReady" not in result and isinstance(health.get("entitlementReady"), bool):
            result["entitlementReady"] = health["entitlementReady"]
        public = {}
        for key in ("wsConnected", "entitlementReady", "overCapacity"):
            if isinstance(health.get(key), bool):
                public[key] = health[key]
        for key in ("wsSubscriptionLimit", "acknowledgedCount", "desiredCount", "pendingCount", "reconnectCount"):
            if isinstance(health.get(key), int) and health[key] >= 0:
                public[key] = health[key]
        for key in ("lastWsReceivedAt", "lastRestReceivedAt", "lastTradeAt"):
            try:
                public[key] = datetime.fromisoformat(str(health.get(key))).isoformat()
            except (TypeError, ValueError):
                pass
        result["sourceHealth"] = public
        for source, target in (("acknowledgedCount", "subscriptionCount"), ("wsSubscriptionLimit", "subscriptionLimit"), ("reconnectCount", "reconnectAttempts")):
            if source in public:
                result[target] = public[source]
    return result


def update_quote_notice(runtime, *, healthy: bool, active: bool) -> None:
    if healthy:
        if runtime.latest_error in QUOTE_NOTICES:
            runtime.latest_error = ""
    elif active and (not runtime.latest_error or runtime.latest_error in QUOTE_NOTICES):
        runtime.latest_error = "行情中斷或延遲，已禁止建立新部位"
