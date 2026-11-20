"""Rebuild the database from the original files in data/raw.

Every import is listed in data/import_manifest.json (file, league, season,
provider, age reference, mapping, active flag). If the database can't be opened
on another computer (for example a different DuckDB version), the app moves
it aside (it is renamed, never deleted) and reimports every file from data/raw
with the same settings, then refits and backtests.

Run by hand:  python -m engine.rebuild
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, DB_PATH, RAW_DIR

MANIFEST = DATA_DIR / "import_manifest.json"


def _rel_to_raw(path) -> str:
    """Stored copy's path relative to data/raw (works after the folder is moved to another computer)."""
    parts = Path(str(path)).parts
    if "raw" in parts:
        return str(Path(*parts[len(parts) - 1 - parts[::-1].index("raw") + 1:]))
    return Path(str(path)).name


def write_manifest(db_path=None):
    """Snapshot of every import's settings, kept next to the database."""
    from . import db
    im = db.query("SELECT * FROM imports ORDER BY import_id", path=db_path)
    rows = []
    for r in im.to_dict("records"):
        rows.append({
            "import_id": int(r["import_id"]), "stored_file": _rel_to_raw(r["stored_path"]),
            "filename": r["filename"], "dataset_type": r["dataset_type"], "provider": r["provider"],
            "file_league": r["file_league"], "file_season": r["file_season"],
            "mapping": json.loads(r["mapping_json"] or "[]"), "options": json.loads(r["options_json"] or "{}"),
            "notes": r["notes"], "active": bool(r.get("active", True)) if r.get("active") is not None else True,
            "exclusion_reason": r.get("exclusion_reason"),
            "display_only": bool(r.get("display_only")) if r.get("display_only") is not None else False,
            "scope_note": r.get("scope_note"), "replaced_by": r.get("replaced_by"),
        })
    MANIFEST.write_text(json.dumps(rows, indent=1, default=str))
    return len(rows)


def rebuild(progress=print, remap: bool = False) -> int:
    """remap=True re-detects columns with the current alias list (use after new metrics are added);
    otherwise each file is reimported with the mapping it was first imported with."""
    from . import db
    from .ingest import ColumnMap, ImportOptions, import_file
    if not MANIFEST.exists():
        raise FileNotFoundError("data/import_manifest.json is missing, so the original import settings are unknown.")
    rows = json.loads(MANIFEST.read_text())
    if DB_PATH.exists():
        aside = DB_PATH.with_name(f"engine_unreadable_{datetime.now():%Y%m%d_%H%M%S}.duckdb")
        DB_PATH.rename(aside)
        progress(f"Old database moved to {aside.name}")
    new_raw = RAW_DIR / "_rebuild_copies"   # import_file stores a copy; keep the originals untouched
    new_raw.mkdir(exist_ok=True)
    id_map = {}
    for r in rows:
        src = RAW_DIR / r["stored_file"]
        o = r["options"]
        opt = ImportOptions(dataset_type=r["dataset_type"], provider=r["provider"], file_league=r["file_league"],
                            file_season=r["file_season"], age_reference=o.get("age_reference", "season"),
                            export_year=o.get("export_year"), notes=r.get("notes") or "")
        maps = None if remap else ([ColumnMap(m["source"], m["target"], m["unit"], m.get("confidence", ""))
                                    for m in r["mapping"]] or None)
        iid, _ = import_file(src, opt, sheet=o.get("sheet"), mapping=maps, raw_dir=new_raw,
                             exclude_imports=[id_map[q["import_id"]] for q in rows if q["import_id"] in id_map
                                              and str(r["import_id"]) in str(q.get("replaced_by") or "").split(",")])
        id_map[r["import_id"]] = iid
        with db.connect() as con:       # the manifest's flags win over anything inferred during import
            con.execute("UPDATE imports SET active = ?, display_only = ?, exclusion_reason = ?, scope_note = ? "
                        "WHERE import_id = ?", [bool(r.get("active", True)), bool(r.get("display_only")),
                                                None if r.get("active", True) else (r.get("exclusion_reason") or
                                                                                    "Excluded before rebuild"),
                                                r.get("scope_note"), iid])
        progress(f"Reimported {r['filename']}")
    with db.connect() as con:
        for r in rows:
            if r.get("replaced_by"):
                new_ids = ",".join(str(id_map.get(int(x), x)) for x in str(r["replaced_by"]).split(",") if x)
                con.execute("UPDATE imports SET replaced_by = ? WHERE import_id = ?", [new_ids, id_map[r["import_id"]]])
    write_manifest()
    n_team = rebuild_team_batches(progress)
    return len(rows) + n_team


def rebuild_team_batches(progress=print) -> int:
    """Reimport StatsBomb team batches from data/team_import_manifest.json and data/raw/statsbomb."""
    from . import teamstats as T
    f = DATA_DIR / "team_import_manifest.json"
    if not f.exists():
        return 0
    rows = json.loads(f.read_text())
    n = 0
    for r in rows:
        files = json.loads(r["files_json"])
        paths = [RAW_DIR / x["stored"] for x in files]
        known = {}
        from .normalize import norm_team
        from . import db
        ps = db.load_table("player_seasons", active_only=True)
        t = ps[(ps["league"] == r["competition"]) & (ps["season"] == r["season"])]["team"].dropna().unique()
        known = {norm_team(x): x for x in t}
        b = T.build_batch([(p.name, p.read_bytes()) for p in paths], r["competition"], r["season"], r["phase"], known)
        bid = T.commit_batch(b, r.get("notes") or "", raw_dir=RAW_DIR / "_rebuild_copies")
        if not r.get("active", True):
            T.set_batch_active(bid, False)
        progress(f"Reimported StatsBomb {r['competition']} {r['season']}")
        n += 1
    return n


def refit(progress=print):
    from .api import Engine
    progress("Fitting the translation model")
    eng = Engine.fit()
    progress("Running the backtest")
    eng.run_backtest()
    try:
        from .teamcontext import run_and_save
        progress("Running the team context test")
        run_and_save(eng)
    except Exception:
        pass
    return eng


if __name__ == "__main__":
    n = rebuild()
    refit()
    print(f"Rebuilt {n} imports, refitted and backtested.")
