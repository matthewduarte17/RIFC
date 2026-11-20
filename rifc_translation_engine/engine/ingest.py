"""Phase 1: read, map, normalise, validate and store uploaded datasets."""
from __future__ import annotations

import difflib
import hashlib
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import db
from .config import RAW_DIR
from .normalize import (norm_header, norm_name, norm_team, parse_age, parse_height,
                        parse_season, to_number)
from .schema import (DATASET_TYPES, DERIVED_SUCCESS_PAIRS, METRICS, SUPPORT_FIELDS,
                     position_group)

IGNORE = "(ignore)"


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------
def read_file(data: bytes, filename: str) -> dict[str, pd.DataFrame]:
    """Return {sheet name: DataFrame}. CSV files give a single 'data' sheet."""
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, dtype=object)
        return {k: _clean_frame(v) for k, v in sheets.items() if not v.empty}
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sep = max([",", ";", "\t"], key=lambda s: text.splitlines()[0].count(s) if text else 0)
    df = pd.read_csv(io.StringIO(text), sep=sep, dtype=object)
    return {"data": _clean_frame(df)}


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    # FBref exports repeat the header row inside the table
    if len(df.columns):
        first = df.columns[0]
        df = df[df[first].astype(str).str.strip() != first]
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Column mapping
# ---------------------------------------------------------------------------
@dataclass
class ColumnMap:
    source: str
    target: str = IGNORE
    unit: str = "value"
    confidence: str = ""


def target_options(dataset_type: str) -> list[str]:
    aliases = DATASET_TYPES[dataset_type][1]
    seen = []
    for a in aliases:
        if a.target not in seen:
            seen.append(a.target)
    return [IGNORE] + seen


def auto_map(columns: list[str], dataset_type: str) -> list[ColumnMap]:
    """Assign columns to canonical fields.

    Aliases are tried in priority order, so when a file has both 'Team' and
    'Team within selected timeframe' (Wyscout), the season team wins and 'Team'
    falls through to the current club field.
    """
    aliases = DATASET_TYPES[dataset_type][1]
    heads = {col: norm_header(col) for col in columns}
    out = {col: ColumnMap(col) for col in columns}
    taken_cols, taken_targets = set(), set()

    def assign(col, target, unit, how):
        out[col].target, out[col].unit, out[col].confidence = target, unit, how
        taken_cols.add(col)
        taken_targets.add((target, unit))

    for a in aliases:                       # exact matches, alias priority order
        if (a.target, a.unit) in taken_targets:
            continue
        for n in a.names:
            hit = next((c for c in columns if c not in taken_cols and heads[c] == norm_header(n)), None)
            if hit is not None:
                assign(hit, a.target, a.unit, "exact")
                break
    lookup = {}
    for a in aliases:
        for n in a.names:
            lookup.setdefault(norm_header(n), (a.target, a.unit))
    for col in columns:                     # FBref per 90 block duplicates ('Gls.1')
        h = heads[col]
        if col in taken_cols or not h.endswith(" 1"):
            continue
        base = lookup.get(h[:-2])
        if base and base[1] == "total" and (base[0], "p90") not in taken_targets:
            assign(col, base[0], "p90", "per 90 block")
    for col in columns:                     # close spellings, flagged for review
        if col in taken_cols:
            continue
        h = heads[col]
        close = difflib.get_close_matches(h, list(lookup), n=1, cutoff=0.92)
        # never let a % column fuzzy match a count, or a per 90 column match a total
        if close and (("%" in h) != ("%" in close[0]) or ("90" in h) != ("90" in close[0])):
            close = None
        if close and lookup[close[0]] not in taken_targets:
            assign(col, *lookup[close[0]], f"fuzzy: '{close[0]}' (check)")
    return [out[c] for c in columns]


