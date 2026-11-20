"""Dataset management: one row per league and season, validation checks, and three use levels.

Use levels (per league season; every file of that league season moves together):

  Full use            imports.active = TRUE. Feeds everything: player profiles and discovery, current
                      projections, the translation model's training pairs, the backtest and the Championship
                      benchmarks. Changing it makes the fitted model outdated until it is refitted.
  Profiles and        imports.active = FALSE, display_only = TRUE. Players and their seasons appear in
  discovery only      profiles, search and discovery (with projections from the existing model), labelled as
                      such. Never used for training pairs, the backtest or benchmarks, so the model stays valid.
  Excluded            both FALSE. Not used anywhere. Rows and the original file stay in the database and data/raw.

Nothing here deletes data or changes the statistical method.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import db
from .config import DATA_DIR, DB_PATH, MODEL_DIR

EXPORT_CAP = 500
LEVELS = ["Full use", "Profiles and discovery only", "Excluded"]
USES = ["Player profiles and search", "Recruitment discovery", "Current projections (from his records)",
        "Model training pairs", "Backtest", "Championship benchmarks"]
LEVEL_USES = {"Full use": USES,
              "Profiles and discovery only": USES[:3],
              "Excluded": []}


@dataclass
class Check:
    level: str          # pass, warning, fail
    name: str
    detail: str


@dataclass
class Dataset:
    league: str
    season: str
    imports: pd.DataFrame
    level: str
    checks: list = field(default_factory=list)
    replaced: pd.DataFrame | None = None

    @property
    def status(self) -> str:
        lv = {c.level for c in self.checks}
        return "Invalid" if "fail" in lv else "Valid with warnings" if "warning" in lv else "Valid"

    @property
    def headline(self) -> str:
        f = [c for c in self.checks if c.level == "fail"]
        return f[0].detail.split(". ")[0] + "." if f else ""


def _sheet_count(options_json) -> int | None:
    try:
        m = re.search(r"\((\d+)\)", json.loads(options_json or "{}").get("sheet") or "")
        return int(m.group(1)) if m else None
    except Exception:
        return None


def _bands(ages: pd.Series):
    a = ages.dropna()
    return (int(a.min()), int(a.max())) if len(a) else (None, None)


def load() -> list[Dataset]:
    im = db.query("SELECT * FROM imports WHERE dataset_type = 'player_season' ORDER BY import_id")
    if im.empty:
        return []
    ps = db.query("SELECT import_id, row_id, player_name, name_norm, team, age, birth_year, minutes, matches, league, "
                  "season FROM player_seasons")
    out = []
    if "replaced_by" not in im:
        im["replaced_by"] = None
    for (lg, ss), g0 in im.groupby(["file_league", "file_season"], sort=True):
        g = g0[g0["replaced_by"].isna()]
        if g.empty:
            continue
        if g["active"].all():
            level = "Full use"
        elif g["display_only"].fillna(False).all():
            level = "Profiles and discovery only"
        elif not g["active"].any():
            level = "Excluded"
        else:
            level = "Mixed"
        d = Dataset(lg, str(ss), g, level)
        d.replaced = g0[g0["replaced_by"].notna()]
        d.checks = validate(g, ps, im[im["replaced_by"].isna()])
        out.append(d)
    return out


def validate(g: pd.DataFrame, ps: pd.DataFrame, im: pd.DataFrame) -> list[Check]:
    """Checks for one league season (all its files), compared with the same league's other seasons."""
    checks = []
    lg, ss = g["file_league"].iloc[0], str(g["file_season"].iloc[0])
    rows = ps[ps["import_id"].isin(g["import_id"])]
    # 1. export cap
    capped = []
    for r in g.itertuples():
        sc = _sheet_count(r.options_json)
        if r.n_rows == EXPORT_CAP or sc == EXPORT_CAP:
            a, b = _bands(rows[rows["import_id"] == r.import_id]["age"])
            capped.append(f"{r.filename} (ages {a} to {b}, exactly {EXPORT_CAP} rows)")
    if capped:
        checks.append(Check("fail", "Export cap", "A file hit the Wyscout export limit of 500 rows, so an unknown "
                            "number of players is missing: " + "; ".join(capped) + ". Wyscout sorts exports by market "
                            "value, so the rows cut are players with a market value of 0, not a random sample."))
    else:
        checks.append(Check("pass", "Export cap", "No file hit the 500 row export limit."))
    # 2. age coverage: bands contiguous
    bands = sorted(_bands(rows[rows["import_id"] == i]["age"]) for i in g["import_id"])
    bands = [b for b in bands if b[0] is not None]
    gaps = [f"{a[1]} to {b[0]}" for a, b in zip(bands, bands[1:]) if b[0] > a[1] + 1]
    checks.append(Check("fail" if gaps else "pass", "Age coverage",
                        ("Gap between age bands: " + ", ".join(gaps)) if gaps else
                        f"Age bands {', '.join(f'{a} to {b}' for a, b in bands)} cover every age with no gap."))
    # 3. duplicates: rows the importer skipped, exact repeats left, and near duplicate profiles
    ids = ",".join(str(int(i)) for i in g["import_id"])
    skipped = db.query(f"SELECT message FROM validation_log WHERE import_id IN ({ids}) AND message LIKE 'Skipped %'")
    n_skip = sum(int(re.search(r"Skipped (\d+)", m).group(1)) for m in skipped["message"]) if not skipped.empty else 0
    from .transfers import drop_duplicate_profiles
    full = db.query(f"SELECT * FROM player_seasons WHERE import_id IN ({ids})")
    kept, dropped = drop_duplicate_profiles(full) if not full.empty else (full, pd.DataFrame())
    n_prof = len(dropped)
    dup, n_age = 0, 0
    if not kept.empty:
        k = ["name_norm", "team", "minutes", "matches"]
        dd = kept[kept.duplicated(k, keep=False)].sort_values(k + ["import_id"])
        for _, h in dd.groupby(k):
            by = h["birth_year"].dropna()
            if h["import_id"].nunique() > 1 and (by.max() - by.min() <= 1 if len(by) else True):
                n_age += len(h) - 1      # same player in two overlapping age band files, ages one year apart
            else:
                dup += len(h) - 1
    near = near_duplicates(rows)
    txt = (f"{n_skip} rows were exact copies of rows in an overlapping file and were skipped on import. "
           if n_skip else "No overlapping rows had to be skipped on import. ")
    if n_prof:
        txt += f"{n_prof} rows repeat another Wyscout profile's season stats; one copy is used. "
    if n_age:
        txt += (f"{n_age} rows appear in two overlapping age band files with ages one year apart (not caught as exact "
                "copies); player linking joins them and the season counts once. ")
    txt += f"{dup} rows repeat a club and minutes with a different player's details; kept as separate players. " \
        if dup else ""
    if not near.empty:
        txt += ("Possible duplicate profiles (same club, birth year and matches, minutes within 2%): " +
                "; ".join(f"{a} and {b} ({t})" for a, b, t in near[["player_name", "player_name_b", "team"]].values)
                + ". Shown once in profiles.")
    checks.append(Check("warning" if not near.empty else "pass", "Duplicates", txt.strip()))
    # 4. schema vs the league's other seasons
    others = im[(im["file_league"] == lg) & (im["file_season"].astype(str) != ss) & im["active"]]
    tg = lambda df: set().union(*[{m["target"] for m in json.loads(x or "[]") if m.get("target") != "(ignore)"}
                                  for x in df["mapping_json"]]) if len(df) else set()
    mine, ref = tg(g), tg(others)
    miss = sorted(ref - mine)
    checks.append(Check("warning" if miss else "pass", "Columns",
                        f"Missing fields that {lg} other seasons have: {', '.join(miss)}." if miss else
                        f"Same {len(mine)} mapped fields as the other {lg} seasons."))
    # 5. player and team counts vs other seasons
    oth = ps[(ps["league"] == lg) & (ps["season"].astype(str) != ss) & ps["import_id"].isin(others["import_id"])]
    if not oth.empty:
        per = oth.drop_duplicates(["name_norm", "team", "minutes", "season"]).groupby("season").size()
        n = len(rows.drop_duplicates(["name_norm", "team", "minutes"]))
        lo, hi = per.min(), per.max()
        lvl = "warning" if n < 0.85 * lo or n > 1.25 * hi else "pass"
        checks.append(Check(lvl, "Player count", f"{n:,} players; other seasons {lo:,} to {hi:,}."))
        tm, tmo = rows["team"].nunique(), oth.groupby("season")["team"].nunique()
        checks.append(Check("warning" if tm < tmo.min() - 1 else "pass", "Clubs",
                            f"{tm} clubs; other seasons {tmo.min()} to {tmo.max()}."))
        mm = rows.groupby("team")["matches"].max().median()
        mmo = oth.groupby(["season", "team"])["matches"].max().groupby("season").median().median()
        checks.append(Check("warning" if mm < 0.85 * mmo else "pass", "Season completeness",
                            f"Median club has players with up to {mm:.0f} matches; other seasons {mmo:.0f}."))
    # 6. import validation log errors
    log = db.query("SELECT level, message FROM validation_log WHERE import_id IN (" +
                   ",".join(str(int(i)) for i in g["import_id"]) + ") AND level = 'error'")
    if not log.empty:
        checks.append(Check("fail", "Import errors", "; ".join(log["message"].head(3))))
    return checks


