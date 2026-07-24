CREATE TABLE IF NOT EXISTS system_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  symbol TEXT,
  strategy_id TEXT,
  old_value TEXT,
  new_value TEXT,
  price REAL,
  score REAL,
  triggered_at TEXT NOT NULL,
  reasons_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_events_triggered_at
ON system_events(triggered_at DESC);

CREATE TABLE IF NOT EXISTS screening_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market_direction TEXT NOT NULL,
  active_strategies_json TEXT NOT NULL,
  rankings_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  added_at TEXT NOT NULL,
  added_price REAL NOT NULL,
  added_score REAL NOT NULL,
  original_robot_id TEXT NOT NULL,
  original_robot_name TEXT NOT NULL,
  original_reasons_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_user_added
ON watchlist_items(user_id, added_at DESC);

CREATE TABLE IF NOT EXISTS holding_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name TEXT NOT NULL,
  cost REAL NOT NULL CHECK(cost > 0),
  lots REAL NOT NULL CHECK(lots > 0),
  buy_date TEXT NOT NULL,
  added_at TEXT NOT NULL,
  original_selected_at TEXT NOT NULL,
  original_selected_price REAL NOT NULL,
  original_ai_score REAL NOT NULL,
  original_robot_id TEXT NOT NULL,
  original_robot_name TEXT NOT NULL,
  original_reasons_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(user_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_holdings_user_added
ON holding_items(user_id, added_at DESC);

CREATE TABLE IF NOT EXISTS ai_score_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  list_type TEXT NOT NULL CHECK(list_type IN ('watchlist', 'holding')),
  score REAL NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(user_id, symbol, list_type, recorded_at)
);

CREATE INDEX IF NOT EXISTS idx_score_history_lookup
ON ai_score_history(user_id, symbol, list_type, recorded_at DESC);