def mapping_to_frame(maps: list[ColumnMap], df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "source column": [m.source for m in maps],
        "example": [str(df[m.source].dropna().iloc[0]) if df[m.source].notna().any() else "" for m in maps],
        "maps to": [m.target for m in maps],
        "unit": [m.unit for m in maps],
        "detection": [m.confidence for m in maps],
    })


def frame_to_mapping(frame: pd.DataFrame) -> list[ColumnMap]:
    return [ColumnMap(r["source column"], r["maps to"], r["unit"], r.get("detection", ""))
            for _, r in frame.iterrows()]


# ---------------------------------------------------------------------------
# Building canonical rows
# ---------------------------------------------------------------------------
@dataclass
class ImportOptions:
    dataset_type: str = "player_season"
    provider: str = "Wyscout"
    file_league: str | None = None   # used when the file has no league column
    file_season: str | None = None   # used when the file has no season column
    age_reference: str = "season"    # 'season' or 'export'
    export_year: float | None = None  # decimal year of the export when age_reference == 'export'
    notes: str = ""


@dataclass
class Issue:
    level: str        # error / warning / info
    check: str
    message: str
    n: int = 0


@dataclass
class BuildResult:
    frame: pd.DataFrame
    issues: list = field(default_factory=list)


def _col(df, maps, target, unit=None):
    for m in maps:
        if m.target == target and (unit is None or m.unit == unit):
            return df[m.source]
    return None