def near_duplicates(rows: pd.DataFrame) -> pd.DataFrame:
    """Two rows that are almost certainly one player under two Wyscout profiles: same club and league season,
    same name ignoring spaces and dots, same birth year and matches, minutes within 2%."""
    need = {"player_name", "team", "minutes"}
    if rows.empty or not need <= set(rows.columns):
        return pd.DataFrame(columns=["player_name", "player_name_b", "team"])
    r = rows.copy()
    r["_k"] = r["player_name"].astype(str).str.lower().str.replace(r"[^a-z]", "", regex=True)
    r["_i"] = np.arange(len(r))
    cols = [c for c in ("birth_year", "matches") if c in r]
    m = r.merge(r, on=["_k", "team"] + cols, suffixes=("", "_b"))
    m = m[(m["_i"] < m["_i_b"]) & ((m["minutes"] - m["minutes_b"]).abs() <= np.maximum(2, 0.02 * m["minutes"]))]
    return m[["player_name", "player_name_b", "team"] + [c for c in ("row_id", "row_id_b", "minutes", "minutes_b")
                                                          if c in m]]


# ---------------------------------------------------------------------------
# Changing the use level
# ---------------------------------------------------------------------------
def set_level(ds: Dataset, level: str, reason: str = "", override: str = "") -> str:
    """Moves every file of the league season to the level. Full use of an Invalid dataset needs an override note.
    Returns a short description of what changed."""
    if level == "Full use" and ds.status == "Invalid" and not override.strip():
        raise ValueError("This dataset failed validation. Full use needs a written override reason.")
    ids = [int(i) for i in ds.imports["import_id"]]
    note = override.strip() or reason.strip() or None
    with db.connect() as con:
        for i in ids:
            if level == "Full use":
                con.execute("UPDATE imports SET active = TRUE, display_only = FALSE, exclusion_reason = NULL, "
                            "scope_note = ? WHERE import_id = ?", [("Override: " + override) if override else None, i])
            elif level == "Profiles and discovery only":
                con.execute("UPDATE imports SET active = FALSE, display_only = TRUE, scope_note = ?, "
                            "exclusion_reason = COALESCE(exclusion_reason, ?) WHERE import_id = ?",
                            [note, ds.headline or "Shown in profiles and discovery only", i])
            else:
                con.execute("UPDATE imports SET active = FALSE, display_only = FALSE, exclusion_reason = ?, "
                            "scope_note = NULL WHERE import_id = ?", [reason or ds.headline or "Excluded by user", i])
    try:
        from .rebuild import write_manifest
        write_manifest()
    except Exception:
        pass
    return f"{ds.league} {ds.season}: {ds.level} to {level}."


