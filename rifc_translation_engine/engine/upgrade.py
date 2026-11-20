"""Safe upgrades: your data lives outside the app folder and a new ZIP never replaces it.

Layout
  <app folder>/                  code, launcher, and data/ = the release's own data (only read, never used live)
  ~/RIFC Translation Engine/     your data home (RIFC_TE_HOME): data/, backups/, .venv/

The launcher runs  python -m engine.upgrade --bundle <app folder>  before every start:
  * No data home yet: looks for an existing installation (an older app folder with data/engine.duckdb) and
    asks which one to keep. With none, the release's data becomes the data home.
  * Data home older than the release: upgrades it (below).
  * Same version: nothing to do. Data home newer than the release (an older ZIP): nothing is touched.

An upgrade
  1. builds the new data folder in a staging area: the release's data, then everything that is yours on top:
     shortlist and notes, club verifications, identity decisions and rules, player IDs, each file's use level
     and reason, files you imported that the release does not have (reimported from your own data/raw),
     StatsBomb batches you imported, files you removed (stay removed), and the release decisions in RELEASE.json;
  2. verifies the staged folder against your current one, item by item; any failure stops the upgrade and
     leaves your data exactly as it was;
  3. moves your current data folder into backups/pre_upgrade_<time> (never deleted) and the staged one in.
An old installation found elsewhere is only read: its folder is copied to the backup and left in place.

By hand:  python -m engine.upgrade --bundle /path/to/app [--home "~/RIFC Translation Engine"] [--yes]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

APP_NAME = "RIFC Translation Engine"
DEFAULT_HOME = Path.home() / APP_NAME
SKIP = shutil.ignore_patterns("identity_*.pkl", "discovery_run*.pkl", "RELEASE.json", ".DS_Store")


# ---------------------------------------------------------------------------
# Reading a data folder without touching it
# ---------------------------------------------------------------------------
def _table(con, name: str):
    import pandas as pd
    have = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return con.execute(f"SELECT * FROM {name}").df() if name in have else pd.DataFrame()


def read_state(data_dir: Path) -> dict:
    """Everything that counts as yours in one data folder. Reads a temporary copy, so the file is never changed."""
    import duckdb
    tmpd = Path(tempfile.mkdtemp(prefix="rifc_read_"))
    try:
        shutil.copy2(data_dir / "engine.duckdb", tmpd / "copy.duckdb")
        if (data_dir / "engine.duckdb.wal").exists():     # changes not yet written into the main file
            shutil.copy2(data_dir / "engine.duckdb.wal", tmpd / "copy.duckdb.wal")
        con = duckdb.connect(str(tmpd / "copy.duckdb"))   # a throwaway copy, so it may replay pending changes
        from .monitor.store import TABLE_NAMES as _RM
        st = {k: _table(con, k) for k in ("imports", "shortlist", "club_verifications", "identity_registry",
                                           "team_stat_batches", "app_meta", "model_runs", *_RM)}
        con.close()
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)
    for f in ("identity_decisions.json", "identity_overrides.json"):
        p = data_dir / f
        try:
            st[f] = json.loads(p.read_text()) if p.exists() else {}
        except Exception:
            st[f] = {}
    meta = dict(zip(st["app_meta"]["key"], st["app_meta"]["value"])) if not st["app_meta"].empty else {}
    st["version"] = meta.get("data_version")
    present = set(st["imports"]["sha256"]) if not st["imports"].empty else set()
    st["deleted"] = [x for x in json.loads(meta.get("deleted_imports", "[]")) if x not in present]  # re-imported
    mr = st["model_runs"]
    st["settings"] = json.loads(mr.sort_values("run_id").iloc[-1]["settings_json"]) if not mr.empty else None
    return st


def state_of(r) -> str:
    """Use level of one imports row (older databases have no display_only column)."""
    a = r.get("active", True) if isinstance(r, dict) else getattr(r, "active", True)
    d = r.get("display_only", False) if isinstance(r, dict) else getattr(r, "display_only", False)
    a = True if a is None or a != a else bool(a)
    d = False if d is None or d != d else bool(d)
    return "Full use" if a else ("Profiles and discovery only" if d else "Excluded")


def summary(st: dict) -> str:
    return (f"{len(st['imports'])} files, {len(st['shortlist'])} shortlist entries, "
            f"{len(st['club_verifications'])} club verifications, "
            f"{len(st['identity_decisions.json'].get('decisions', []))} identity decisions")


def froms(d: dict) -> list:
    f = d.get("from", [])
    return [f] if isinstance(f, str) else list(f)


def vkey(v) -> tuple:
    try:
        return tuple(int(x) for x in str(v or "0").split("."))
    except ValueError:
        return (0,)


# ---------------------------------------------------------------------------
# Finding an existing installation
# ---------------------------------------------------------------------------
def find_installations(bundle: Path, home: Path) -> list[Path]:
    """Older app folders that hold their own data (start.command next to data/engine.duckdb)."""
    roots = [bundle.parent, Path.home() / "Desktop", Path.home() / "Downloads", Path.home() / "Documents", Path.home(),
             Path.home() / ".Trash"]
    found = set()
    for r in roots:
        if not r.exists():
            continue
        for pat in ("*", "*/*"):
            try:
                cands = list(r.glob(pat))
            except (PermissionError, OSError):
                continue
            for p in cands:
                try:
                    if p.is_dir() and (p / "start.command").exists() and (p / "data" / "engine.duckdb").exists() \
                            and p.resolve() != bundle.resolve() and home.resolve() not in p.resolve().parents:
                        found.add(p.resolve())
                except (PermissionError, OSError):
                    continue
    return sorted(found, key=lambda p: (p / "data" / "engine.duckdb").stat().st_mtime, reverse=True)


# ---------------------------------------------------------------------------
# Merge (child process, RIFC_TE_HOME = staging folder, so every engine path points there)
# ---------------------------------------------------------------------------
def _rel_raw(stored_path) -> str:
    parts = Path(str(stored_path)).parts
    if "raw" in parts:
        i = len(parts) - 1 - parts[::-1].index("raw")
        return str(Path(*parts[i + 1:]))
    return Path(str(stored_path)).name


def merge_into(stage_root: Path, user_data: Path, release: dict, log: list) -> dict:
    import pandas as pd
    from . import db
    from .config import DATA_DIR, RAW_DIR
    from .identity import SQL as ISQL
    from .ingest import ColumnMap, ImportOptions, import_file
    from .rebuild import write_manifest
    from .recruit import SL_SQL

    assert DATA_DIR.resolve() == (stage_root / "data").resolve(), "staging folder not active"
    user = read_state(user_data)
    try:
        db.init_db()
    except Exception as exc:   # this computer's DuckDB cannot open the release database: rebuild it from its files
        from .rebuild import rebuild
        log.append(f"The release database could not be opened here ({type(exc).__name__}), so it was rebuilt from "
                   "the original files. The model refits when you next open the app.")
        rebuild(progress=lambda m: None)
    base = db.query("SELECT * FROM imports")
    by_sha = {r.sha256: r for r in base.itertuples()}
    dec_of = {s: d for d in release.get("decisions", []) for s in d.get("sha256", [])}
    uim = user["imports"]
    id_map, missing = {}, []

    # 1. files you imported that the release does not have: reimported from your data/raw, same settings
    for r in uim.sort_values("import_id").itertuples() if not uim.empty else []:
        if r.sha256 in by_sha:
            id_map[int(r.import_id)] = int(by_sha[r.sha256].import_id)
            continue
        src = user_data / "raw" / _rel_raw(r.stored_path)
        if not src.exists():
            missing.append(r.filename)
            log.append(f"MISSING: the original of your file {r.filename} is not in {user_data / 'raw'}.")
            continue
        o = json.loads(r.options_json or "{}")
        maps = [ColumnMap(m["source"], m["target"], m["unit"], m.get("confidence", ""))
                for m in json.loads(r.mapping_json or "[]")] or None
        opt = ImportOptions(dataset_type=r.dataset_type, provider=r.provider, file_league=r.file_league,
                            file_season=r.file_season, age_reference=o.get("age_reference", "season"),
                            export_year=o.get("export_year"), notes=r.notes or "")
        named = Path(tempfile.mkdtemp(prefix="rifc_named_")) / Path(str(r.filename)).name
        shutil.copy2(src, named)                # keeps the file name you imported it under
        iid, _ = import_file(named, opt, sheet=o.get("sheet"), mapping=maps, raw_dir=RAW_DIR)
        shutil.rmtree(named.parent, ignore_errors=True)
        id_map[int(r.import_id)] = int(iid)
        log.append(f"Kept your file {r.filename} ({r.file_league} {r.file_season}).")

    # 2. use levels: yours win, except a release decision for a file still at the level it replaces
    with db.connect() as con:
        for r in uim.itertuples() if not uim.empty else []:
            nid = id_map.get(int(r.import_id))
            if nid is None:
                continue
            st = state_of(r)
            reason = getattr(r, "exclusion_reason", None)
            note = getattr(r, "scope_note", None)
            d = dec_of.get(r.sha256)
            if d and st in froms(d):
                b = by_sha[r.sha256]
                st, reason, note = state_of(b), b.exclusion_reason, b.scope_note
                log.append(f"Release decision: {r.filename} moved from {state_of(r)} to {d['to']}.")
            con.execute("UPDATE imports SET active = ?, display_only = ?, exclusion_reason = ?, scope_note = ? "
                        "WHERE import_id = ?", [st == "Full use", st == "Profiles and discovery only",
                                                None if pd.isna(reason) else reason,
                                                None if pd.isna(note) else note, nid])
            rb = getattr(r, "replaced_by", None)
            if rb is not None and not pd.isna(rb) and str(rb):
                con.execute("UPDATE imports SET replaced_by = ? WHERE import_id = ?",
                            [",".join(str(id_map.get(int(x), x)) for x in str(rb).split(",") if x), nid])

    # 3. files you removed stay removed; files new in this release are listed
    for s in user["deleted"]:
        if s in by_sha and s not in dec_of:
            db.delete_import(int(by_sha[s].import_id))
            log.append(f"Kept removed: {by_sha[s].filename}.")
    user_sha = set(uim["sha256"]) if not uim.empty else set()
    for b in base.itertuples():
        if b.sha256 not in user_sha and b.sha256 not in user["deleted"]:
            log.append(f"New in this release: {b.file_league} {b.file_season}, {b.filename} ({state_of(b)}).")
    write_manifest()

    # 4. StatsBomb batches
    ub = user["team_stat_batches"]
    if not ub.empty:
        from . import teamstats as T
        from .normalize import norm_team
        for r in ub.itertuples():
            bb = T.batches()
            same = bb[(bb["competition"] == r.competition) & (bb["season"].astype(str) == str(r.season))
                      & (bb["phase"] == r.phase)] if not bb.empty else bb
            if not same.empty:
                T.set_batch_active(int(same["batch_id"].iloc[0]), bool(r.active))
                continue
            files = json.loads(r.files_json)
            paths = [user_data / "raw" / f["stored"] for f in files]
            if not all(p.exists() for p in paths):
                missing.append(f"StatsBomb {r.competition} {r.season}")
                log.append(f"MISSING: original StatsBomb files for {r.competition} {r.season} ({r.phase}).")
                continue
            ps = db.load_table("player_seasons", active_only=True)
            known = {norm_team(x): x for x in ps[(ps["league"] == r.competition) &
                                                  (ps["season"] == str(r.season))]["team"].dropna().unique()}
            b = T.build_batch([(p.name, p.read_bytes()) for p in paths], r.competition, str(r.season), r.phase, known)
            T.set_batch_active(T.commit_batch(b, r.notes or ""), bool(r.active))
            log.append(f"Kept your StatsBomb batch {r.competition} {r.season} ({r.phase}).")

    # 5. player IDs (yours win), shortlist, club verifications, identity decisions and rules
    with db.connect() as con:
        con.execute(ISQL)
        con.execute(SL_SQL)
        ureg = user["identity_registry"]
        if not ureg.empty:
            con.register("_ur", ureg[["natkey", "uid", "assigned_at"]])
            con.execute("DELETE FROM identity_registry WHERE natkey IN (SELECT natkey FROM _ur)")
            con.execute("INSERT INTO identity_registry SELECT * FROM _ur")
            con.unregister("_ur")
        con.execute("DELETE FROM shortlist")
        sl = user["shortlist"]
        if not sl.empty:
            con.register("_sl", sl[["key", "role", "note", "added_at"]])
            con.execute("INSERT INTO shortlist SELECT * FROM _sl")
            con.unregister("_sl")
        con.execute("DELETE FROM club_verifications")
        cv = user["club_verifications"]
        if not cv.empty:
            cols = [c[0] for c in con.execute("DESCRIBE club_verifications").fetchall()]
            con.register("_cv", cv.reindex(columns=cols))
            con.execute("INSERT INTO club_verifications SELECT * FROM _cv")
            con.unregister("_cv")
    udec = user["identity_decisions.json"]
    dec_file = DATA_DIR / "identity_decisions.json"
    if udec.get("decisions"):
        dec_file.write_text(json.dumps(udec, indent=1))
    elif dec_file.exists():
        dec_file.unlink()
    rules_f = DATA_DIR / "identity_overrides.json"
    rules = json.loads(rules_f.read_text()) if rules_f.exists() else {"do_not_link": []}
    for rule in user["identity_overrides.json"].get("do_not_link", []):
        if rule not in rules.setdefault("do_not_link", []):
            rules["do_not_link"].append(rule)
            log.append(f"Kept your identity rule for {rule.get('player')}.")
    rules_f.write_text(json.dumps(rules, indent=1, ensure_ascii=False))

    # 5b. recruitment monitor: inbox, your decisions, match answers, scans, source state, briefs and settings
    _carry_monitor(user, user_data, log)

    # 6. model settings and status
    from .api import Engine
    eng = Engine.load()
    cur = json.loads(json.dumps(eng.settings.to_dict(), default=str)) if eng is not None else None
    diff = {k: v for k, v in (user["settings"] or {}).items() if cur is not None and k in cur and cur[k] != v}
    if diff:
        log.append("NOTE: your last model used settings that differ from this release's model: "
                   + ", ".join(f"{k} = {v}" for k, v in diff.items())
                   + ". Set them again on the Translation Model page and refit if you want them back.")
    stale = eng is None or eng.is_stale()
    log.append("Model: the files used for training differ from the release's model, so the app will offer a refit "
               "(Data Library, Model is outdated)." if stale else "Model: ready, trained on the same files you use.")

    set_meta("data_version", release["version"])
    set_meta("upgraded_from", user.get("version") or "before versioning")
    set_meta("upgraded_at", datetime.now().isoformat(timespec="seconds"))
    set_meta("deleted_imports", json.dumps(user["deleted"]))
    return {"id_map": id_map, "missing": missing}


MONITOR_FILES = ["monitor_config.json", "monitor_sources.json", "club_aliases.json"]
MONITOR_SEQ = {"rm_event_seq": ("rm_events", "event_id"), "rm_item_seq": ("rm_items", "item_id"),
               "rm_scan_seq": ("rm_scans", "scan_id")}


def _carry_monitor(user: dict, user_data: Path, log: list):
    """Copies the monitor tables and files from your data into the staged data. Row ids are kept, so decisions and
    evidence stay attached to the same events."""
    from . import db
    from .monitor import store
    have = [t for t in store.TABLE_NAMES if t in user and not user[t].empty]
    store.ensure_schema(log=lambda m: None, backup=False)
    with db.connect() as con:
        for t in have:
            cols = [c[0] for c in con.execute(f"DESCRIBE {t}").fetchall()]
            df = user[t].reindex(columns=cols)
            if t == "rm_meta":
                df = df[df["key"] != "schema_version"]
            con.execute(f"DELETE FROM {t}" + (" WHERE key <> 'schema_version'" if t == "rm_meta" else ""))
            if len(df):
                con.register("_rm_tmp", df)
                con.execute(f"INSERT INTO {t} SELECT * FROM _rm_tmp")
                con.unregister("_rm_tmp")
        for seq, (t, c) in MONITOR_SEQ.items():            # new ids continue after the carried ones
            mx = con.execute(f"SELECT COALESCE(MAX({c}), 0) FROM {t}").fetchone()[0]
            con.execute(f"DROP SEQUENCE IF EXISTS {seq}")
            con.execute(f"CREATE SEQUENCE {seq} START {int(mx) + 1}")
    copied = []
    for f in MONITOR_FILES:
        src = user_data / f
        if src.exists():
            shutil.copy2(src, Path(os.environ["RIFC_TE_HOME"]) / "data" / f)
            copied.append(f)
    bsrc = user_data / "briefs"
    if bsrc.exists():
        shutil.copytree(bsrc, Path(os.environ["RIFC_TE_HOME"]) / "data" / "briefs", dirs_exist_ok=True)
    if have or copied:
        log.append(f"Kept your recruitment monitor: {len(user.get('rm_events', []))} inbox events, "
                   f"{len(user.get('rm_event_decisions', []))} saved decisions, {len(user.get('rm_match_decisions', []))} match answers"
                   + (f", files {', '.join(copied)}" if copied else "") + ".")


def set_meta(key: str, value: str, path=None):
    from . import db
    with db.connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
        con.execute("INSERT OR REPLACE INTO app_meta VALUES (?, ?)", [key, str(value)])


def get_meta(key: str, default=None, path=None):
    from . import db
    try:
        with db.connect(path) as con:
            con.execute("CREATE TABLE IF NOT EXISTS app_meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
            r = con.execute("SELECT value FROM app_meta WHERE key = ?", [key]).fetchall()
        return r[0][0] if r else default
    except Exception:
        return default


def record_deleted(sha: str, path=None):
    """Called when a file is removed in the app, so a later upgrade does not bring it back."""
    cur = json.loads(get_meta("deleted_imports", "[]", path))
    if sha and sha not in cur:
        set_meta("deleted_imports", json.dumps(cur + [sha]), path)


# ---------------------------------------------------------------------------
# Verification: the staged folder against your current one
# ---------------------------------------------------------------------------
def verify(old_data: Path, new_data: Path, release: dict, missing=()) -> list[tuple[bool, str]]:
    import pandas as pd
    old, new = read_state(old_data), read_state(new_data)
    res = []

    def same(a, b):
        return (pd.isna(a) and pd.isna(b)) or a == b

    o, n = old["shortlist"], new["shortlist"]
    nk = n.set_index("key") if not n.empty else pd.DataFrame(columns=["role", "note"])
    bad = [r.key for r in o.itertuples() if r.key not in nk.index
           or not same(nk.loc[r.key, "note"], r.note) or not same(nk.loc[r.key, "role"], r.role)]
    ok = not bad and len(n) == len(o)
    res.append((ok, f"Shortlist: {len(o)} of {len(o)} entries kept with their notes and roles" if ok
                else f"Shortlist changed: {bad or 'extra entries'}"))

    def rows(df, cols):
        if df.empty:
            return set()
        return {tuple(str(v) for v in r) for r in df.reindex(columns=cols).itertuples(index=False)}
    cvc = ["uid", "status", "current_club", "verified_on", "source", "contract_expiry", "note"]
    lost = rows(old["club_verifications"], cvc) - rows(new["club_verifications"], cvc)
    res.append((not lost, f"Club verifications: {len(old['club_verifications'])} kept unchanged" if not lost
                else f"Club verifications lost or changed: {len(lost)}"))
    od = old["identity_decisions.json"].get("decisions", [])
    nd = new["identity_decisions.json"].get("decisions", [])
    res.append((all(d in nd for d in od), f"Identity decisions: {len(od)} kept"))
    orl = old["identity_overrides.json"].get("do_not_link", [])
    nrl = new["identity_overrides.json"].get("do_not_link", [])
    res.append((all(x in nrl for x in orl), f"Identity rules: all {len(orl)} of yours kept ({len(nrl)} in total)"))
    oreg, nreg = old["identity_registry"], new["identity_registry"]
    if not oreg.empty:
        m = oreg.merge(nreg, on="natkey", how="left", suffixes=("", "_n"))
        ch = int((m["uid"] != m["uid_n"]).sum())
        res.append((ch == 0, f"Player IDs: all {len(oreg):,} kept" if ch == 0 else f"Player IDs changed: {ch}"))
        known = set(nreg["uid"])
        orphan = [k for k in (set(n["key"]) if not n.empty else set()) if "|" not in str(k) and k not in known]
        res.append((not orphan, "Every shortlist entry points to a known player" if not orphan
                    else f"Shortlist entries with no player: {orphan}"))

    oe, ne = old.get("rm_events"), new.get("rm_events")
    if oe is not None and not oe.empty:
        k = ["fingerprint", "inbox_status", "user_note", "user_decided", "uid", "match_status"]
        lost = rows(oe, k) - rows(ne if ne is not None else oe.iloc[0:0], k)
        res.append((not lost, f"Recruitment inbox: {len(oe)} events kept with your statuses, notes and match answers" if not lost
                    else f"Recruitment inbox events lost or changed: {len(lost)}"))
        for t, kc in (("rm_event_decisions", ["fingerprint", "status", "note"]), ("rm_match_decisions", ["name_norm", "club_norm", "uid", "verdict"])):
            o, n_ = old.get(t), new.get(t)
            lost = rows(o if o is not None else pd.DataFrame(), kc) - rows(n_ if n_ is not None else pd.DataFrame(), kc)
            res.append((not lost, f"{t}: {0 if o is None else len(o)} kept" if not lost else f"{t} lost: {len(lost)}"))
    ni = new["imports"].set_index("sha256")
    dec_of = {s: d for d in release.get("decisions", []) for s in d.get("sha256", [])}
    bad = []
    for r in old["imports"].itertuples():
        if r.sha256 in old["deleted"]:
            continue
        if r.sha256 not in ni.index:
            bad.append(f"{r.filename} missing")
            continue
        want, got = state_of(r), state_of(ni.loc[r.sha256].to_dict())
        d = dec_of.get(r.sha256)
        if d and want in froms(d):
            want = d["to"]
        if got != want:
            bad.append(f"{r.filename}: expected {want}, found {got}")
    ok = not bad and not missing
    res.append((ok, f"Files: all {len(old['imports'])} of yours present at their use level" if ok
                else "Files: " + "; ".join(bad[:6] + [f"{m} missing" for m in missing if f"{m} missing" not in bad])))
    gone = [s for s in old["deleted"] if s in ni.index]
    res.append((not gone, f"Removed files: {len(old['deleted'])} stay removed"))
    uo = {r.sha256: state_of(r) for r in old["imports"].itertuples()}
    for d in release.get("decisions", []):
        got = [state_of(ni.loc[s].to_dict()) for s in d["sha256"] if s in ni.index]
        want = [uo[s] if s in uo and uo[s] not in froms(d) else d["to"] for s in d["sha256"] if s in ni.index]
        ok = len(got) == len(d["sha256"]) and got == want
        res.append((ok, f"{d['label']}: {len(got)} of {len(d['sha256'])} files present, "
                        f"{', '.join(sorted(set(got))) or 'none'}"))
    res.append((new["version"] == release["version"], f"Data version {new['version']}"))
    return res


# ---------------------------------------------------------------------------
# Orchestration (parent process)
# ---------------------------------------------------------------------------
def say(msg=""):
    print(msg, flush=True)


def release_info(bundle: Path) -> dict:
    p = bundle / "data" / "RELEASE.json"
    return json.loads(p.read_text()) if p.exists() else {"version": "0", "decisions": []}


def upgrade(bundle: Path, home: Path, source_data: Path, in_place: bool) -> bool:
    """source_data: the data folder carried forward. in_place: it is the live data home (moved to the backup
    after verification); otherwise it belongs to an old installation and is only copied."""
    release = release_info(bundle)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stage = home / f".staging_{stamp}"
    stage.mkdir(parents=True)
    shutil.copytree(bundle / "data", stage / "data", ignore=SKIP)
    for f in (stage / "data").glob("engine_unreadable_*"):
        f.unlink()
    say("Merging your data into the new version. This takes a minute or two.")
    env = dict(os.environ, RIFC_TE_HOME=str(stage), PYTHONPATH=str(bundle))
    out = subprocess.run([sys.executable, "-m", "engine.upgrade", "--bundle", str(bundle),
                          "--merge-into", str(stage), "--user-data", str(source_data)],
                         env=env, cwd=str(bundle), capture_output=True, text=True)
    if out.returncode != 0:
        say("The upgrade stopped before changing anything. Your data is untouched.")
        say(out.stderr[-3000:])
        shutil.rmtree(stage, ignore_errors=True)
        return False
    result = json.loads((stage / "upgrade_result.json").read_text())
    checks = verify(source_data, stage / "data", release, result["missing"])
    say("Checks")
    for ok, txt in checks:
        say(("  OK    " if ok else "  FAIL  ") + txt)
    if not all(ok for ok, _ in checks):
        say("A check failed, so nothing was changed. Your data is untouched.")
        failed = home / "backups" / f"failed_upgrade_{stamp}"
        failed.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(stage), str(failed))
        say(f"The merged copy is kept for inspection in {failed}")
        return False
    backup = home / "backups" / f"pre_upgrade_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    live = home / "data"
    if in_place:
        shutil.move(str(live), str(backup / "data"))
    else:
        shutil.copytree(source_data, backup / "data",
                        ignore=shutil.ignore_patterns("identity_*.pkl", "discovery_run*.pkl"))
        (backup / "SOURCE.txt").write_text(f"Copied from {source_data}\n")
    shutil.move(str(stage / "data"), str(live))
    report = [f"Upgrade to version {release['version']} on {datetime.now():%d %b %Y %H:%M}",
              f"From: {source_data}", ""] + result["log"] + ["", "Checks"] + \
        [("OK    " if ok else "FAIL  ") + t for ok, t in checks]
    (backup / "upgrade_report.txt").write_text("\n".join(report) + "\n")
    shutil.copy2(backup / "upgrade_report.txt", live / "last_upgrade_report.txt")
    shutil.rmtree(stage, ignore_errors=True)
    say("Changes")
    for line in result["log"]:
        say("  " + line)
    say(f"Upgraded to version {release['version']}. Your previous data is saved in {backup}")
    return True


def ensure(bundle: Path, home: Path, assume_yes=False) -> int:
    release = release_info(bundle)
    live = home / "data"
    home.mkdir(parents=True, exist_ok=True)
    for old in home.glob(".staging_*"):      # an interrupted earlier attempt; the live data was never touched
        shutil.rmtree(old, ignore_errors=True)
    if (live / "engine.duckdb").exists():
        cur = read_state(live).get("version") or "0"
        if vkey(cur) == vkey(release["version"]):
            return 0
        if vkey(cur) > vkey(release["version"]):
            say(f"Your data is from version {cur}, newer than this app ({release['version']}). Nothing was changed. "
                "Use the newer app folder.")
            return 2
        say(f"Updating your data from version {cur} to {release['version']}.")
        return 0 if upgrade(bundle, home, live, in_place=True) else 1

    old = find_installations(bundle, home)
    choice = None
    if old:
        say("Found an existing installation with your data:")
        for i, p in enumerate(old, 1):
            try:
                s = summary(read_state(p / "data"))
            except Exception as exc:
                s = f"could not be read ({type(exc).__name__})"
            t = datetime.fromtimestamp((p / "data" / "engine.duckdb").stat().st_mtime)
            say(f"  {i}. {p}")
            say(f"     last used {t:%d %b %Y %H:%M}; {s}")
        say("If the old version is still open in another Terminal window, close that window first.")
        if assume_yes:
            choice = old[0]
        else:
            while True:
                try:
                    ans = input("Press Return to keep the data from 1, type another number, or type new for a "
                                "clean start: ").strip().lower()
                except EOFError:
                    ans = ""
                if ans in ("", "y", "yes"):
                    choice = old[0]
                    break
                if ans.isdigit() and 1 <= int(ans) <= len(old):
                    choice = old[int(ans) - 1]
                    break
                if ans == "new":
                    say("Clean start. The old folder stays where it is, unchanged.")
                    break
                say("Please press Return, type a number from the list, or type new.")
    if choice is not None:
        say(f"Carrying forward {choice / 'data'} (that folder is only read, never changed).")
        if not upgrade(bundle, home, choice / "data", in_place=False):
            return 1
        say(f"From now on your data lives in {home}. New versions find it automatically.")
        return 0
    say(f"Setting up your data folder in {home}")
    shutil.copytree(bundle / "data", live, ignore=SKIP)
    env = dict(os.environ, RIFC_TE_HOME=str(home), PYTHONPATH=str(bundle))
    subprocess.run([sys.executable, "-c", f"from engine.upgrade import set_meta; set_meta('data_version', "
                    f"{release['version']!r})"], env=env, cwd=str(bundle), check=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", required=True)
    ap.add_argument("--home", default=str(DEFAULT_HOME))
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--merge-into")
    ap.add_argument("--user-data")
    a = ap.parse_args()
    bundle = Path(a.bundle).expanduser().resolve()
    if a.merge_into:        # child process
        log = []
        res = merge_into(Path(a.merge_into), Path(a.user_data), release_info(bundle), log)
        (Path(a.merge_into) / "upgrade_result.json").write_text(json.dumps({**res, "log": log}))
        return 0
    return ensure(bundle, Path(a.home).expanduser(), a.yes)


if __name__ == "__main__":
    sys.exit(main())
