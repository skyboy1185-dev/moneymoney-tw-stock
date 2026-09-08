import { describe, expect, it } from "vitest";
import { emptyNotificationPoll, mergeNotificationPoll, notificationSourceState, NOTIFICATION_FEEDS } from "./robot-notification-poll";
import { normalizeTodayRobotNotifications } from "./today-robot-notifications";

const owner = "browser-user-one";
const now = "2026-09-07T02:00:00Z";
const failed = (): PromiseSettledResult<unknown>[] => NOTIFICATION_FEEDS.map(() => ({ status: "rejected", reason: new Error("503") }));
const success = (payload: unknown = { items: [] }): PromiseSettledResult<unknown> => ({ status: "fulfilled", value: payload });

describe("notification polling cache", () => {
  it("retains only the failed feed while replacing successful feeds", () => {
    const results = failed();
    const payload = { items: [{ id: "limit-1", createdAt: now, title: "買進提醒", isRead: false }] };
    results[2] = success(payload);
    results[4] = success({ items: [{ id: "pattern-1", createdAt: now, title: "掃描完成" }] });
    const first = mergeNotificationPoll(emptyNotificationPoll(owner), owner, results, now);
    const next = failed();
    next[4] = success();
    const second = mergeNotificationPoll(first, owner, next, "2026-09-07T02:00:30Z");
    expect(second.payloads.limitUp).toBe(payload);
    expect(second.payloads.pattern).toEqual({ items: [] });
    expect(notificationSourceState(second, "limit-up-ai", "2026-09-07")).toEqual({ status: "stale", hasTodayData: true });
    expect(notificationSourceState(second, "pattern-robot", "2026-09-07").status).toBe("current");
    expect(normalizeTodayRobotNotifications(second.payloads, "2026-09-07").map((item) => item.id)).toEqual(["limit-up-ai:limit-1"]);
  });
  it("shows unavailable on first failure, and clears stale state on successful empty recovery", () => {
    const first = mergeNotificationPoll(emptyNotificationPoll(owner), owner, failed(), now);
    expect(notificationSourceState(first, "day-trading-v2", "2026-09-07")).toEqual({ status: "unavailable", hasTodayData: false });
    const recovered = mergeNotificationPoll(first, owner, NOTIFICATION_FEEDS.map(() => success()), now);
    expect(notificationSourceState(recovered, "day-trading-v2", "2026-09-07")).toEqual({ status: "current", hasTodayData: true });
  });
  it("does not leak a previous user's successful payload into a new user's failure", () => {
    const results = failed();
    results[2] = success({ items: [{ id: "private" }] });
    const first = mergeNotificationPoll(emptyNotificationPoll(owner), owner, results, now);
    const second = mergeNotificationPoll(first, "browser-user-two", failed(), now);
    expect(second.payloads.limitUp).toBeUndefined();
    expect(second.payloads.dayTradingV2UserId).toBe("browser-user-two");
    expect(notificationSourceState(second, "limit-up-ai", "2026-09-07").status).toBe("unavailable");
  });
  it("does not claim yesterday's successful empty fetch establishes today's zero", () => {
    const first = mergeNotificationPoll(emptyNotificationPoll(owner), owner, NOTIFICATION_FEEDS.map(() => success()), now);
    const nextDay = mergeNotificationPoll(first, owner, failed(), "2026-09-07T16:01:00Z");
    expect(notificationSourceState(nextDay, "day-trading-v2", "2026-09-08")).toEqual({ status: "unavailable", hasTodayData: false });
  });
  it("treats legacy signals and alerts as one partially stale source", () => {
    const results = failed();
    results[0] = success();
    const snapshot = mergeNotificationPoll(emptyNotificationPoll(owner), owner, results, now);
    expect(notificationSourceState(snapshot, "day-trading", "2026-09-07")).toEqual({ status: "stale", hasTodayData: true });
  });
});
