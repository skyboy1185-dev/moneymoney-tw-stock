import { describe, expect, it } from "vitest";
import { getTaifexSessionState } from "./taifex-session";

const taipeiTime = (value: string) => new Date(`${value}+08:00`);

describe("getTaifexSessionState", () => {
  it("辨識平日日盤", () => {
    expect(getTaifexSessionState(taipeiTime("2026-07-24T10:00:00"))).toEqual({
      session: "day", preferredFeed: "day", open: true,
    });
  });

  it("辨識平日夜盤", () => {
    expect(getTaifexSessionState(taipeiTime("2026-07-24T16:00:00"))).toEqual({
      session: "night", preferredFeed: "night", open: true,
    });
  });

  it("辨識跨日夜盤", () => {
    expect(getTaifexSessionState(taipeiTime("2026-07-25T03:30:00"))).toEqual({
      session: "night", preferredFeed: "night", open: true,
    });
  });

  it("週六清晨收盤後不再標示交易中", () => {
    expect(getTaifexSessionState(taipeiTime("2026-07-25T06:00:00"))).toEqual({
      session: "closed", preferredFeed: "night", open: false,
    });
  });
});
