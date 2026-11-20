"""Storage for the recruitment monitor: tables, migration with a backup first, short lived connections.

The monitor tables live in the existing engine database (data/engine.duckdb) and only refer to existing
player IDs (uid). Before the first migration the whole data folder database is copied to
backups/pre_monitor_<time>/ so nothing can be lost.
"""
from __future__ import annotations

import json
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import duckdb

from .. import config
from .. import db as _db

SCHEMA_VERSION = 2

TABLE_NAMES = ["rm_meta", "rm_scans", "rm_source_state", "rm_items", "rm_events", "rm_event_evidence",
               "rm_match_decisions", "rm_event_decisions", "rm_ai_cache", "rm_briefs",
               "rm_club_source", "rm_watch", "rm_watch_change"]

SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS rm_scan_seq START 1;
CREATE SEQUENCE IF NOT EXISTS rm_item_seq START 1;
CREATE SEQUENCE IF NOT EXISTS rm_event_seq START 1;
CREATE SEQUENCE IF NOT EXISTS rm_change_seq START 1;
CREATE TABLE IF NOT EXISTS rm_meta (key VARCHAR PRIMARY KEY, value VARCHAR);
CREATE TABLE IF NOT EXISTS rm_scans (
  scan_id INTEGER PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP, heartbeat_at TIMESTAMP,
  status VARCHAR, trigger VARCHAR, summary_json VARCHAR);
CREATE TABLE IF NOT EXISTS rm_source_state (
  source_id VARCHAR PRIMARY KEY, name VARCHAR, last_attempt TIMESTAMP, last_success TIMESTAMP,
  last_status VARCHAR, last_error VARCHAR, consecutive_failures INTEGER, items_last INTEGER, note VARCHAR);
CREATE TABLE IF NOT EXISTS rm_items (
  item_id INTEGER PRIMARY KEY, source_id VARCHAR, url VARCHAR, url_key VARCHAR, title VARCHAR,
  published_at TIMESTAMP, discovered_at TIMESTAMP, snippet VARCHAR, body_text VARCHAR, body_status VARCHAR,
  text_hash VARCHAR, announcement_type VARCHAR, club_text VARCHAR, league_hint VARCHAR, scan_id INTEGER,
  processed_hash VARCHAR, body_attempts INTEGER, body_next_try TIMESTAMP, health VARCHAR, health_note VARCHAR,
  mentions_found INTEGER, correction_notice BOOLEAN);
CREATE TABLE IF NOT EXISTS rm_events (
  event_id INTEGER PRIMARY KEY, fingerprint VARCHAR, event_type VARCHAR, player_text VARCHAR, name_norm VARCHAR,
  club_text VARCHAR, club_norm VARCHAR, league_hint VARCHAR, position_hint VARCHAR, announced_at TIMESTAMP,
  first_seen TIMESTAMP, last_seen TIMESTAMP, n_sources INTEGER, uid VARCHAR, match_status VARCHAR,
  match_grade VARCHAR, match_reasons VARCHAR, candidates_json VARCHAR, extraction VARCHAR,
  inbox_status VARCHAR, status_changed_at TIMESTAMP, user_note VARCHAR, user_decided BOOLEAN,
  updated_flag BOOLEAN, update_note VARCHAR, prior_decision VARCHAR, quote VARCHAR, detail_hash VARCHAR,
  followed_at_match BOOLEAN, win VARCHAR, source_state VARCHAR, confirmation VARCHAR, supported_by VARCHAR, correction_note VARCHAR);
CREATE TABLE IF NOT EXISTS rm_event_evidence (
  event_id INTEGER, item_id INTEGER, quote VARCHAR, mention_text VARCHAR, seen_at TIMESTAMP, withdrawn_at TIMESTAMP, mention_type VARCHAR);
CREATE TABLE IF NOT EXISTS rm_match_decisions (
  name_norm VARCHAR, club_norm VARCHAR, uid VARCHAR, verdict VARCHAR, decided_at TIMESTAMP, note VARCHAR);
