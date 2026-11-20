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

SCHEMA_VERSION = 1

TABLE_NAMES = ["rm_meta", "rm_scans", "rm_source_state", "rm_items", "rm_events", "rm_event_evidence",
               "rm_match_decisions", "rm_event_decisions", "rm_ai_cache", "rm_briefs"]

SCHEMA_SQL = """
CREATE SEQUENCE IF NOT EXISTS rm_scan_seq START 1;
CREATE SEQUENCE IF NOT EXISTS rm_item_seq START 1;
CREATE SEQUENCE IF NOT EXISTS rm_event_seq START 1;
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
  processed_hash VARCHAR);
CREATE TABLE IF NOT EXISTS rm_events (
  event_id INTEGER PRIMARY KEY, fingerprint VARCHAR, event_type VARCHAR, player_text VARCHAR, name_norm VARCHAR,
  club_text VARCHAR, club_norm VARCHAR, league_hint VARCHAR, position_hint VARCHAR, announced_at TIMESTAMP,
  first_seen TIMESTAMP, last_seen TIMESTAMP, n_sources INTEGER, uid VARCHAR, match_status VARCHAR,
  match_grade VARCHAR, match_reasons VARCHAR, candidates_json VARCHAR, extraction VARCHAR,
  inbox_status VARCHAR, status_changed_at TIMESTAMP, user_note VARCHAR, user_decided BOOLEAN,
  updated_flag BOOLEAN, update_note VARCHAR, prior_decision VARCHAR, quote VARCHAR, detail_hash VARCHAR,
  followed_at_match BOOLEAN, win VARCHAR);
CREATE TABLE IF NOT EXISTS rm_event_evidence (
  event_id INTEGER, item_id INTEGER, quote VARCHAR, mention_text VARCHAR, seen_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS rm_match_decisions (
  name_norm VARCHAR, club_norm VARCHAR, uid VARCHAR, verdict VARCHAR, decided_at TIMESTAMP, note VARCHAR);
CREATE TABLE IF NOT EXISTS rm_event_decisions (
  fingerprint VARCHAR, status VARCHAR, note VARCHAR, decided_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS rm_ai_cache (
  cache_key VARCHAR, result_json VARCHAR, tokens_in INTEGER, tokens_out INTEGER, cost_usd DOUBLE,
  created_at TIMESTAMP);
CREATE TABLE IF NOT EXISTS rm_briefs (
  brief_date DATE, generated_at TIMESTAMP, markdown VARCHAR, payload_json VARCHAR);
"""

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
    if not has_monitor_tables(path):
        if backup:
            out["backup"] = str(backup_database("pre_monitor", path))
            log(f"Monitor tables are new in this database. Backup written to {out['backup']}")
        out["created"] = True
    con = open_con(path)
    try:
        con.execute(SCHEMA_SQL)
        cur = con.execute("SELECT value FROM rm_meta WHERE key = 'schema_version'").fetchone()
        if cur is None:
            con.execute("INSERT INTO rm_meta VALUES ('schema_version', ?)", [str(SCHEMA_VERSION)])
            con.execute("INSERT INTO rm_meta VALUES ('created_at', ?)", [datetime.now().isoformat(timespec='seconds')])
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
