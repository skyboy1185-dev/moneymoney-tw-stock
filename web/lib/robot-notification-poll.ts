import { todayTaipeiDate, type TodayRobotNotificationPayloads, type TodayRobotSource } from "@/lib/today-robot-notifications";

export const NOTIFICATION_FEEDS = ["dayTradingSignals", "dayTradingAlerts", "limitUp", "superAi", "pattern", "rocket", "dayTradingV2"] as const;
type Feed = typeof NOTIFICATION_FEEDS[number];
export type FeedStatus = "current" | "stale" | "unavailable" | "waiting";
interface FeedSnapshot { status: FeedStatus; lastSuccessAt: string | null }
export interface NotificationPollSnapshot {
  ownerId: string;
  payloads: TodayRobotNotificationPayloads;
  feeds: Partial<Record<Feed, FeedSnapshot>>;
  attemptedAt: string | null;
}
export function emptyNotificationPoll(ownerId: string): NotificationPollSnapshot {
  return { ownerId, payloads: { dayTradingV2UserId: ownerId }, feeds: {}, attemptedAt: null };
}

/** Retain a successful feed independently when another request fails; never reuse a different user's cache. */
export function mergeNotificationPoll(previous: NotificationPollSnapshot, ownerId: string, results: PromiseSettledResult<unknown>[], now: string): NotificationPollSnapshot {
  const base = previous.ownerId === ownerId ? previous : emptyNotificationPoll(ownerId);
  const payloads = { ...base.payloads };
  const feeds = { ...base.feeds };
  NOTIFICATION_FEEDS.forEach((feed, index) => {
    const result = results[index];
    if (result?.status === "fulfilled") {
      payloads[feed] = result.value;
      feeds[feed] = { status: "current", lastSuccessAt: now };
    } else {
      const lastSuccessAt = base.feeds[feed]?.lastSuccessAt ?? null;
      feeds[feed] = { status: lastSuccessAt ? "stale" : "unavailable", lastSuccessAt };
    }
  });
  return { ownerId, payloads, feeds, attemptedAt: now };
}

const SOURCE_FEEDS: Record<TodayRobotSource, Feed[]> = {
  "day-trading": ["dayTradingSignals", "dayTradingAlerts"], "limit-up-ai": ["limitUp"],
  "adaptive-electronic": ["superAi"], "pattern-robot": ["pattern"], "rocket-radar": ["rocket"], "day-trading-v2": ["dayTradingV2"],
};
export function notificationSourceState(snapshot: NotificationPollSnapshot, source: TodayRobotSource, today = todayTaipeiDate()): { status: FeedStatus; hasTodayData: boolean } {
  const feeds = SOURCE_FEEDS[source].map((feed) => snapshot.feeds[feed]);
  const hasTodayData = feeds.some((feed) => feed?.lastSuccessAt && todayTaipeiDate(new Date(feed.lastSuccessAt)) === today);
  const failed = feeds.some((feed) => feed?.status === "stale" || feed?.status === "unavailable");
  return {
    status: failed ? hasTodayData ? "stale" : "unavailable" : feeds.every((feed) => feed?.status === "current") ? "current" : "waiting",
    hasTodayData,
  };
}
