PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT,
  adapter TEXT NOT NULL,
  active_url TEXT NOT NULL,
  url_field TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_status_code INTEGER,
  last_checked_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS raw_payloads (
  payload_hash TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  harvested_at TEXT NOT NULL,
  http_status INTEGER,
  content_type TEXT,
  payload_text TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS normalized_records (
  record_id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  harvested_at TEXT NOT NULL,
  published_at TEXT,
  canonical_url TEXT,
  title TEXT,
  content_raw TEXT,
  entities_json TEXT NOT NULL,
  observations_json TEXT NOT NULL,
  checksum TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (source_id) REFERENCES sources(source_id)
);

CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id TEXT NOT NULL,
  gate TEXT NOT NULL,
  severity TEXT NOT NULL,
  matched_terms_json TEXT,
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (record_id) REFERENCES normalized_records(record_id)
);

CREATE TABLE IF NOT EXISTS briefs (
  brief_id TEXT PRIMARY KEY,
  system_id TEXT NOT NULL,
  brief_date TEXT NOT NULL,
  title TEXT NOT NULL,
  markdown TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_raw_payloads_source_time
  ON raw_payloads(source_id, harvested_at);

CREATE INDEX IF NOT EXISTS idx_records_source_time
  ON normalized_records(source_id, harvested_at);

CREATE INDEX IF NOT EXISTS idx_records_published_at
  ON normalized_records(published_at);

CREATE INDEX IF NOT EXISTS idx_observations_record
  ON observations(record_id);

CREATE INDEX IF NOT EXISTS idx_briefs_date
  ON briefs(brief_date);