def build_player_rows(df: pd.DataFrame, maps: list[ColumnMap], opt: ImportOptions) -> BuildResult:
    issues: list[Issue] = []
    out = pd.DataFrame(index=df.index)
    out["provider"] = opt.provider

    def text(target):
        s = _col(df, maps, target)
        return s.astype(object).where(s.notna(), None) if s is not None else pd.Series(None, index=df.index, dtype=object)

    out["player_name"] = text("player_name").map(lambda v: str(v).strip() if v is not None else None)
    out["name_norm"] = out["player_name"].map(norm_name)
    out["provider_player_id"] = text("provider_player_id")
    out["team"] = text("team")
    out["nationality"] = text("nationality")
    out["position_raw"] = text("position_raw")
    out["position_group"] = out["position_raw"].map(position_group)
    out["foot"] = text("foot")
    out["current_team"] = text("current_team")
    ce = _col(df, maps, "contract_expires")
    out["contract_expires"] = pd.to_datetime(ce, errors="coerce").dt.strftime("%Y-%m-%d") if ce is not None else None
    h = _col(df, maps, "height_cm")
    out["height_cm"] = parse_height(h) if h is not None else np.nan

    lg = _col(df, maps, "league")
    out["league"] = lg.astype(object).where(lg.notna(), opt.file_league) if lg is not None else opt.file_league
    out["league"] = out["league"].map(lambda v: str(v).strip() if v is not None else None)
    ss = _col(df, maps, "season")
    season_raw = ss if ss is not None else pd.Series(opt.file_season, index=df.index)
    parsed = season_raw.map(parse_season)
    out["season"] = parsed.map(lambda p: p[0])
    out["season_start"] = parsed.map(lambda p: p[1]).astype(float)
    out["season_end"] = parsed.map(lambda p: p[2]).astype(float)

    for f in ("minutes", "matches", "starts"):
        s = _col(df, maps, f)
        out[f] = to_number(s) if s is not None else np.nan

    # Age and birth year
    bd = _col(df, maps, "birth_date")
    out["birth_date"] = pd.to_datetime(bd, errors="coerce") if bd is not None else pd.NaT
    ag = _col(df, maps, "age")
    out["age"] = ag.map(parse_age).astype(float) if ag is not None else np.nan
    ref = (np.floor(out["season_start"]) + 0.5) if opt.age_reference == "season" else (opt.export_year or np.nan)
    by_from_age = np.floor(ref - out["age"] - 0.5)  # best guess, +/- 1
    out["birth_year"] = out["birth_date"].dt.year.where(out["birth_date"].notna(), by_from_age)
    out["age_at_season"] = (np.floor(out["season_start"]) + 0.5) - out["birth_year"] - 0.5
    out.loc[out["birth_date"].notna(), "age_at_season"] = (
        (np.floor(out["season_start"]) + 0.5) - (out["birth_date"].dt.year + out["birth_date"].dt.dayofyear / 365.25)
    )
    out["player_key"] = out["name_norm"].fillna("?") + "|" + out["birth_year"].map(
        lambda v: "" if pd.isna(v) else str(int(v)))

    # Metrics
    mins = out["minutes"]
    normalized_headers = {norm_header(c): c for c in df.columns}
    for m in list(METRICS) + SUPPORT_FIELDS:
        kind = METRICS[m].kind if m in METRICS else "rate"
        tot = _col(df, maps, m, "total")
        p90 = _col(df, maps, m, "p90")
        pct = _col(df, maps, m, "pct")
        tot = to_number(tot) if tot is not None else None
        p90 = to_number(p90) if p90 is not None else None
        if kind == "pct":
            v = to_number(pct) if pct is not None else (p90 if p90 is not None else tot)
            if v is not None and v.dropna().between(0, 1.0001).all() and v.dropna().max() <= 1:
                v = v * 100  # stored as fraction
            out[f"{m}_p90"] = v if v is not None else np.nan
            out[f"{m}_total"] = np.nan
            continue
        if p90 is None and tot is not None:
            p90 = tot / mins.where(mins > 0) * 90
        if tot is None and p90 is not None:
            tot = p90 * mins / 90
        if tot is not None and p90 is not None and _col(df, maps, m, "total") is not None \
                and _col(df, maps, m, "p90") is not None:
            calc = to_number(_col(df, maps, m, "total")) / mins.where(mins > 0) * 90
            given = to_number(_col(df, maps, m, "p90"))
            ok = (calc.notna() & given.notna() & (mins >= 270))
            if ok.sum() >= 5:
                rel = ((calc[ok] - given[ok]).abs() / given[ok].abs().clip(lower=0.05)).median()
                if rel > 0.10:
                    issues.append(Issue("warning", "definition check",
                                        f"{METRICS.get(m, m) if m not in METRICS else METRICS[m].label}: "
                                        f"total and per 90 columns disagree (median gap {rel:.0%}). "
                                        "Per 90 column kept. Check whether the per 90 figure is adjusted.",
                                        int(ok.sum())))
        out[f"{m}_p90"] = p90 if p90 is not None else np.nan
        out[f"{m}_total"] = tot if tot is not None else np.nan

    # Derived metrics, only where the arithmetic is exact
    if out["goal_contributions_p90"].isna().all() and out["goals_p90"].notna().any() and out["assists_p90"].notna().any():
        out["goal_contributions_p90"] = out["goals_p90"] + out["assists_p90"]
        out["goal_contributions_total"] = out["goals_total"] + out["assists_total"]
        issues.append(Issue("info", "derived", "Goal contributions derived as goals + assists."))
    if out["penalty_goals_total"].isna().all() and out["goals_total"].notna().any() and \
            _col(df, maps, "np_goals", "total") is not None:
        out["penalty_goals_total"] = out["goals_total"] - out["np_goals_total"]
        out["penalty_goals_p90"] = out["penalty_goals_total"] / mins.where(mins > 0) * 90
        issues.append(Issue("info", "derived", "Penalty goals derived as goals minus non penalty goals."))
    if out["np_goals_p90"].isna().all() and out["goals_total"].notna().any() and out["penalty_goals_total"].notna().any():
        out["np_goals_total"] = out["goals_total"] - out["penalty_goals_total"]
        out["np_goals_p90"] = out["np_goals_total"] / mins.where(mins > 0) * 90
        issues.append(Issue("info", "derived", "Non penalty goals derived as goals minus penalty goals."))
    for vol_h, pct_h, target in DERIVED_SUCCESS_PAIRS:
        if out[f"{target}_p90"].isna().all() and norm_header(vol_h) in normalized_headers \
                and norm_header(pct_h) in normalized_headers:
            vol = to_number(df[normalized_headers[norm_header(vol_h)]])
            pc = to_number(df[normalized_headers[norm_header(pct_h)]])
            out[f"{target}_p90"] = vol * pc / 100
            out[f"{target}_total"] = out[f"{target}_p90"] * mins / 90
            issues.append(Issue("info", "derived",
                                f"{METRICS[target].label} derived from '{normalized_headers[norm_header(vol_h)]}' x "
                                f"'{normalized_headers[norm_header(pct_h)]}'."))
    return BuildResult(out, issues)


