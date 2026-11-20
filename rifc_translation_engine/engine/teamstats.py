"""StatsBomb team season statistics: registry, multi file import, benchmarks.

Kept apart from the player translation model on purpose: nothing here feeds the
neutral model. The RIFC layer reads it for context, and the team context test
(engine/teamcontext.py) decides whether any of it earns a place in projections.

One import = one batch = the category files of ONE competition, season and
phase (regular season, playoffs, or combined). Season and phase are required
inputs: the files carry neither.
"""
from __future__ import annotations

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
from .normalize import norm_team

PROVIDER = "StatsBomb"
PHASES = {"regular": "Regular season", "playoffs": "Playoffs", "combined": "Regular season + playoffs (combined)"}
LEAGUE_AVG_NAMES = {"league average"}
META_COLS = {"Team Name", "Games", "Home Fixtures", "Away Fixtures"}

# ---------------------------------------------------------------------------
# Metric registry
#   kind: total     additive season total (normalised to per match)
#         pct       percentage (never summed)
#         share     0 to 1 proportion (never summed)
#         ratio     ratio of two totals (never summed)
#         average   average of a measured quantity (distance, length)
#         derived   provider specific value model total (OBV): additive within StatsBomb only
#   better: "high", "low" or None (style, not quality)
#   recompute: (numerator, denominator, scale) from totals in the same batch, for full precision
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TeamMetric:
    name: str
    category: str
    kind: str
    better: str | None
    definition: str
    recompute: tuple | None = None


C_RES, C_SHOT, C_SP, C_PASS, C_DEF, C_OBV = ("Results and xG", "Shooting", "Set pieces", "Passing",
                                             "Pressing and defending", "On-ball value")
