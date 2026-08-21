/* eslint-disable @typescript-eslint/no-require-imports */
const { spawn } = require("node:child_process");

const publicPort = process.env.PORT || "3000";
let stopping = false;
let scanner;

function childEnv(role, port, hostname) {
  return {
    ...process.env,
    APP_ROLE: role,
    PORT: port,
    HOSTNAME: hostname,
    SCANNER_WORKER_URL: "http://127.0.0.1:3001",
  };
}

function startScanner() {
  scanner = spawn(process.execPath, ["server.js"], {
    env: childEnv("scanner", "3001", "127.0.0.1"),
    stdio: "inherit",
  });
  scanner.on("exit", () => {
    if (!stopping) setTimeout(startScanner, 1_000);
  });
}

startScanner();
const web = spawn(process.execPath, ["server.js"], {
  env: childEnv("web", publicPort, "0.0.0.0"),
  stdio: "inherit",
});

function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  scanner?.kill(signal);
  web.kill(signal);
  setTimeout(() => process.exit(0), 5_000).unref();
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
web.on("exit", (code) => {
  if (!stopping) {
    stopping = true;
    scanner?.kill("SIGTERM");
    process.exit(code ?? 1);
  }
});