def build_team_rows(df, maps, opt: ImportOptions) -> BuildResult:
    out = pd.DataFrame(index=df.index)
    out["provider"] = opt.provider
    t = _col(df, maps, "team")
    out["team"] = t.astype(str).str.strip() if t is not None else None
    out["team_norm"] = out["team"].map(norm_team)
    lg = _col(df, maps, "league")
    out["league"] = lg.astype(str).str.strip() if lg is not None else opt.file_league
    ss = _col(df, maps, "season")
    parsed = (ss if ss is not None else pd.Series(opt.file_season, index=df.index)).map(parse_season)
    out["season"] = parsed.map(lambda p: p[0])
    out["season_start"] = parsed.map(lambda p: p[1]).astype(float)
    out["season_end"] = parsed.map(lambda p: p[2]).astype(float)
    for f in ("matches", "goals_for", "goals_against", "xg_for", "xg_against", "shots_for",
              "possession_pct", "penalties_awarded", "penalties_scored"):
        s = _col(df, maps, f)
        out[f] = to_number(s) if s is not None else np.nan
    fm = _col(df, maps, "formation")
    out["formation"] = fm.astype(str) if fm is not None else None
    return BuildResult(out, [])


def build_transfer_rows(df, maps, opt: ImportOptions) -> BuildResult:
    out = pd.DataFrame(index=df.index)
    for f in ("player_name", "from_team", "from_league", "from_season", "to_team", "to_league",
              "to_season", "transfer_date", "transfer_type"):
        s = _col(df, maps, f)
        out[f] = s.astype(object).where(s.notna(), None) if s is not None else None
    out["name_norm"] = out["player_name"].map(norm_name)
    for f in ("from_season", "to_season"):
        out[f] = out[f].map(lambda v: parse_season(v)[0])
    bd = _col(df, maps, "birth_date")
    out["birth_date"] = pd.to_datetime(bd, errors="coerce") if bd is not None else pd.NaT
    return BuildResult(out, [])


BUILDERS = {"player_season": build_player_rows, "team_season": build_team_rows, "transfer": build_transfer_rows}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
REQUIRED = {
    "player_season": ["player_name", "team", "league", "season", "minutes", "position_raw"],
    "team_season": ["team", "league", "season", "matches"],
    "transfer": ["player_name", "from_league", "to_league", "to_season"],
}


