import { DatabaseSync } from "node:sqlite";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";
import type { SystemEvent } from "@/lib/market-types";

let database: DatabaseSync | null = null;

export function getDatabase() {
  if (database) return database;
  const dataDirectory = path.join(process.cwd(), "data");
  mkdirSync(dataDirectory, { recursive: true });
  database = new DatabaseSync(path.join(dataDirectory, "moneymoney.db"));
  database.exec(readFileSync(path.join(process.cwd(), "database", "schema.sql"), "utf8"));
  return database;
}

export function saveEvent(event: SystemEvent) {
  const dedupeKey = [event.type, event.symbol ?? "", event.strategyId ?? "", event.newValue ?? "", event.triggeredAt.slice(0, 16)].join(":");
  getDatabase().prepare(`
    INSERT OR IGNORE INTO system_events
    (dedupe_key, type, symbol, strategy_id, old_value, new_value, price, score, triggered_at, reasons_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    dedupeKey, event.type, event.symbol ?? null, event.strategyId ?? null,
    event.oldValue == null ? null : String(event.oldValue),
    event.newValue == null ? null : String(event.newValue),
    event.price ?? null, event.score ?? null, event.triggeredAt, JSON.stringify(event.reasons),
  );
}

export function recentEvents(limit = 20): SystemEvent[] {
  const rows = getDatabase().prepare(`
    SELECT type, symbol, strategy_id, old_value, new_value, price, score, triggered_at, reasons_json
    FROM system_events ORDER BY triggered_at DESC LIMIT ?
  `).all(limit) as Record<string, unknown>[];
  return rows.map((row) => ({
    type: String(row.type), symbol: row.symbol ? String(row.symbol) : undefined,
    strategyId: row.strategy_id ? String(row.strategy_id) : undefined,
    oldValue: row.old_value ? String(row.old_value) : undefined,
    newValue: row.new_value ? String(row.new_value) : undefined,
    price: row.price == null ? undefined : Number(row.price),
    score: row.score == null ? undefined : Number(row.score),
    triggeredAt: String(row.triggered_at),
    reasons: JSON.parse(String(row.reasons_json)),
  }));
}
