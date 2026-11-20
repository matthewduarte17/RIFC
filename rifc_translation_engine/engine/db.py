"""DuckDB storage. One local file, separate from any existing scouting system.

Tables
  imports          one row per uploaded file (with the column mapping used)
  raw_records      every original row as JSON, untouched
  player_seasons   canonical player season rows (per 90 values + totals)
  team_seasons     canonical team season rows
  transfers_ref    canonical transfer list rows
  validation_log   warnings raised at import
  model_runs       fitted model metadata (artifacts are pickled in data/models)
  backtest_runs    backtest summaries
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import duckdb
import pandas as pd

from .config import DB_PATH
from .schema import METRICS, SUPPORT_FIELDS

METRIC_COLS = list(METRICS) + SUPPORT_FIELDS

_PLAYER_COLS = {
    "row_id": "BIGINT", "import_id": "INTEGER", "provider": "VARCHAR",
    "player_key": "VARCHAR", "player_name": "VARCHAR", "name_norm": "VARCHAR",
    "provider_player_id": "VARCHAR", "birth_date": "DATE", "birth_year": "INTEGER",
    "age": "DOUBLE", "age_at_season": "DOUBLE", "nationality": "VARCHAR",
    "position_raw": "VARCHAR", "position_group": "VARCHAR", "team": "VARCHAR",
    "league": "VARCHAR", "season": "VARCHAR", "season_start": "DOUBLE", "season_end": "DOUBLE",
    "minutes": "DOUBLE", "matches": "DOUBLE", "starts": "DOUBLE", "foot": "VARCHAR",
    "height_cm": "DOUBLE", "current_team": "VARCHAR", "contract_expires": "VARCHAR",
}
for _m in METRIC_COLS:
    _PLAYER_COLS[f"{_m}_p90"] = "DOUBLE"
    _PLAYER_COLS[f"{_m}_total"] = "DOUBLE"

_TEAM_COLS = {
    "row_id": "BIGINT", "import_id": "INTEGER", "provider": "VARCHAR", "team": "VARCHAR",
    "team_norm": "VARCHAR", "league": "VARCHAR", "season": "VARCHAR", "season_start": "DOUBLE",
    "season_end": "DOUBLE", "matches": "DOUBLE", "goals_for": "DOUBLE", "goals_against": "DOUBLE",
    "xg_for": "DOUBLE", "xg_against": "DOUBLE", "shots_for": "DOUBLE", "possession_pct": "DOUBLE",
    "penalties_awarded": "DOUBLE", "penalties_scored": "DOUBLE", "formation": "VARCHAR",
}

_TRANSFER_COLS = {
    "row_id": "BIGINT", "import_id": "INTEGER", "player_name": "VARCHAR", "name_norm": "VARCHAR",
    "birth_date": "DATE", "from_team": "VARCHAR", "from_league": "VARCHAR", "from_season": "VARCHAR",
    "to_team": "VARCHAR", "to_league": "VARCHAR", "to_season": "VARCHAR",
    "transfer_date": "VARCHAR", "transfer_type": "VARCHAR",
}

SCHEMA_SQL = f"""
CREATE SEQUENCE IF NOT EXISTS import_seq START 1;
CREATE SEQUENCE IF NOT EXISTS row_seq START 1;
CREATE SEQUENCE IF NOT EXISTS run_seq START 1;
CREATE TABLE IF NOT EXISTS imports (
    import_id INTEGER PRIMARY KEY, filename VARCHAR, stored_path VARCHAR, sha256 VARCHAR,
    dataset_type VARCHAR, provider VARCHAR, file_league VARCHAR, file_season VARCHAR,
    n_rows INTEGER, mapping_json VARCHAR, options_json VARCHAR, imported_at TIMESTAMP, notes VARCHAR
);
CREATE TABLE IF NOT EXISTS raw_records (import_id INTEGER, row_num INTEGER, record_json VARCHAR);
CREATE TABLE IF NOT EXISTS player_seasons ({", ".join(f"{k} {v}" for k, v in _PLAYER_COLS.items())});
CREATE TABLE IF NOT EXISTS team_seasons ({", ".join(f"{k} {v}" for k, v in _TEAM_COLS.items())});
CREATE TABLE IF NOT EXISTS transfers_ref ({", ".join(f"{k} {v}" for k, v in _TRANSFER_COLS.items())});
CREATE TABLE IF NOT EXISTS validation_log (
    import_id INTEGER, level VARCHAR, check_name VARCHAR, message VARCHAR, n_affected INTEGER
);
CREATE TABLE IF NOT EXISTS model_runs (
    run_id INTEGER PRIMARY KEY, created_at TIMESTAMP, settings_json VARCHAR,
    summary_json VARCHAR, artifact_path VARCHAR, data_fingerprint VARCHAR
);
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id INTEGER PRIMARY KEY, created_at TIMESTAMP, settings_json VARCHAR,
    summary_json VARCHAR, artifact_path VARCHAR, data_fingerprint VARCHAR
);
"""

TABLE_COLUMNS = {"player_seasons": _PLAYER_COLS, "team_seasons": _TEAM_COLS, "transfers_ref": _TRANSFER_COLS}


@contextmanager
def connect(path=None, read_only=False):
    con = duckdb.connect(str(path or DB_PATH), read_only=read_only)
    try:
        if not read_only:
            con.execute(SCHEMA_SQL)
            _migrate(con)
        yield con
    finally:
        con.close()


def _migrate(con):
    """Add columns introduced after a database was created (never drops anything)."""
    have = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'imports'").fetchall()}
    if "active" not in have:
        con.execute("ALTER TABLE imports ADD COLUMN active BOOLEAN DEFAULT TRUE")
        con.execute("UPDATE imports SET active = TRUE WHERE active IS NULL")
    if "exclusion_reason" not in have:
        con.execute("ALTER TABLE imports ADD COLUMN exclusion_reason VARCHAR")
    if "display_only" not in have:      # shown in profiles and discovery, never used to train or test the model
        con.execute("ALTER TABLE imports ADD COLUMN display_only BOOLEAN DEFAULT FALSE")
        con.execute("UPDATE imports SET display_only = FALSE WHERE display_only IS NULL")
    if "scope_note" not in have:
        con.execute("ALTER TABLE imports ADD COLUMN scope_note VARCHAR")
    if "replaced_by" not in have:       # import id(s) of the corrected files that replaced this one
        con.execute("ALTER TABLE imports ADD COLUMN replaced_by VARCHAR")
    for table, cols in TABLE_COLUMNS.items():
        have = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]).fetchall()}
        for c, t in cols.items():
            if c not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")


def init_db(path=None):
    with connect(path):
        pass


def query(sql: str, params=None, path=None) -> pd.DataFrame:
    init_db(path)
    with connect(path) as con:
        return con.execute(sql, params or []).df()


def next_id(con, seq: str) -> int:
    return int(con.execute(f"SELECT nextval('{seq}')").fetchone()[0])


def insert_df(con, table: str, df: pd.DataFrame):
    cols = TABLE_COLUMNS[table]
    out = df.reindex(columns=list(cols))
    for c, t in cols.items():
        if t == "DOUBLE":
            out[c] = pd.to_numeric(out[c], errors="coerce")
        elif t in ("INTEGER", "BIGINT"):
            out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
        elif t == "DATE":
            out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
        else:
            out[c] = out[c].astype("object").where(out[c].notna(), None)
            out[c] = out[c].map(lambda v: None if v is None else str(v))
    con.register("_tmp_insert", out)
    con.execute(f"INSERT INTO {table} SELECT * FROM _tmp_insert")
    con.unregister("_tmp_insert")


def delete_import(import_id: int, path=None):
    """Removes one file's rows. Its checksum is recorded in app_meta, so a later upgrade keeps it removed."""
    with connect(path) as con:
        sha = con.execute("SELECT sha256 FROM imports WHERE import_id = ?", [import_id]).fetchone()
        con.execute("CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        cur = con.execute("SELECT value FROM app_meta WHERE key = 'deleted_imports'").fetchone()
        gone = json.loads(cur[0]) if cur else []
        if sha and sha[0] and sha[0] not in gone:
            con.execute("INSERT OR REPLACE INTO app_meta VALUES ('deleted_imports', ?)", [json.dumps(gone + [sha[0]])])
        for t in ("player_seasons", "team_seasons", "transfers_ref", "raw_records", "validation_log", "imports"):
            con.execute(f"DELETE FROM {t} WHERE import_id = ?", [import_id])


def load_table(table: str, path=None, active_only: bool = False) -> pd.DataFrame:
    if active_only and table in TABLE_COLUMNS:
        return query(f"SELECT t.* FROM {table} t JOIN imports i USING (import_id) WHERE i.active", path=path)
    return query(f"SELECT * FROM {table}", path=path)


def set_import_active(import_ids, active: bool, reason: str | None = None, path=None):
    """Exclude or restore imports without deleting anything."""
    ids = [int(i) for i in (import_ids if isinstance(import_ids, (list, tuple, set)) else [import_ids])]
    with connect(path) as con:
        for i in ids:
            con.execute("UPDATE imports SET active = ?, exclusion_reason = ? WHERE import_id = ?",
                        [active, None if active else reason, i])
    try:
        from .rebuild import write_manifest
        write_manifest(path)
    except Exception:
        pass


def training_subset(fingerprint: str) -> list:
    """Only the imports that train the model (active). Files added or kept outside the model do not make it stale."""
    try:
        return [r[:2] for r in json.loads(fingerprint) if r[2]]
    except Exception:
        return [fingerprint]


def data_fingerprint(path=None) -> str:
    df = query("SELECT import_id, sha256, active FROM imports ORDER BY import_id", path=path)
    return json.dumps(df.values.tolist())