def validate(res: BuildResult, opt: ImportOptions, sha: str | None = None, db_path=None) -> list[Issue]:
    df, issues = res.frame, list(res.issues)
    n = len(df)
    for f in REQUIRED[opt.dataset_type]:
        if f not in df or df[f].isna().all():
            issues.append(Issue("error", "required field", f"Required field '{f}' is missing. Map a column "
                                "or set it for the whole file.", n))
        elif df[f].isna().any():
            issues.append(Issue("warning", "missing values", f"'{f}' is blank in {int(df[f].isna().sum())} rows.",
                                int(df[f].isna().sum())))
    if opt.dataset_type == "player_season":
        if df["season_start"].isna().any():
            issues.append(Issue("error", "season format", "Some season labels could not be read. Use '2025' or "
                                "'2024-25'.", int(df["season_start"].isna().sum())))
        unk = df["position_raw"].notna() & df["position_group"].isna()
        if unk.any():
            ex = ", ".join(sorted(df.loc[unk, "position_raw"].astype(str).unique())[:6])
            issues.append(Issue("warning", "position labels", f"{int(unk.sum())} rows have positions that could not "
                                f"be grouped ({ex}). They are stored but left out of position models.", int(unk.sum())))
        small = df["minutes"].fillna(0) < 450
        if small.any():
            issues.append(Issue("info", "small samples", f"{int(small.sum())} rows under 450 minutes. Stored, but "
                                "excluded from model pairs by default.", int(small.sum())))
        dup_cols = ["name_norm", "team", "league", "season"]
        dups = df.duplicated(subset=dup_cols, keep=False) & df["name_norm"].notna()
        if dups.any():
            ex = ", ".join(sorted(df.loc[dups, "player_name"].astype(str).unique())[:6])
            issues.append(Issue("warning", "duplicates", f"{int(dups.sum())} rows share a name and team in this "
                                f"file ({ex}). Usually different players with the same short name; they are kept "
                                "as separate players and never merged with each other.", int(dups.sum())))
        within = exact_keys(df).duplicated()
        if within.any():
            issues.append(Issue("info", "duplicates", f"{int(within.sum())} rows are exact repeats inside this file; "
                                "one copy of each is kept.", int(within.sum())))
        if len(df) in (500, 1000, 2000):
            issues.append(Issue("warning", "possible truncation", f"The file has exactly {len(df)} rows, a common "
                                "export limit. Some players may be missing; export this group in smaller pieces "
                                "(e.g. narrower age bands) to be sure.", len(df)))
        no_id = df["birth_date"].isna() & df["age"].isna()
        if no_id.mean() > 0.2:
            issues.append(Issue("warning", "identity", "No birth date or age for many rows. Linking the same "
                                "player across leagues will rely on name only and may miss or merge players.",
                                int(no_id.sum())))
        if df["birth_date"].isna().all() and df["age"].notna().any():
            issues.append(Issue("info", "identity", f"Birth year estimated from age (age reference: "
                                f"{opt.age_reference}). Linking allows a one year tolerance."))
        too_many = df["minutes"] > df["matches"].fillna(60) * 125
        if too_many.any():
            issues.append(Issue("warning", "implausible minutes", "Minutes exceed 125 per match (more than a "
                                "full match with extra time). Check minutes and matches columns.",
                                int(too_many.sum())))
        coverage = []
        for m, meta in METRICS.items():
            col = df[f"{m}_p90"]
            if col.notna().any():
                miss = col.isna().mean()
                coverage.append(m)
                if miss > 0.2:
                    issues.append(Issue("warning", "missing values", f"{meta.label}: blank in {miss:.0%} of rows.",
                                        int(col.isna().sum())))
                if meta.kind == "pct" and ((col < 0) | (col > 100)).any():
                    issues.append(Issue("error", "range", f"{meta.label}: values outside 0 to 100.",
                                        int(((col < 0) | (col > 100)).sum())))
                if meta.kind == "rate" and (col < 0).any():
                    issues.append(Issue("error", "range", f"{meta.label}: negative values.", int((col < 0).sum())))
        issues.append(Issue("info", "coverage", f"Metrics available: {', '.join(METRICS[m].label for m in coverage) or 'none'}."))
        # Overlap with existing data
        try:
            existing = db.query("SELECT DISTINCT league, season, provider FROM player_seasons", path=db_path)
            ls = df[["league", "season"]].drop_duplicates()
            same = existing.merge(ls, on=["league", "season"])
            if not same.empty:
                provs = set(same["provider"])
                if opt.provider in provs:
                    keys = exact_keys(df)
                    n_exact = int(keys.isin(existing_exact_keys(df, db_path)).sum())
                    if n_exact:
                        issues.append(Issue("info", "duplicates", f"{n_exact} rows are identical to rows already "
                                            "imported (same player, team, age, minutes, goals), e.g. overlapping "
                                            "age bands. They will be skipped.", n_exact))
                    old = db.query("SELECT name_norm, team, league, season FROM player_seasons WHERE provider = ?",
                                   [opt.provider], path=db_path)
                    key = ["name_norm", "team", "league", "season"]
                    overlap = df[key].merge(old.drop_duplicates(), on=key, how="inner")
                    conflict = len(overlap) - n_exact
                    if conflict > 0.2 * len(df):
                        issues.append(Issue("warning", "duplicates", f"{conflict} rows match a player and team already "
                                            "imported for this season but with different numbers. This looks like a "
                                            "second, different export of the same league season.", conflict))
                    elif not n_exact:
                        issues.append(Issue("info", "duplicates", "Adds to a league season already imported "
                                            "(e.g. another age band)."))
                if provs - {opt.provider}:
                    issues.append(Issue("warning", "definitions", "This league and season already exists from "
                                        f"{', '.join(provs - {opt.provider})}. Provider specific metrics are only "
                                        "compared within the same provider."))
        except Exception:
            pass
    if sha:
        prev = db.query("SELECT import_id, filename FROM imports WHERE sha256 = ?", [sha], path=db_path)
        if not prev.empty:
            issues.append(Issue("error", "duplicates", f"This exact file was already imported "
                                f"(import #{int(prev.iloc[0, 0])}, {prev.iloc[0, 1]})."))
    return issues


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------
EXACT_KEY = ["name_norm", "team", "league", "season", "provider", "age", "minutes", "matches", "goals_total"]