def affects_model(old: str, new: str) -> bool:
    return (old == "Full use") != (new == "Full use")


def backup(tag: str) -> Path:
    """Copy of the database and fitted models before a refit (nothing is overwritten)."""
    root = DATA_DIR.parent / "backups" / f"{tag}_{datetime.now():%Y%m%d_%H%M%S}"
    root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(DB_PATH, root / DB_PATH.name)
    shutil.copytree(MODEL_DIR, root / "models")
    for f in ("import_manifest.json", "identity_overrides.json", "identity_decisions.json"):
        if (DATA_DIR / f).exists():
            shutil.copy2(DATA_DIR / f, root / f)
    return root


# ---------------------------------------------------------------------------
# Replacing files of a league season (for example a capped age band re-exported in smaller pieces)
# ---------------------------------------------------------------------------
def preview_replacement(ds: Dataset, replace_ids: list[int], uploads: list[tuple[str, bytes]]) -> dict:
    """Validate the season as it would be after the replacement, without writing anything."""
    from .ingest import BUILDERS, ImportOptions, auto_map, exact_keys, read_file
    keep = ds.imports[~ds.imports["import_id"].isin(replace_ids)]
    ps = db.query("SELECT import_id, row_id, player_name, name_norm, team, age, birth_year, minutes, matches, league, "
                  "season, provider, goals_total FROM player_seasons")
    kept_rows = ps[ps["import_id"].isin(keep["import_id"])]
    old_rows = ps[ps["import_id"].isin(replace_ids)]
    tmpl = ds.imports[ds.imports["import_id"].isin(replace_ids)].iloc[0] if replace_ids else ds.imports.iloc[0]
    o = json.loads(tmpl["options_json"] or "{}")
    opt = ImportOptions(dataset_type="player_season", provider=tmpl["provider"], file_league=ds.league,
                        file_season=ds.season, age_reference=o.get("age_reference", "season"),
                        export_year=o.get("export_year"), notes="Replacement export")
    files, new_frames, problems = [], [], []
    for name, data in uploads:
        sheets = read_file(data, name)
        sheet = next(iter(sheets))
        raw = sheets[sheet]
        maps = auto_map(list(raw.columns), "player_season")
        res = BUILDERS["player_season"](raw, maps, opt)
        f = res.frame
        a, b = _bands(f["age"]) if "age" in f else (None, None)
        files.append({"file": name, "rows": len(f), "ages": f"{a} to {b}", "capped": len(f) >= EXPORT_CAP or
                      _sheet_count(json.dumps({"sheet": sheet})) == EXPORT_CAP})
        if len(f) >= EXPORT_CAP:
            problems.append(f"{name} has {len(f)} rows: it hit the export limit again; split it further.")
        new_frames.append(f)
    new = pd.concat(new_frames, ignore_index=True) if new_frames else pd.DataFrame()
    if new.empty:
        return {"ok": False, "files": files, "problems": ["No rows in the uploaded files."]}
    k_new, k_kept, k_old = exact_keys(new), set(exact_keys(kept_rows)) if len(kept_rows) else set(), \
        set(exact_keys(old_rows)) if len(old_rows) else set()
    overlap_kept = int(k_new.isin(k_kept).sum())
    within = int(k_new.duplicated().sum())
    added_rows = int((~k_new.isin(k_kept) & ~k_new.duplicated()).sum())
    found_old = len(k_old & set(k_new)) if k_old else 0
    # age coverage of the merged season
    bands = sorted([_bands(kept_rows[kept_rows["import_id"] == i]["age"]) for i in keep["import_id"]]
                   + [_bands(f["age"]) for f in new_frames])
    bands = [b for b in bands if b[0] is not None]
    gaps = [f"{a[1]} to {b[0]}" for a, b in zip(bands, bands[1:]) if b[0] > a[1] + 1]
    if gaps:
        problems.append("Age gap in the merged season: " + ", ".join(gaps) + ".")
    if old_rows is not None and len(old_rows) and found_old < 0.95 * len(old_rows):
        problems.append(f"Only {found_old} of the {len(old_rows)} rows of the replaced file appear unchanged in the new "
                        "files. Expected nearly all of them (same export date); check the age band and season.")
    merged_players = len(kept_rows.drop_duplicates(["name_norm", "team", "minutes"])) + added_rows
    near = near_duplicates(pd.concat([kept_rows, new.assign(row_id=-1)], ignore_index=True))
    return {"ok": not problems, "files": files, "problems": problems, "overlap_with_kept": overlap_kept,
            "repeats_inside_new": within, "rows_added": added_rows, "old_rows": len(old_rows),
            "old_rows_found": found_old, "new_players_not_in_old": max(0, added_rows - found_old),
            "merged_players": merged_players, "bands": bands, "near_duplicates": len(near)}


