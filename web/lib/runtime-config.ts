export type AppRuntimeMode = "local" | "railway";

export interface AppRuntimeConfig {
  mode: AppRuntimeMode;
  backendBaseUrl: string;
  scannerWorkerUrl: string | null;
  railwayEnvironment: string | null;
}

export class RuntimeConfigurationError extends Error {}

type RuntimeEnvironment = Readonly<Record<string, string | undefined>>;

function explicitMode(env: RuntimeEnvironment): AppRuntimeMode | "auto" {
  const value = env.APP_RUNTIME_MODE?.trim().toLowerCase() || "auto";
  if (value === "auto" || value === "local" || value === "railway") return value;
  throw new RuntimeConfigurationError("APP_RUNTIME_MODE 僅能設定為 local、railway 或 auto");
}

function validBaseUrl(value: string, variableName: string): string {
  try {
    const url = new URL(value);
    if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("invalid protocol");
    return url.toString().replace(/\/$/, "");
  } catch {
    throw new RuntimeConfigurationError(`${variableName} 必須是有效的 http(s) 網址`);
  }
}

export function resolveRuntimeConfig(env: RuntimeEnvironment = process.env): AppRuntimeConfig {
  const configuredMode = explicitMode(env);
  const railwayDetected = Boolean(
    env.RAILWAY_ENVIRONMENT
    || env.RAILWAY_ENVIRONMENT_NAME
    || env.RAILWAY_SERVICE_ID,
  );
  const mode: AppRuntimeMode = configuredMode === "auto"
    ? railwayDetected ? "railway" : "local"
    : configuredMode;
  const configuredBackend = env.FASTAPI_URL?.trim();
  if (mode === "railway" && !configuredBackend) {
    throw new RuntimeConfigurationError("Railway 模式必須設定 FASTAPI_URL");
  }
  const backendBaseUrl = validBaseUrl(
    configuredBackend || "http://127.0.0.1:8000",
    "FASTAPI_URL",
  );
  const backendHostname = new URL(backendBaseUrl).hostname;
  if (mode === "railway" && ["localhost", "127.0.0.1", "::1"].includes(backendHostname)) {
    throw new RuntimeConfigurationError("Railway 模式的 FASTAPI_URL 不可指向 localhost");
  }
  const configuredWorker = env.SCANNER_WORKER_URL?.trim();
  return {
    mode,
    backendBaseUrl,
    scannerWorkerUrl: configuredWorker ? validBaseUrl(configuredWorker, "SCANNER_WORKER_URL") : null,
    railwayEnvironment: env.RAILWAY_ENVIRONMENT_NAME?.trim() || env.RAILWAY_ENVIRONMENT?.trim() || null,
  };
}

export function getBackendBaseUrl(): string {
  return resolveRuntimeConfig().backendBaseUrl;
}

export function getScannerWorkerUrl(): string | null {
  return resolveRuntimeConfig().scannerWorkerUrl;
}