def exact_keys(df: pd.DataFrame) -> pd.Series:
    """Identity of a row for duplicate detection across overlapping exports (e.g. age bands)."""
    parts = []
    for c in EXACT_KEY:
        col = df[c] if c in df else pd.Series(None, index=df.index)
        if col.dtype.kind in "fiu":
            col = col.astype(float).round(2)   # int and float columns must render the same way
        parts.append(col.astype(object).map(lambda v: "" if pd.isna(v) else str(v)))   # blank stays a value
    out = parts[0]
    for p in parts[1:]:
        out = out + "|" + p
    return out


def existing_exact_keys(df: pd.DataFrame, db_path=None, exclude_imports=()) -> set:
    """Rows already stored for the same league seasons. exclude_imports: files being replaced, whose rows must not
    cause the replacement's rows to be skipped."""
    pairs = df[["league", "season"]].drop_duplicates().dropna()
    if pairs.empty:
        return set()
    cond = " OR ".join("(league = ? AND season = ?)" for _ in range(len(pairs)))
    params = [v for row in pairs.values.tolist() for v in row]
    ex = ""
    if exclude_imports:
        ex = " AND import_id NOT IN (" + ",".join(str(int(i)) for i in exclude_imports) + ")"
    old = db.query(f"SELECT {', '.join(EXACT_KEY)} FROM player_seasons WHERE ({cond}){ex}", params, path=db_path)
    return set(exact_keys(old)) if not old.empty else set()