_R = [
    # File: results and xG
    TeamMetric("Goal Difference", C_RES, "total", "high", "Goals scored minus goals conceded."),
    TeamMetric("xG Difference w/Pen", C_RES, "total", "high", "xG for minus xG against, penalties included."),
    TeamMetric("Non Penalty Goal Difference", C_RES, "total", "high", "Goal difference excluding penalty goals."),
    TeamMetric("Non Penalty xG Difference", C_RES, "total", "high", "Non penalty xG for minus non penalty xG against."),
    TeamMetric("Goals", C_RES, "total", "high", "Goals scored."),
    TeamMetric("Goals Conceded", C_RES, "total", "low", "Goals conceded."),
    TeamMetric("Non Penalty xG", C_RES, "total", "high", "Expected goals from all shots except penalties."),
    TeamMetric("Non Penalty xG Faced", C_RES, "total", "low", "Non penalty xG from opponents' shots."),
    TeamMetric("Non Penalty xG/Shot", C_RES, "ratio", "high", "Average non penalty xG per shot: a measure of chance quality.",
               ("Non Penalty xG", "Non Penalty Shots", 1)),
    TeamMetric("Non Penalty xG/Shot Faced", C_RES, "ratio", "low", "Average non penalty xG per shot conceded.",
               ("Non Penalty xG Faced", "Non Penalty Shots Faced", 1)),
    TeamMetric("Open Play xG", C_RES, "total", "high", "xG from shots in open play (not from set pieces or penalties)."),
    TeamMetric("Open Play xG Faced", C_RES, "total", "low", "Open play xG conceded."),
    TeamMetric("Own Goals", C_RES, "total", None, "Own goals scored in the team's favour."),
    TeamMetric("Own Goals Conceded", C_RES, "total", None, "Own goals the team scored against itself."),
    TeamMetric("Penalty Goals", C_RES, "total", "high", "Goals scored from penalties."),
    TeamMetric("Penalty Goals Conceded", C_RES, "total", "low", "Penalty goals conceded."),
    # File: shooting
    TeamMetric("Non Penalty Shots", C_SHOT, "total", "high", "Shots excluding penalties."),
    TeamMetric("Non Penalty Shots Faced", C_SHOT, "total", "low", "Opponents' shots excluding penalties."),
    TeamMetric("Shot Distance", C_SHOT, "average", "low", "Average distance from goal of the team's shots, in StatsBomb "
               "pitch units (the pitch is 120 long). Reported value; the per shot distances are not in the file."),
    TeamMetric("Shot Distance Faced", C_SHOT, "average", "high", "Average distance of opponents' shots."),
    TeamMetric("Clear Shots", C_SHOT, "total", "high", "Shots with only the goalkeeper between shooter and goal."),
    TeamMetric("Clear Shots Faced", C_SHOT, "total", "low", "Clear shots conceded."),
    TeamMetric("Counter Attack Shots", C_SHOT, "total", "high", "Shots StatsBomb tags as coming from counter attacks."),
    TeamMetric("Counter Attack Shots Faced", C_SHOT, "total", "low", "Counter attack shots conceded."),
    TeamMetric("High Press Shots", C_SHOT, "total", "high", "Shots StatsBomb tags as following a high regain (pressure "
               "high up the pitch). Shows chances after winning the ball high, not the whole pressing system."),
    TeamMetric("High Press Shots Conceded", C_SHOT, "total", "low", "High press shots conceded."),
    TeamMetric("Shots Outside Box", C_SHOT, "total", None, "Shots taken from outside the penalty area."),
    TeamMetric("Open Play Shots Outside Box Faced", C_SHOT, "total", None, "Open play shots from outside the box conceded."),
    TeamMetric("Penalties", C_SHOT, "total", "high", "Penalties taken."),
    TeamMetric("Penalties Faced", C_SHOT, "total", "low", "Penalties conceded. Exported by StatsBomb in the shooting file "
               "under the name 'Penalty Goals Conceded', but its league total equals penalties taken in every season "
               "imported, so it counts penalties faced, not goals."),
    TeamMetric("Open Play Shots", C_SHOT, "total", "high", "Shots in open play."),
    TeamMetric("Open Play Shots Faced", C_SHOT, "total", "low", "Open play shots conceded."),
    # File: set pieces
    TeamMetric("Set Piece Goals", C_SP, "total", "high", "Goals from set pieces."),
    TeamMetric("Set Piece Goals Conceded", C_SP, "total", "low", "Set piece goals conceded."),
    TeamMetric("Set Piece xG", C_SP, "total", "high", "xG from set piece shots."),
    TeamMetric("Set Piece xG Faced", C_SP, "total", "low", "Set piece xG conceded."),
    TeamMetric("Set Piece Shots", C_SP, "total", "high", "Shots from set pieces."),
    TeamMetric("Set Piece Shots Faced", C_SP, "total", "low", "Set piece shots conceded."),
    TeamMetric("Goals/Set Piece", C_SP, "ratio", "high", "Set piece goals per set piece taken.", ("Set Piece Goals", "SP", 1)),
    TeamMetric("Goals/Set Piece Conceded", C_SP, "ratio", "low", "Set piece goals conceded per opponent set piece.",
               ("Set Piece Goals Conceded", "SP Conceded", 1)),
    TeamMetric("xG/Set Piece", C_SP, "ratio", "high", "Set piece xG per set piece taken.", ("Set Piece xG", "SP", 1)),
    TeamMetric("xG/Set Piece Faced", C_SP, "ratio", "low", "Set piece xG conceded per opponent set piece.",
               ("Set Piece xG Faced", "SP Conceded", 1)),
    TeamMetric("Shots/Set Piece", C_SP, "ratio", "high", "Set piece shots per set piece taken.", ("Set Piece Shots", "SP", 1)),
    TeamMetric("Shots/Set Piece Faced", C_SP, "ratio", "low", "Set piece shots conceded per opponent set piece.",
               ("Set Piece Shots Faced", "SP Conceded", 1)),
    TeamMetric("SP", C_SP, "total", None, "Set pieces taken (corners, free kicks, throw ins as StatsBomb counts them)."),
    TeamMetric("SP Conceded", C_SP, "total", None, "Opponent set pieces."),
    # File: passing
    TeamMetric("Passing%", C_PASS, "pct", None, "Pass completion rate.", ("Successful Passes", "Passes", 100)),
    TeamMetric("Opposition Passing%", C_PASS, "pct", "low", "Opponents' pass completion rate.",
               ("Successful Passes Conceded", "Passes Conceded", 100)),
    TeamMetric("Passes Inside Box", C_PASS, "total", "high", "Completed passes into or inside the opposition penalty area."),
    TeamMetric("Passes Inside Box Conceded", C_PASS, "total", "low", "Opponents' completed passes inside the team's box."),
    TeamMetric("Deep Completions", C_PASS, "total", "high", "Completed non cross passes received within 20 metres of "
               "the opposition goal."),
    TeamMetric("Opposition Deep Completions", C_PASS, "total", "low", "Deep completions conceded."),
    TeamMetric("Box Cross%", C_PASS, "pct", None, "Share of the team's passes into the box that are crosses. Reported "
               "value; its denominator is not in the file."),
    TeamMetric("GK Long Ball%", C_PASS, "pct", None, "Share of goalkeeper passes that are long. Reported value."),
    TeamMetric("GK Pass Length", C_PASS, "average", None, "Average goalkeeper pass length, StatsBomb pitch units."),
    TeamMetric("Box Crosses", C_PASS, "total", None, "Crosses into the penalty area."),
    TeamMetric("Successful Box Crosses", C_PASS, "total", "high", "Completed crosses into the penalty area."),
    TeamMetric("Successful Box Cross%", C_PASS, "pct", "high", "Box cross completion. Shown as successful box crosses "
               "divided by box crosses over the season; the provider's reported figure differs (likely an average of "
               "match level percentages) and is kept as reported.", ("Successful Box Crosses", "Box Crosses", 100)),
    TeamMetric("Passes", C_PASS, "total", None, "Passes attempted: a proxy for possession volume."),
    TeamMetric("Passes Conceded", C_PASS, "total", None, "Opponents' passes attempted."),
    TeamMetric("Successful Passes", C_PASS, "total", None, "Completed passes."),
    TeamMetric("Successful Passes Conceded", C_PASS, "total", None, "Opponents' completed passes."),
    TeamMetric("Open Play Passes", C_PASS, "total", None, "Passes in open play."),
    TeamMetric("Open Play Passes Conceded", C_PASS, "total", None, "Opponents' open play passes."),
    # File: pressing and defending
    TeamMetric("PPDA", C_DEF, "ratio", None, "Passes allowed per defensive action in the opposition's part of the pitch. "
               "Lower means opponents get fewer passes before the team engages. Reported value; components not in the file."),
    TeamMetric("Defensive Distance", C_DEF, "average", None, "Average distance from the team's own goal of its "
               "defensive actions, StatsBomb pitch units. Higher means the team defends further up the pitch."),
    TeamMetric("Aggression", C_DEF, "share", None, "Share of opposition pass receipts that are pressured, tackled or "
               "fouled within two seconds. Reported value (0 to 1)."),
    TeamMetric("Aggressive Actions", C_DEF, "total", None, "Pressures, tackles and fouls within two seconds of an "
               "opposition receipt."),
    TeamMetric("Defensive Regains", C_DEF, "total", None, "Times the team's defensive action led to winning the ball."),
    # File: OBV (StatsBomb specific)
    TeamMetric("Pass OBV", C_OBV, "derived", "high", "StatsBomb On-Ball Value added by passes. Provider specific: no "
               "Wyscout equivalent."),
    TeamMetric("Opposition Pass OBV", C_OBV, "derived", "low", "Pass OBV of opponents."),
    TeamMetric("Dribble & Carry OBV", C_OBV, "derived", "high", "OBV added by dribbles and carries."),
    TeamMetric("Dribble & Carry OBV Conceded", C_OBV, "derived", "low", "Dribble and carry OBV of opponents."),
    TeamMetric("Defensive Action OBV", C_OBV, "derived", "high", "OBV added by defensive actions."),
    TeamMetric("Defensive Action OBV Conceded", C_OBV, "derived", "low", "Defensive action OBV of opponents."),
    TeamMetric("Shot OBV", C_OBV, "derived", "high", "OBV from shots (finishing relative to the chance)."),
    TeamMetric("Shot OBV Faced", C_OBV, "derived", "low", "Shot OBV of opponents."),
    TeamMetric("Goalkeeper OBV", C_OBV, "derived", "high", "OBV from goalkeeper actions."),
    TeamMetric("Goalkeeper OBV Conceded", C_OBV, "derived", "low", "Goalkeeper OBV of opponents."),
    TeamMetric("OBV", C_OBV, "derived", "high", "Total On-Ball Value."),
    TeamMetric("OBV Conceded", C_OBV, "derived", "low", "Total OBV of opponents."),
]
REGISTRY: dict[str, TeamMetric] = {m.name: m for m in _R}
ADDITIVE = {"total", "derived"}