CREATE TABLE IF NOT EXISTS rm_event_decisions (
  fingerprint VARCHAR, status VARCHAR, note VARCHAR, decided_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS rm_ai_cache (
  cache_key VARCHAR, result_json VARCHAR, tokens_in INTEGER, tokens_out INTEGER, cost_usd DOUBLE,
  created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS rm_briefs (
  brief_date DATE, generated_at TIMESTAMP, markdown VARCHAR, payload_json VARCHAR);
CREATE TABLE IF NOT EXISTS rm_club_source (
  club_id VARCHAR, name VARCHAR, league VARCHAR, db_name VARCHAR, kind VARCHAR, url VARCHAR, verified_at TIMESTAMP,
  status VARCHAR, http_status INTEGER, robots VARCHAR, detail VARCHAR, items_found INTEGER, note VARCHAR);
CREATE TABLE IF NOT EXISTS rm_watch (
  watch_id VARCHAR, club_id VARCHAR, kind VARCHAR, url VARCHAR, cd_uuid VARCHAR, created_at TIMESTAMP, last_checked TIMESTAMP,
  last_success TIMESTAMP, last_status VARCHAR, last_error VARCHAR, consecutive_failures INTEGER, last_change_at TIMESTAMP,
  n_changes INTEGER, baseline_at TIMESTAMP, selector VARCHAR, note VARCHAR, last_snapshot VARCHAR, config_hash VARCHAR, role VARCHAR);
CREATE TABLE IF NOT EXISTS rm_watch_change (
  change_id INTEGER, watch_id VARCHAR, detected_at TIMESTAMP, snapshot_from VARCHAR, snapshot_to VARCHAR, added_json VARCHAR,
  removed_json VARCHAR, cosmetic_only BOOLEAN, note VARCHAR);
"""
V2_COLUMNS = {
    "rm_items": [("body_attempts", "INTEGER"), ("body_next_try", "TIMESTAMP"), ("health", "VARCHAR"), ("health_note", "VARCHAR"),
                 ("mentions_found", "INTEGER"), ("correction_notice", "BOOLEAN")],
    "rm_events": [("source_state", "VARCHAR"), ("confirmation", "VARCHAR"), ("supported_by", "VARCHAR"), ("correction_note", "VARCHAR")],
    "rm_event_evidence": [("withdrawn_at", "TIMESTAMP"), ("mention_type", "VARCHAR")],
}

STATUSES = ["New", "Review", "Monitor", "Pass"]


def backups_dir() -> Path:
    return config.DATA_DIR.parent / "backups"


def open_con(path=None, tries: int = 12):
    """Opens the database; waits and retries while another process (the app, a scan) holds the file lock."""
    last = None
    for i in range(tries):
        try:
            return duckdb.connect(str(path or config.DB_PATH))
        except duckdb.IOException as exc:
            msg = str(exc).lower()
            if "lock" not in msg and "conflicting" not in msg and "already open" not in msg:
                raise
            last = exc
            time.sleep(min(0.25 * (2 ** i), 4.0))
    raise RuntimeError(f"The database stayed locked by another process: {last}")


def has_monitor_tables(path=None) -> bool:
    con = open_con(path)
    try:
        have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    finally:
        con.close()
    return "rm_meta" in have and "rm_events" in have


def _stored_version(path=None) -> int:
    con = open_con(path)
    try:
        r = con.execute("SELECT value FROM rm_meta WHERE key = 'schema_version'").fetchone()
        return int(r[0]) if r else 0
    except Exception:
        return 0
    finally:
        con.close()


def insert(con, table: str, **cols):
    """Named insert, so columns added by a migration can never shift a positional insert."""
    names = ", ".join(cols)
    con.execute(f"INSERT INTO {table} ({names}) VALUES ({', '.join('?' for _ in cols)})", list(cols.values()))


def backup_database(tag: str, path=None) -> Path:
    """Full copy of the database file (and anything pending) into backups/<tag>_<time>/."""
    src = Path(path or config.DB_PATH)
    dst = backups_dir() / f"{tag}_{datetime.now():%Y%m%d_%H%M%S}"
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst / src.name)
    wal = src.with_name(src.name + ".wal")
    if wal.exists():
        shutil.copy2(wal, dst / wal.name)
    return dst


def ensure_schema(path=None, log=print, backup: bool = True) -> dict:
    """Creates or upgrades the monitor tables. The first time, the database is backed up before any change.
    Returns {'created': bool, 'backup': path|None, 'version': int}."""
    out = {"created": False, "backup": None, "version": SCHEMA_VERSION}
    if has_monitor_tables(path) and _stored_version(path) < SCHEMA_VERSION:
        if backup:
            out["backup"] = str(backup_database("pre_monitor_v%d" % SCHEMA_VERSION, path))
            log(f"Monitor tables are being upgraded to version {SCHEMA_VERSION}. Backup written to {out['backup']}")
    if not has_monitor_tables(path):
        if backup:
            out["backup"] = str(backup_database("pre_monitor", path))
            log(f"Monitor tables are new in this database. Backup written to {out['backup']}")
        out["created"] = True
    con = open_con(path)
    try:
        con.execute(SCHEMA_SQL)
        for table, cols in V2_COLUMNS.items():          # tables made by version 1 get the new columns; nothing is dropped
            have = {r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]).fetchall()}
            for c, t in cols:
                if c not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")
        cur = con.execute("SELECT value FROM rm_meta WHERE key = 'schema_version'").fetchone()
        if cur is None:
            con.execute("INSERT INTO rm_meta VALUES ('schema_version', ?)", [str(SCHEMA_VERSION)])
            con.execute("INSERT INTO rm_meta VALUES ('created_at', ?)", [datetime.now().isoformat(timespec='seconds')])
        elif int(cur[0]) < SCHEMA_VERSION:
            con.execute("UPDATE rm_meta SET value = ? WHERE key = 'schema_version'", [str(SCHEMA_VERSION)])
    finally:
        con.close()
    return out


@contextmanager
def conn(path=None):
    """Short lived connection (the app and a scan can then share the file). Commits on success."""
    con = open_con(path)
    try:
        con.execute("BEGIN TRANSACTION")
        yield con
        con.execute("COMMIT")
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


def next_id(con, seq: str) -> int:
    return int(con.execute(f"SELECT nextval('{seq}')").fetchone()[0])


def get_meta(key: str, default=None, path=None):
    try:
        with conn(path) as con:
            r = con.execute("SELECT value FROM rm_meta WHERE key = ?", [key]).fetchone()
        return r[0] if r else default
    except Exception:
        return default


def set_meta(key: str, value, path=None):
    with conn(path) as con:
        con.execute("DELETE FROM rm_meta WHERE key = ?", [key])
        con.execute("INSERT INTO rm_meta VALUES (?, ?)", [key, str(value)])


def query(sql: str, params=None, path=None):
    """Read helper returning a DataFrame."""
    con = open_con(path)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


# ---- user configuration kept in the data folder (carried through upgrades) -------------------------------------
CONFIG_FILE = "monitor_config.json"
DEFAULT_CONFIG = {
    "max_age_days": 45,              # items older than this are ignored on a scan
    "min_hours_between_scans": 20,   # used by the catch-up scan at app start
    "request_timeout_s": 25,
    "max_retries": 3,
    "max_article_fetches_per_scan": 60,
    "followed_includes_verified": True,   # players with a manual club verification count as followed
    "google_news": {"enabled": False},    # off by default: its robots.txt disallows automated fetching
    "ai": {"enabled": False, "model": "claude-haiku-5-5", "max_calls_per_scan": 5, "monthly_usd_cap": 2.0},
    "schedule": {"enabled": False, "hour": 7, "minute": 15},
    "watch": {"enabled": False, "command": None, "allow_local": False, "page_timeout_s": 90, "page_delay_s": 0.5},
}


def load_config() -> dict:
    p = config.DATA_DIR / CONFIG_FILE
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        user = json.loads(p.read_text())
    except Exception:
        user = {}
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_config(cfg: dict):
    (config.DATA_DIR / CONFIG_FILE).write_text(json.dumps(cfg, indent=1))
