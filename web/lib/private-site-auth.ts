const encoder = new TextEncoder();

export const PRIVATE_SITE_COOKIE = "moneymoney-private-session";
export const PRIVATE_SITE_SESSION_SECONDS = 12 * 60 * 60;

function sessionSecret(): string {
  return process.env.PRIVATE_SITE_SESSION_SECRET ?? "local-development-secret-change-before-production";
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

async function signature(value: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(sessionSecret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signed = await crypto.subtle.sign("HMAC", key, encoder.encode(value));
  return toBase64Url(new Uint8Array(signed));
}

function constantTimeEqual(left: string, right: string): boolean {
  const length = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

export async function secureCredentialEqual(actual: string, expected: string): Promise<boolean> {
  const [actualHash, expectedHash] = await Promise.all([
    crypto.subtle.digest("SHA-256", encoder.encode(actual)),
    crypto.subtle.digest("SHA-256", encoder.encode(expected)),
  ]);
  return constantTimeEqual(
    toBase64Url(new Uint8Array(actualHash)),
    toBase64Url(new Uint8Array(expectedHash)),
  );
}

export async function verifyAdaptiveScannerToken(token: string | null | undefined): Promise<boolean> {
  const expected = process.env.ADAPTIVE_ELECTRONIC_SCANNER_TOKEN;
  if (!token || !expected || expected.length < 32) return false;
  return secureCredentialEqual(token, expected);
}

export async function createPrivateSiteSession(username: string, now = Date.now()): Promise<string> {
  const expiresAt = Math.floor(now / 1000) + PRIVATE_SITE_SESSION_SECONDS;
  const payload = `${username}.${expiresAt}`;
  return `${payload}.${await signature(payload)}`;
}

export async function verifyPrivateSiteSession(token: string | undefined, now = Date.now()): Promise<boolean> {
  if (!token) return false;
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  const [username, expiresAtText, providedSignature] = parts;
  const expiresAt = Number(expiresAtText);
  if (!username || !Number.isFinite(expiresAt) || expiresAt <= Math.floor(now / 1000)) return false;
  const expectedUsername = process.env.PRIVATE_SITE_USERNAME ?? "admin";
  if (!constantTimeEqual(username, expectedUsername)) return false;
  const expectedSignature = await signature(`${username}.${expiresAtText}`);
  return constantTimeEqual(providedSignature, expectedSignature);
}
