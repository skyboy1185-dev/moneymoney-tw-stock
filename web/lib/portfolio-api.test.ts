import { describe, expect, it } from "vitest";
import { getUserId, validHoldingInput } from "./portfolio-api";

describe("portfolio API validation", () => {
  it("接受有效的持股成本、張數與日期", () => {
    expect(validHoldingInput({ symbol: "2330", cost: 2350, lots: 1.5, buyDate: "2026-07-24" })).toBe(true);
  });

  it("拒絕零成本、零張數與錯誤日期", () => {
    expect(validHoldingInput({ symbol: "2330", cost: 0, lots: 1, buyDate: "2026-07-24" })).toBe(false);
    expect(validHoldingInput({ symbol: "2330", cost: 2350, lots: 0, buyDate: "2026-07-24" })).toBe(false);
    expect(validHoldingInput({ symbol: "2330", cost: 2350, lots: 1, buyDate: "07/24/2026" })).toBe(false);
  });

  it("只接受格式安全的匿名使用者 ID", () => {
    expect(getUserId(new Request("http://localhost", { headers: { "x-user-id": "user-12345678" } }))).toBe("user-12345678");
    expect(getUserId(new Request("http://localhost", { headers: { "x-user-id": "../unsafe" } }))).toBeNull();
  });
});
