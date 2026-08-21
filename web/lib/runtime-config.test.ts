import { describe, expect, it } from "vitest";
import { resolveRuntimeConfig, RuntimeConfigurationError } from "./runtime-config";

describe("local / Railway runtime configuration", () => {
  it("uses the local FastAPI default outside Railway", () => {
    expect(resolveRuntimeConfig({ APP_RUNTIME_MODE: "local" })).toMatchObject({
      mode: "local",
      backendBaseUrl: "http://127.0.0.1:8000",
      scannerWorkerUrl: null,
    });
  });

  it("auto-detects Railway and uses its configured private backend", () => {
    expect(resolveRuntimeConfig({
      APP_RUNTIME_MODE: "auto",
      RAILWAY_ENVIRONMENT_NAME: "production",
      FASTAPI_URL: "http://backend.railway.internal:8080/",
    })).toEqual({
      mode: "railway",
      backendBaseUrl: "http://backend.railway.internal:8080",
      scannerWorkerUrl: null,
      railwayEnvironment: "production",
    });
  });

  it("requires an explicit non-local backend in Railway mode", () => {
    expect(() => resolveRuntimeConfig({ APP_RUNTIME_MODE: "railway" }))
      .toThrow(RuntimeConfigurationError);
    expect(() => resolveRuntimeConfig({
      APP_RUNTIME_MODE: "railway",
      FASTAPI_URL: "http://127.0.0.1:8000",
    })).toThrow("不可指向 localhost");
  });

  it("rejects invalid mode names and malformed service URLs", () => {
    expect(() => resolveRuntimeConfig({ APP_RUNTIME_MODE: "production" }))
      .toThrow("local、railway 或 auto");
    expect(() => resolveRuntimeConfig({ APP_RUNTIME_MODE: "local", FASTAPI_URL: "backend:8000" }))
      .toThrow("有效的 http(s) 網址");
  });
});
