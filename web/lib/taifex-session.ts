export type TaifexSession = "day" | "night" | "closed";
export type TaifexQuoteFeed = "day" | "night";

export interface TaifexSessionState {
  session: TaifexSession;
  preferredFeed: TaifexQuoteFeed;
  open: boolean;
}

function taipeiClock(now: Date) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Taipei",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(now);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? "";
  const weekday = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].indexOf(get("weekday"));
  return {
    weekday,
    minutes: Number(get("hour")) * 60 + Number(get("minute")),
  };
}

/**
 * 臺股期貨一般交易為週一至週五 08:45–13:45；
 * 盤後交易為週一至週五 15:00–翌日 05:00。
 * 休市日最後仍由官方端點的 isNoData 再確認。
 */
export function getTaifexSessionState(now = new Date()): TaifexSessionState {
  const { weekday, minutes } = taipeiClock(now);
  const weekdayDay = weekday >= 1 && weekday <= 5;
  const weekdayNightStart = weekdayDay && minutes >= 15 * 60;
  const followingMorning = weekday >= 2 && weekday <= 6 && minutes < 5 * 60;
  const dayOpen = weekdayDay && minutes >= 8 * 60 + 45 && minutes <= 13 * 60 + 45;
  const nightOpen = weekdayNightStart || followingMorning;

  if (dayOpen) return { session: "day", preferredFeed: "day", open: true };
  if (nightOpen) return { session: "night", preferredFeed: "night", open: true };

  // 午盤結束至夜盤開盤前，以最近的日盤為準；其餘時段保留最近夜盤。
  const preferredFeed: TaifexQuoteFeed =
    weekdayDay && minutes > 13 * 60 + 45 && minutes < 15 * 60 ? "day" : "night";
  return { session: "closed", preferredFeed, open: false };
}
