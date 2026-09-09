import { describe, expect, it, vi } from "vitest";
import { AsyncValueCache } from "./async-value-cache";

describe("history cache", () => {
  it("shares concurrent loads, expires and separates trading days", async () => {
    let now = 0;
    const cache = new AsyncValueCache<number>(100, 2, () => now);
    const load = vi.fn(async () => 42);
    expect(await Promise.all([cache.get("day1", load), cache.get("day1", load)])).toEqual([42, 42]);
    await cache.get("day1", load);
    expect(load).toHaveBeenCalledTimes(1);
    await cache.get("day2", load);
    now = 101;
    await cache.get("day1", load);
    expect(load).toHaveBeenCalledTimes(3);
  });
  it("does not cache failures or serve expired data on error", async () => {
    let now = 0;
    const cache = new AsyncValueCache<number>(100, 2, () => now);
    await cache.get("stock", async () => 42);
    now = 101;
    const failed = vi.fn(async () => { throw new Error("offline"); });
    await expect(cache.get("stock", failed)).rejects.toThrow("offline");
    await expect(cache.get("stock", failed)).rejects.toThrow("offline");
    expect(failed).toHaveBeenCalledTimes(2);
    expect(await cache.get("stock", async () => 43)).toBe(43);
  });
  it("bounds retained entries", async () => {
    const cache = new AsyncValueCache<number>(100, 1, () => 0);
    const load = vi.fn(async () => 1);
    await cache.get("a", load);
    await cache.get("b", load);
    await cache.get("a", load);
    expect(load).toHaveBeenCalledTimes(3);
  });
});
