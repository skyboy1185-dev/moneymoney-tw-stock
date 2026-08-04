import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  createPrivateSiteSession,
  PRIVATE_SITE_SESSION_SECONDS,
  secureCredentialEqual,
  verifyAdaptiveScannerToken,
  verifyPrivateSiteSession,
} from "./private-site-auth";

const previousUsername = process.env.PRIVATE_SITE_USERNAME;
const previousSecret = process.env.PRIVATE_SITE_SESSION_SECRET;
const previousScannerToken = process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN;

describe("private site authentication", () => {
  beforeAll(() => {
    process.env.PRIVATE_SITE_USERNAME = "admin";
    process.env.PRIVATE_SITE_SESSION_SECRET = "test-secret-with-enough-entropy";
    process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN = "test-adaptive-scanner-token-with-32-characters";
  });

  afterAll(() => {
    if (previousUsername === undefined) delete process.env.PRIVATE_SITE_USERNAME;
    else process.env.PRIVATE_SITE_USERNAME = previousUsername;
    if (previousSecret === undefined) delete process.env.PRIVATE_SITE_SESSION_SECRET;
    else process.env.PRIVATE_SITE_SESSION_SECRET = previousSecret;
    if (previousScannerToken === undefined) delete process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN;
    else process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN = previousScannerToken;
  });

  it("accepts a valid signed session", async () => {
    const now = Date.UTC(2026, 7, 3, 9, 0);
    const token = await createPrivateSiteSession("admin", now);
    await expect(verifyPrivateSiteSession(token, now + 1_000)).resolves.toBe(true);
  });

  it("rejects expired and tampered sessions", async () => {
    const now = Date.UTC(2026, 7, 3, 9, 0);
    const token = await createPrivateSiteSession("admin", now);
    await expect(
      verifyPrivateSiteSession(token, now + (PRIVATE_SITE_SESSION_SECONDS + 1) * 1_000),
    ).resolves.toBe(false);
    await expect(verifyPrivateSiteSession(`${token}tampered`, now)).resolves.toBe(false);
  });

  it("compares credentials without exposing the expected value", async () => {
    await expect(secureCredentialEqual("111", "111")).resolves.toBe(true);
    await expect(secureCredentialEqual("112", "111")).resolves.toBe(false);
  });

  it("accepts only the private adaptive scanner service token", async () => {
    await expect(verifyAdaptiveScannerToken("test-adaptive-scanner-token-with-32-characters"))
      .resolves.toBe(true);
    await expect(verifyAdaptiveScannerToken("wrong-token")).resolves.toBe(false);
    await expect(verifyAdaptiveScannerToken(undefined)).resolves.toBe(false);
  });
});