def commit_replacement(ds: Dataset, replace_ids: list[int], uploads: list[tuple[str, bytes]], tmp_dir: Path) -> list[int]:
    """Imports the new files (deduplicated against the files that stay, never against the replaced ones), gives
    them the season's current use level, and marks the replaced files as replaced and excluded. Nothing is deleted."""
    from .ingest import ImportOptions, import_file
    tmpl = ds.imports[ds.imports["import_id"].isin(replace_ids)].iloc[0] if replace_ids else ds.imports.iloc[0]
    o = json.loads(tmpl["options_json"] or "{}")
    level = ds.level
    new_ids = []
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for name, data in uploads:
        p = tmp_dir / name
        p.write_bytes(data)
        opt = ImportOptions(dataset_type="player_season", provider=tmpl["provider"], file_league=ds.league,
                            file_season=ds.season, age_reference=o.get("age_reference", "season"),
                            export_year=o.get("export_year"),
                            notes=f"Replacement for import {', '.join(map(str, replace_ids))}")
        iid, _ = import_file(p, opt, exclude_imports=replace_ids)
        new_ids.append(iid)
    with db.connect() as con:
        for i in new_ids:   # same level as the season had; Full use only through set_level after validation
            if level == "Full use":
                con.execute("UPDATE imports SET active = FALSE, display_only = TRUE, exclusion_reason = ? WHERE "
                            "import_id = ?", ["New files: validate the merged season before Full use.", i])
            else:
                con.execute("UPDATE imports SET active = FALSE, display_only = ?, exclusion_reason = ? WHERE "
                            "import_id = ?", [level.startswith("Profiles"), "Replacement files; validate the merged "
                                              "season before Full use.", i])
        for i in replace_ids:
            con.execute("UPDATE imports SET active = FALSE, display_only = FALSE, replaced_by = ?, exclusion_reason = ? "
                        "WHERE import_id = ?", [",".join(map(str, new_ids)), f"Replaced by import "
                                                f"{', '.join(map(str, new_ids))}", int(i)])
        if level == "Full use":   # a season in Full use drops to display only until it is validated again
            for i in ds.imports["import_id"]:
                if int(i) not in replace_ids:
                    con.execute("UPDATE imports SET active = FALSE, display_only = TRUE, exclusion_reason = ? WHERE "
                                "import_id = ?", ["Season files replaced; validate before Full use again.", int(i)])
    try:
        from .rebuild import write_manifest
        write_manifest()
    except Exception:
        pass
    return new_ids