# category of a file, detected from its headers
FILE_TYPES = {
    "results_xg": {"Goal Difference", "Non Penalty xG", "Open Play xG"},
    "shooting": {"Non Penalty Shots", "Clear Shots", "High Press Shots"},
    "set_pieces": {"Set Piece Goals", "Set Piece xG", "SP"},
    "passing": {"Passing%", "Deep Completions", "Box Crosses"},
    "pressing": {"PPDA", "Defensive Distance", "Aggression"},
    "obv": {"Pass OBV", "OBV", "Shot OBV"},
}
FILE_LABELS = {"results_xg": "Results and xG", "shooting": "Shooting", "set_pieces": "Set pieces",
               "passing": "Passing", "pressing": "Pressing and defending", "obv": "On-ball value"}


def detect_file_type(columns) -> str | None:
    cols = set(columns)
    hits = [k for k, need in FILE_TYPES.items() if need <= cols]
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS team_batch_seq START 1;
CREATE TABLE IF NOT EXISTS team_stat_batches (
    batch_id INTEGER PRIMARY KEY, provider VARCHAR, competition VARCHAR, season VARCHAR, phase VARCHAR,
    n_teams INTEGER, files_json VARCHAR, checks_json VARCHAR, imported_at TIMESTAMP, notes VARCHAR, active BOOLEAN
);
CREATE TABLE IF NOT EXISTS team_stats (
    batch_id INTEGER, provider VARCHAR, competition VARCHAR, season VARCHAR, phase VARCHAR,
    team VARCHAR, team_key VARCHAR, matched_team VARCHAR, matches DOUBLE,
    category VARCHAR, metric VARCHAR, kind VARCHAR, reported DOUBLE, value DOUBLE, value_source VARCHAR,
    per_match DOUBLE
);
CREATE TABLE IF NOT EXISTS team_stat_league_avg (
    batch_id INTEGER, metric VARCHAR, reported DOUBLE, mean_of_teams DOUBLE, median_of_teams DOUBLE,
    pooled_per_match_x_avg_games DOUBLE, note VARCHAR
);
CREATE TABLE IF NOT EXISTS team_stat_fixtures (
    batch_id INTEGER, team VARCHAR, home_opponents VARCHAR, away_opponents VARCHAR
);
"""


def init(path=None):
    with db.connect(path) as con:
        con.execute(SCHEMA)


@dataclass
class Check:
    level: str
    check: str
    message: str


@dataclass
class TeamBatch:
    files: dict                     # file type -> (filename, bytes, DataFrame)
    competition: str
    season: str
    phase: str
    checks: list = field(default_factory=list)
    merged: pd.DataFrame | None = None       # long format team rows
    league_avg: pd.DataFrame | None = None
    fixtures: pd.DataFrame | None = None


# Columns whose exported name doesn't match their content, per file type (verified on league totals)
RENAMES = {"shooting": {"Penalty Goals Conceded": "Penalties Faced"}}


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    text = data.decode("utf-8-sig", errors="replace")
    df = pd.read_csv(io.StringIO(text), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    return df


def _num(s: pd.Series) -> pd.Series:
    return 1.0 * pd.to_numeric(s.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False)
                         .str.strip(), errors="coerce")


def build_batch(uploads: list[tuple[str, bytes]], competition: str, season: str, phase: str,
                known_teams: dict | None = None) -> TeamBatch:
    """uploads: [(filename, bytes)]. known_teams: {team_key: name} of player data teams for this season (to map)."""
    checks: list[Check] = []
    files = {}
    for name, data in uploads:
        df = read_csv_bytes(data)
        ft = detect_file_type(df.columns)
        if ft is None:
            checks.append(Check("error", "file type", f"{name}: columns don't match any known StatsBomb team export."))
            continue
        if ft in RENAMES and set(RENAMES[ft]) & set(df.columns):
            df = df.rename(columns=RENAMES[ft])
            checks.append(Check("info", "column names", f"{FILE_LABELS[ft]} file: 'Penalty Goals Conceded' stored as "
                                "'Penalties Faced' (its league total equals penalties taken, so it counts penalties "
                                "conceded, not goals)."))
        if ft in files:
            checks.append(Check("error", "file type", f"{name} and {files[ft][0]} are both {FILE_LABELS[ft]} files. "
                                "Import one season at a time."))
            continue
        files[ft] = (name, data, df)
    if not season:
        checks.append(Check("error", "season", "Season is required. The files contain no season column."))
    if phase not in PHASES:
        checks.append(Check("error", "phase", "Choose regular season, playoffs or combined."))
    missing = [FILE_LABELS[k] for k in FILE_TYPES if k not in files]
    if missing:
        checks.append(Check("warning", "coverage", "Missing categories: " + ", ".join(missing) + ". Those metrics will be "
                            "blank for this season."))
    b = TeamBatch(files, competition, season, phase, checks)
    if not files:
        return b

    # team sets and match counts
    team_sets, games = {}, {}
    for ft, (name, _, df) in files.items():
        is_avg = df["Team Name"].str.strip().str.lower().isin(LEAGUE_AVG_NAMES)
        team_sets[ft] = set(df.loc[~is_avg, "Team Name"].str.strip())
        games[ft] = _num(df.loc[~is_avg].set_index(df.loc[~is_avg, "Team Name"].str.strip())["Games"])
    ref = next(iter(team_sets))
    for ft, ts in team_sets.items():
        if ts != team_sets[ref]:
            checks.append(Check("error", "team sets", f"{FILE_LABELS[ft]} has different clubs from {FILE_LABELS[ref]}: "
                                f"{sorted(ts ^ team_sets[ref])}. These files are probably from different seasons."))
    for ft, g in games.items():
        diff = g.reindex(games[ref].index) != games[ref]
        if diff.any():
            checks.append(Check("error", "match counts", f"Match counts disagree between {FILE_LABELS[ft]} and "
                                f"{FILE_LABELS[ref]} for {sorted(diff[diff].index)}."))
    g = games[ref]
    teams = sorted(team_sets[ref])
    checks.append(Check("info", "clubs", f"{len(teams)} clubs; League Average rows kept separately for verification."))
    if g.nunique() > 1:
        msg = (f"Matches per club range from {int(g.min())} to {int(g.max())} (most common {int(g.mode().iloc[0])}). "
               "In a regular season every club plays the same number of matches, so either playoff matches are "
               "included or some matches are missing from coverage.")
        checks.append(Check("warning" if phase == "regular" else "info", "competition phase",
                            msg + (" You selected Regular season: check this before importing." if phase == "regular"
                                   else "")))
    # duplicate columns across files must agree
    seen = {}
    for ft, (name, _, df) in files.items():
        d = df.set_index(df["Team Name"].str.strip())
        for c in d.columns:
            if c in META_COLS or c not in REGISTRY:
                continue
            v = _num(d[c])
            if c in seen:
                other = seen[c][1].reindex(v.index)
                if not np.allclose(v.fillna(-9e9), other.fillna(-9e9)):
                    checks.append(Check("warning", "definitions", f"'{c}' appears in two files with different values."))
            else:
                seen[c] = (ft, v)
    unknown = sorted({c for _, _, df in files.values() for c in df.columns if c not in META_COLS and c not in REGISTRY})
    if unknown:
        checks.append(Check("warning", "registry", "Columns not in the metric registry are stored as reported, kind "
                            f"unknown, never summed: {unknown}."))

    # team name mapping to player data
    mapped = {}
    if known_teams:
        for t in teams:
            k = norm_team(t)
            mapped[t] = known_teams.get(k)
        un = [t for t, v in mapped.items() if v is None]
        if un:
            checks.append(Check("warning", "team names", f"No club with this name in the {season} player data: {un}. "
                                "They are stored but can't be linked to player rows."))
        else:
            checks.append(Check("info", "team names", f"All {len(teams)} clubs match a club in the {season} player data."))

    # long format with full precision values
    rows, avg_rows, fx = [], [], []
    vals = {c: v for c, (ft, v) in seen.items()}
    emitted = set()
    for ft, (name, _, df) in files.items():
        d = df.set_index(df["Team Name"].str.strip())
        for c in d.columns:
            if c in META_COLS or c in emitted:   # a column repeated in two files is stored once
                continue
            emitted.add(c)
            meta = REGISTRY.get(c)
            kind = meta.kind if meta else "unknown"
            rep = _num(d[c]).astype(float)
            value, source = rep.copy(), pd.Series("reported", index=rep.index, dtype=object)
            if meta and meta.recompute and meta.recompute[0] in vals and meta.recompute[1] in vals:
                num, den, scale = meta.recompute
                rc = vals[num] / vals[den].where(vals[den] > 0) * scale
                ok = rc.notna()
                value[ok] = rc[ok]
                source[ok] = f"recomputed: {num} / {den}"
            for t in teams:
                m = float(g[t])
                v = float(value.get(t, np.nan))
                rows.append({"team": t, "team_key": norm_team(t), "matched_team": mapped.get(t), "matches": m,
                             "category": FILE_LABELS[ft], "metric": c, "kind": kind,
                             "reported": float(rep.get(t, np.nan)), "value": v, "value_source": source.get(t),
                             "per_match": v / m if kind in ADDITIVE and m > 0 else np.nan})
            # League Average verification (never used as a team observation)
            avg = [i for i in d.index if i.lower() in LEAGUE_AVG_NAMES]
            if avg and kind in ADDITIVE | {"pct", "ratio", "average", "share"}:
                r = float(rep.get(avg[0], np.nan))
                tv = value.reindex(teams)
                avg_games = float(_num(d.loc[[avg[0]], "Games"]).iloc[0])
                pooled = (tv.sum() / g.sum() * avg_games) if kind in ADDITIVE and avg_games > 0 else np.nan
                note = ""
                if avg_games == 0:
                    note = "League Average row in this file is empty (0 games); not used."
                elif kind in ADDITIVE:
                    note = ("matches mean of clubs" if abs(r - tv.mean()) <= max(0.51, 0.01 * abs(r))
                            else "matches median of clubs" if abs(r - tv.median()) <= max(0.51, 0.01 * abs(r))
                            else "does not match mean or median of clubs")
                avg_rows.append({"metric": c, "reported": r, "mean_of_teams": float(tv.mean()),
                                 "median_of_teams": float(tv.median()), "pooled_per_match_x_avg_games": pooled,
                                 "note": note})
        if "Home Fixtures" in d.columns and not fx:
            for t in teams:
                fx.append({"team": t, "home_opponents": d.loc[t, "Home Fixtures"], "away_opponents": d.loc[t, "Away Fixtures"]})
    b.merged = pd.DataFrame(rows)
    b.league_avg = pd.DataFrame(avg_rows)
    b.fixtures = pd.DataFrame(fx)
    if not b.league_avg.empty:
        add = b.league_avg[b.league_avg["note"].str.startswith("does not") | b.league_avg["note"].str.startswith("matches m")]
        n_bad = int(b.league_avg["note"].str.startswith("does not").sum())
        if n_bad:
            checks.append(Check("info", "league average", f"The provider's League Average row differs from the mean of "
                                f"the clubs on {n_bad} additive metrics. Benchmarks here use the clubs' own values; the "
                                "row is stored for reference only."))
        if (b.league_avg["note"].str.contains("empty")).any():
            checks.append(Check("info", "league average", "One file's League Average row is empty (0 games)."))
    rc = b.merged[b.merged["value_source"].str.startswith("recomputed", na=False)]
    if not rc.empty:
        checks.append(Check("info", "precision", f"{rc['metric'].nunique()} rate metrics recomputed at full precision "
                            "from totals in the files (e.g. Passing% = Successful Passes / Passes). Reported values kept."))
    return b


def existing_batch(competition, season, phase, path=None):
    init(path)
    return db.query("SELECT * FROM team_stat_batches WHERE provider = ? AND competition = ? AND season = ? AND "
                    "phase = ? AND active", [PROVIDER, competition, season, phase], path=path)


def commit_batch(b: TeamBatch, notes: str = "", path=None, raw_dir: Path = RAW_DIR) -> int:
    if any(c.level == "error" for c in b.checks):
        raise ValueError("Batch has blocking errors.")
    if not existing_batch(b.competition, b.season, b.phase, path).empty:
        raise ValueError(f"{b.competition} {b.season} ({PHASES[b.phase]}) is already imported. Exclude the old "
                         "import first if you want to replace it.")
    init(path)
    with db.connect(path) as con:
        con.execute(SCHEMA)
        bid = int(con.execute("SELECT nextval('team_batch_seq')").fetchone()[0])
        folder = Path(raw_dir) / "statsbomb" / f"batch_{bid:03d}_{b.season}_{b.phase}"
        folder.mkdir(parents=True, exist_ok=True)
        files_meta = []
        for ft, (name, data, _) in b.files.items():
            (folder / Path(name).name).write_bytes(data)
            files_meta.append({"file": Path(name).name, "type": ft, "sha256": hashlib.sha256(data).hexdigest(),
                               "stored": str(Path("statsbomb") / folder.name / Path(name).name)})
        con.execute("INSERT INTO team_stat_batches VALUES (?,?,?,?,?,?,?,?,?,?,TRUE)", [
            bid, PROVIDER, b.competition, b.season, b.phase, int(b.merged["team"].nunique()), json.dumps(files_meta),
            json.dumps([c.__dict__ for c in b.checks]), datetime.now(), notes])
        for tbl, frame in (("team_stats", b.merged.assign(batch_id=bid, provider=PROVIDER, competition=b.competition,
                                                            season=b.season, phase=b.phase)),
                           ("team_stat_league_avg", b.league_avg.assign(batch_id=bid)),
                           ("team_stat_fixtures", b.fixtures.assign(batch_id=bid))):
            if frame is None or frame.empty:
                continue
            cols = [r[0] for r in con.execute(f"DESCRIBE {tbl}").fetchall()]
            con.register("_f", frame.reindex(columns=cols))
            con.execute(f"INSERT INTO {tbl} SELECT * FROM _f")
            con.unregister("_f")
    _write_team_manifest(path)
    return bid


def _write_team_manifest(path=None):
    from .config import DATA_DIR
    bt = db.query("SELECT batch_id, provider, competition, season, phase, files_json, notes, active FROM "
                  "team_stat_batches ORDER BY batch_id", path=path)
    (DATA_DIR / "team_import_manifest.json").write_text(json.dumps(bt.to_dict("records"), default=str, indent=1))


def set_batch_active(batch_id: int, active: bool, path=None):
    with db.connect(path) as con:
        con.execute(SCHEMA)
        con.execute("UPDATE team_stat_batches SET active = ? WHERE batch_id = ?", [active, int(batch_id)])
    _write_team_manifest(path)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
def load(path=None, competition="USL Championship") -> pd.DataFrame:
    init(path)
    return db.query("SELECT s.* FROM team_stats s JOIN team_stat_batches b USING (batch_id) WHERE b.active AND "
                    "s.competition = ?", [competition], path=path)


def batches(path=None) -> pd.DataFrame:
    init(path)
    return db.query("SELECT * FROM team_stat_batches ORDER BY season, phase", path=path)


def benchmark_table(stats: pd.DataFrame, season: str, phase: str) -> pd.DataFrame:
    """One row per club and metric for one verified season and phase: the comparable value (per match for
    additive totals, the value itself otherwise), league mean of clubs, difference, rank, percentile."""
    s = stats[(stats["season"] == season) & (stats["phase"] == phase)].copy()
    if s.empty:
        return s
    s["comparable"] = np.where(s["kind"].isin(list(ADDITIVE)), s["per_match"], s["value"])
    out = []
    for m, g in s.groupby("metric"):
        meta = REGISTRY.get(m)
        x = g["comparable"]
        mean, sd = x.mean(), x.std(ddof=0)
        asc = meta.better == "low" if meta and meta.better else False
        rank = x.rank(ascending=asc, method="min")
        pct = x.rank(pct=True, method="average") * 100
        if meta and meta.better == "low":
            pct = 100 - pct + 100 / len(x)
        out.append(g.assign(league_mean=mean, diff=x - mean, diff_pct=(x / mean - 1) * 100 if mean else np.nan,
                            z=(x - mean) / sd if sd else 0.0, rank=rank, n_teams=len(x),
                            percentile=pct.clip(0, 100), higher_is=(meta.better if meta else None)))
    return pd.concat(out, ignore_index=True)


def team_profile(stats: pd.DataFrame, team_key: str, season: str, phase: str, metrics: list[str]) -> pd.DataFrame:
    bt = benchmark_table(stats, season, phase)
    if bt.empty:
        return bt
    r = bt[(bt["team_key"] == team_key) & (bt["metric"].isin(metrics))].copy()
    r["order"] = r["metric"].map({m: i for i, m in enumerate(metrics)})
    return r.sort_values("order")


PROFILES = {
    "Attacking environment": ["Goals", "Non Penalty xG", "Open Play xG", "Non Penalty Shots", "Clear Shots",
                              "Non Penalty xG/Shot", "Shot Distance", "Passes Inside Box", "Deep Completions",
                              "Box Crosses", "Successful Box Cross%"],
    "Chance creation methods": ["Open Play xG", "Set Piece xG", "Counter Attack Shots", "High Press Shots",
                                "Open Play Shots", "Set Piece Shots", "Passes Inside Box"],
    "Defensive environment": ["Goals Conceded", "Non Penalty xG Faced", "Non Penalty Shots Faced", "Defensive Regains",
                              "Aggressive Actions", "PPDA", "Defensive Distance", "Aggression"],
    "On-ball value": ["Pass OBV", "Dribble & Carry OBV", "Defensive Action OBV", "Shot OBV", "OBV"],
    "Possession": ["Passes", "Passing%", "Open Play Passes", "Opposition Passing%", "GK Long Ball%"],
}


def import_folder_batch(paths: list[Path], competition: str, season: str, phase: str, notes: str = "", path=None):
    """Scriptable import of one season's files."""
    known = {}
    ps = db.load_table("player_seasons", path, active_only=True)
    if not ps.empty:
        t = ps[(ps["league"] == competition) & (ps["season"] == season)]["team"].dropna().unique()
        known = {norm_team(x): x for x in t}
    b = build_batch([(Path(p).name, Path(p).read_bytes()) for p in paths], competition, season, phase, known)
    return b, (commit_batch(b, notes, path) if not any(c.level == "error" for c in b.checks) else None)