TABLE_FOR = {"player_season": "player_seasons", "team_season": "team_seasons", "transfer": "transfers_ref"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def commit(data: bytes, filename: str, sheet: str, raw: pd.DataFrame, res: BuildResult,
           maps: list[ColumnMap], opt: ImportOptions, issues: list[Issue], db_path=None,
           raw_dir: Path = RAW_DIR, exclude_imports=()) -> int:
    if any(i.level == "error" for i in issues):
        raise ValueError("Import has blocking errors. Fix the mapping or file and try again.")
    db.init_db(db_path)
    seen = existing_exact_keys(res.frame, db_path, exclude_imports) if opt.dataset_type == "player_season" else set()
    with db.connect(db_path) as con:
        iid = db.next_id(con, "import_seq")
        stored = Path(raw_dir) / f"{iid:05d}_{Path(filename).name}"
        stored.write_bytes(data)
        con.execute("INSERT INTO imports (import_id, filename, stored_path, sha256, dataset_type, provider, "
                    "file_league, file_season, n_rows, mapping_json, options_json, imported_at, notes, active) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,TRUE)", [
            iid, filename, str(stored), sha256(data), opt.dataset_type, opt.provider, opt.file_league,
            opt.file_season, len(res.frame),
            json.dumps([m.__dict__ for m in maps]),
            json.dumps({**opt.__dict__, "sheet": sheet}, default=str), datetime.now(), opt.notes])
        rec = pd.DataFrame({"import_id": iid, "row_num": np.arange(len(raw)),
                            "record_json": [json.dumps(r, default=str) for r in raw.to_dict("records")]})
        con.register("_raw", rec)
        con.execute("INSERT INTO raw_records SELECT * FROM _raw")
        con.unregister("_raw")
        frame = res.frame.copy()
        if opt.dataset_type == "player_season":
            keys = exact_keys(frame)
            skip = keys.isin(seen) | keys.duplicated()
            if skip.any():
                frame = frame[~skip]
                issues = issues + [Issue("info", "duplicates", f"Skipped {int(skip.sum())} rows identical to rows "
                                         "already stored. Originals are still kept in data/raw.", int(skip.sum()))]
            con.execute("UPDATE imports SET n_rows = ? WHERE import_id = ?", [len(frame), iid])
        frame["import_id"] = iid
        frame["row_id"] = iid * 10_000_000 + np.arange(len(frame))  # unique, stable
        # a new file for a league season that is not in Full use inherits that season's level, so it can never
        # reach the model before the merged season passes validation in the Data Library
        if opt.dataset_type == "player_season" and opt.file_league and opt.file_season:
            lv = con.execute("SELECT active, COALESCE(display_only, FALSE) FROM imports WHERE file_league = ? AND "
                             "file_season = ? AND import_id <> ? AND replaced_by IS NULL AND dataset_type = "
                             "'player_season'", [opt.file_league, str(opt.file_season), iid]).fetchall()
            if lv and not all(a for a, _ in lv):
                disp = any(d for _, d in lv)
                con.execute("UPDATE imports SET active = FALSE, display_only = ?, exclusion_reason = ? WHERE "
                            "import_id = ?", [disp, "Added to a season that is not in Full use; validate the merged "
                                              "season in the Data Library before Full use.", iid])
        db.insert_df(con, TABLE_FOR[opt.dataset_type], frame)
        if issues:
            log = pd.DataFrame([{"import_id": iid, "level": i.level, "check_name": i.check,
                                 "message": i.message, "n_affected": i.n} for i in issues])
            con.register("_log", log)
            con.execute("INSERT INTO validation_log SELECT * FROM _log")
            con.unregister("_log")
    try:
        from .rebuild import write_manifest
        write_manifest(db_path)
    except Exception:
        pass
    return iid


def import_file(path: str | Path, opt: ImportOptions, sheet: str | None = None,
                mapping: list[ColumnMap] | None = None, db_path=None, raw_dir: Path = RAW_DIR, exclude_imports=()):
    """Scriptable import (no UI). Returns (import_id, issues)."""
    path = Path(path)
    data = path.read_bytes()
    sheets = read_file(data, path.name)
    sheet = sheet or next(iter(sheets))
    raw = sheets[sheet]
    maps = mapping or auto_map(list(raw.columns), opt.dataset_type)
    res = BUILDERS[opt.dataset_type](raw, maps, opt)
    issues = validate(res, opt, sha256(data), db_path=db_path)
    iid = commit(data, path.name, sheet, raw, res, maps, opt, issues, db_path=db_path, raw_dir=raw_dir,
                 exclude_imports=exclude_imports)
    return iid, issues
