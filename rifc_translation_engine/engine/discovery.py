"""Recruitment discovery: neutral Championship projections for every eligible player at once.

Same model, same inputs and same readiness and confidence rules as a single
player projection (engine.api.Engine.project); only vectorised. A test checks
that the two agree. Nothing here changes a projection.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .api import Engine, PlayerInput
from .config import MODEL_DIR
from .model import variant_label
from .schema import METRICS, USLC

REF_MINUTES = 2200          # ranges in discovery are quoted for this many Championship minutes (profile default)
YEARS_AHEAD, START_SHARE = 1.0, 0.85
CONF_RANK = {"High": 3, "Medium": 2, "Low": 1, "None": 0}
GROUP_ROLE = {"ST": "Center forward", "W": "Winger", "AM": "Attacking midfielder",
              "CM": "Central or defensive midfielder", "DM": "Central or defensive midfielder",
              "FB": "Fullback", "CB": "Center back"}


def _stub(position, league, per90_keys):
    return PlayerInput(name="", position_group=position, league=league, season="", minutes=1,
                       per90={k: 1.0 for k in per90_keys})


def _latest_season(agg, league):
    a = agg[agg["league"] == league]
    return None if a.empty else a.sort_values("season_start")["season"].iloc[-1]


def benchmarks(eng: Engine, groups: list[str], season: str | None = None) -> dict:
    """Championship distribution per metric for one role (latest season, benchmark minutes)."""
    season = season or _latest_season(eng.agg, USLC)
    a = eng.agg[(eng.agg["league"] == USLC) & (eng.agg["season"] == season) & eng.agg["position_group"].isin(groups)
                & (eng.agg["minutes"] >= eng.settings.benchmark_min_minutes)]
    return {m: np.sort(a[f"{m}_p90"].dropna().to_numpy()) for m in METRICS}


def pctl(dist: np.ndarray, v):
    """Mid rank percentile, same rule as Engine.percentile (None under 10 benchmark players)."""
    v = np.asarray(v, float)
    if dist is None or len(dist) < 10:
        return np.full(v.shape, np.nan)
    lo = np.searchsorted(dist, v, side="left")
    hi = np.searchsorted(dist, v, side="right")
    out = (lo + 0.5 * (hi - lo)) / len(dist) * 100
    return np.where(np.isnan(v), np.nan, out)


def project_all(eng: Engine, minutes: float = REF_MINUTES, records: pd.DataFrame | None = None) -> pd.DataFrame:
    """Wide table: one row per player league season record (outfield), columns <metric>_now, _proj, _lo80, _hi80,
    _status, _conf, _pctl, _key. Neutral team context, as Engine.project with team_idx=None.
    records: the identity layer's season records (one player ID each); default the model's own table."""
    src = eng.agg if records is None else records
    a = src[src["position_group"].notna() & (src["position_group"] != "GK")].copy().reset_index(drop=True)
    a["key"] = a["player_name"] + "|" + a["team"].astype(str) + "|" + a["league"] + "|" + a["season"]
    ids = [c for c in ("uid", "rec_key", "pid", "display_only") if c in a]
    out = a[ids + ["key", "player_name", "team", "current_team", "contract_expires", "league", "season",
                   "season_start", "position_group", "position_raw", "minutes", "matches", "age_at_season",
                   "nationality", "provider"]].copy()
    out["role"] = out["position_group"].map(GROUP_ROLE)
    out["is_rifc"] = out["team"].fillna("").str.lower().str.contains("rhode island") & (out["league"] == USLC)
    n = len(a)
    base = pd.DataFrame({
        "src_minutes": a["minutes"].astype(float), "dst_minutes": float(minutes), "position_group": a["position_group"],
        "src_league": a["league"], "dst_league": USLC,
        "age_dst": a["age_at_season"].astype(float) + YEARS_AHEAD,
        "src_team_idx": a["team_attack_idx"].astype(float).fillna(0.0) if "team_attack_idx" in a else 0.0,
        "dst_team_idx": 0.0, "dst_start_share": START_SHARE})
    combos = a[["position_group", "league"]].drop_duplicates().values.tolist()
    # benchmarks per role group
    bench = {r: benchmarks(eng, g) for r, g in {
        "Center forward": ["ST"], "Winger": ["W"], "Attacking midfielder": ["AM"],
        "Central or defensive midfielder": ["CM", "DM"], "Fullback": ["FB"], "Center back": ["CB"]}.items()}
    new = {}
    role_arr = out["role"].to_numpy()
    for m, meta in METRICS.items():
        now = a[f"{m}_p90"].astype(float)
        new[f"{m}_now"] = now.to_numpy()
        cols = {c: np.full(n, np.nan) for c in ("proj", "lo80", "hi80")}
        status = np.full(n, "not available", dtype=object)
        conf = np.full(n, "None", dtype=object)
        keys = np.full(n, None, dtype=object)
        bases = [k for k in eng.models if k.partition("+")[0] == m]
        if bases:
            sup_key = eng.model_key(m, _stub("ST", USLC, list(METRICS)))
            sup = sup_key.partition("+")[2].split(",") if "+" in sup_key else []
            has_sup = np.ones(n, bool)
            for x in sup:
                has_sup &= a[f"{x}_p90"].notna().to_numpy()
            plain = m if m in eng.models else None
            row_key = np.where(has_sup & bool(sup), sup_key, plain)
            # readiness and confidence per (position, league, key)
            rc = {}
            for pos, lg in combos:
                for k in {sup_key, plain} - {None}:
                    stub = _stub(pos, lg, [m] + (k.partition("+")[2].split(",") if "+" in k else []))
                    rd = eng.readiness(m, stub, USLC)
                    c = eng.confidence(k, pos, lg, USLC, rd.status)
                    rc[(pos, lg, k)] = (rd.status, c["confidence"])
            for k in {sup_key, plain} - {None}:
                mm = eng.models[k]
                idx = np.where((row_key == k) & now.notna().to_numpy())[0]
                if not len(idx):
                    continue
                st = np.array([rc[(p, l, k)][0] for p, l in zip(a.loc[idx, "position_group"], a.loc[idx, "league"])],
                              dtype=object)
                cf = np.array([rc[(p, l, k)][1] for p, l in zip(a.loc[idx, "position_group"], a.loc[idx, "league"])],
                              dtype=object)
                status[idx], conf[idx], keys[idx] = st, cf, k
                ok = idx[st != "insufficient"]
                if not len(ok):
                    continue
                df = base.loc[ok].copy()
                df[f"src_{m}"] = now.loc[ok]
                for x in mm.design.support:
                    df[f"src_{x}"] = a.loc[ok, f"{x}_p90"].astype(float)
                pr = mm.predict(df, dst_minutes=float(minutes), levels=(0.8,))
                cols["proj"][ok], cols["lo80"][ok], cols["hi80"][ok] = pr["pred"], pr["lo80"], pr["hi80"]
        new[f"{m}_proj"], new[f"{m}_lo80"], new[f"{m}_hi80"] = cols["proj"], cols["lo80"], cols["hi80"]
        new[f"{m}_status"], new[f"{m}_conf"], new[f"{m}_key"] = status, conf, keys
        p = np.full(n, np.nan)
        pn = np.full(n, np.nan)
        for role, b in bench.items():
            mask = role_arr == role
            p[mask] = pctl(b.get(m), cols["proj"][mask])
            pn[mask] = pctl(b.get(m), new[f"{m}_now"][mask])
        new[f"{m}_pctl"] = p
        new[f"{m}_now_pctl_uslc"] = pn
    return pd.concat([out, pd.DataFrame(new, index=out.index)], axis=1)


def cache_path(eng: Engine, minutes: float, tag: str = "") -> str:
    return str(MODEL_DIR / f"discovery_run{eng.run_id}_bt{eng.backtest_id}_{int(minutes)}{tag}.pkl")


def load_or_build(eng: Engine, minutes: float = REF_MINUTES, records: pd.DataFrame | None = None,
                  tag: str = "") -> pd.DataFrame:
    from pathlib import Path
    p = Path(cache_path(eng, minutes, tag))
    if p.exists():
        try:
            d = pickle.loads(p.read_bytes())
            if d.get("fingerprint") == eng.fingerprint:
                return d["table"]
        except Exception:
            pass
    for old in MODEL_DIR.glob("discovery_run*.pkl"):
        old.unlink(missing_ok=True)
    t = project_all(eng, minutes, records)
    try:
        p.write_bytes(pickle.dumps({"fingerprint": eng.fingerprint, "table": t}))
    except Exception:
        pass
    return t


# ---------------------------------------------------------------------------
# Positional presets (only shown when both metrics have models)
# ---------------------------------------------------------------------------
PRESETS = {
    "Center forward": [("xg", "goals"), ("goals", "assists"), ("xg", "succ_dribbles"), ("shots", "xg"),
                       ("xg", "aerials_won")],
    "Winger": [("xg", "xa"), ("succ_dribbles", "assists"), ("goals", "prog_carries"), ("key_passes", "succ_dribbles")],
    "Attacking midfielder": [("xa", "prog_carries"), ("key_passes", "prog_passes"), ("xg", "xa")],
    "Central or defensive midfielder": [("prog_passes", "succ_def_actions"), ("xa", "prog_carries"),
                                        ("prog_passes", "interceptions")],
    "Fullback": [("prog_passes", "succ_def_actions"), ("accurate_crosses", "prog_carries"),
                 ("def_duels_won", "prog_passes")],
    "Center back": [("aerials_won", "prog_passes"), ("succ_def_actions", "prog_passes"),
                    ("def_duels_won", "interceptions")],
}


def presets(eng: Engine, role: str) -> list[tuple[str, str]]:
    have = {k.partition("+")[0] for k in eng.models}
    return [(x, y) for x, y in PRESETS.get(role, []) if x in have and y in have]


# ---------------------------------------------------------------------------
# Target finder: Meets requirements, Near matches, Exploratory targets
# ---------------------------------------------------------------------------
REFS = {
    "rifc_best": "Best current RIFC player",
    "rifc_median": "RIFC positional median",
    "rifc_mean": "RIFC positional mean",
    "uslc_median": "Championship positional median",
    "uslc_p75": "Championship 75th percentile",
    "uslc_pctl": "Championship percentile (choose)",
    "value": "Custom per 90 value",
}


@dataclass
class Criterion:
    metric: str
    ref: str = "uslc_median"        # one of REFS
    value: float = 50.0              # percentile for uslc_pctl, per 90 for value
    mandatory: bool = True


@dataclass
class Bar:
    value: float | None
    text: str


def resolve(c: Criterion, bench: dict, rifc: pd.DataFrame, role: str, season: str | None) -> Bar:
    """The actual number a criterion compares against, with where it comes from."""
    m = c.metric
    kind = METRICS[m].kind
    f = (lambda v: f"{v:.1f}%") if kind == "pct" else (lambda v: f"{v:.2f}")
    if c.ref.startswith("uslc"):
        d = bench.get(m)
        if d is None or len(d) < 10:
            return Bar(None, "fewer than 10 Championship players to benchmark")
        q = {"uslc_median": 50, "uslc_p75": 75}.get(c.ref, c.value)
        v = float(np.percentile(d, q))
        return Bar(v, f"{f(v)}: Championship {role.lower()}s, {int(q)}th percentile ({season}, 900+ min, n={len(d)})")
    if c.ref.startswith("rifc"):
        r = rifc[rifc[f"{m}_p90"].notna()] if not rifc.empty else rifc
        if r.empty:
            return Bar(None, "no RIFC players in this role with enough minutes")
        vals = r[f"{m}_p90"]
        if c.ref == "rifc_best":
            i = vals.idxmax()
            return Bar(float(vals[i]), f"{f(vals[i])}: {r.loc[i, 'player_name']}, observed in {int(r.loc[i, 'minutes']):,} "
                                         f"min ({r.loc[i, 'season']})")
        v = float(vals.median() if c.ref == "rifc_median" else vals.mean())
        who = ", ".join(f"{x.player_name} {f(getattr(x, f'{m}_p90'))}" for x in r.itertuples())
        return Bar(v, f"{f(v)}: RIFC {'median' if c.ref == 'rifc_median' else 'mean'} of {len(r)} observed ({who})")
    return Bar(float(c.value), f"{f(c.value)}: custom value")


def describe(c: Criterion, bar: Bar) -> str:
    lab = METRICS[c.metric].label
    ref = {"uslc_pctl": f"the Championship {int(c.value)}th percentile", "value": "a custom value"}.get(
        c.ref, "the " + REFS[c.ref].replace("RIFC", "RIFC").replace("Best current", "best current"))
    return f"{lab} at least {ref}, {bar.text}" + ("" if c.mandatory else " (preference)")


def classify(table: pd.DataFrame, criteria: list[Criterion], bars: list[Bar], tolerance: float = 0.15,
             max_short: int = 1, conservative: bool = False, allow_low: bool = False) -> pd.DataFrame:
    """Puts every player in one group with the exact reason. Mandatory criteria are never weakened:
    a Near match is labelled with each requirement he misses and by how much.

    Meets requirements: every mandatory criterion cleared, every mandatory metric High or Medium confidence
                        (or Low too when allow_low).
    Near matches:       misses at most max_short mandatory criteria, each by no more than tolerance (relative),
                        same confidence rule.
    Exploratory:        would be a Meet or Near match on the numbers, but a mandatory metric is Low confidence,
                        not validated or missing.
    Others are not shown."""
    t = table.copy()
    n = len(t)
    ok_conf = {"High", "Medium"} | ({"Low"} if allow_low else set())
    groups, reasons, shorts, prefs, weak = [], [], [], [], []
    cols = {}
    for c, b in zip(criteria, bars):
        m = c.metric
        cols[m] = (t[f"{m}_proj"].to_numpy(float), t[f"{m}_lo80"].to_numpy(float), t[f"{m}_hi80"].to_numpy(float),
                   t[f"{m}_conf"].to_numpy(object))
    for i in range(n):
        why, short, pref, low, missing = [], [], [], [], []
        n_short = 0
        bad = False
        for c, b in zip(criteria, bars):
            proj, lo, hi, conf = (x[i] for x in cols[c.metric])
            lab = METRICS[c.metric].label
            kind = METRICS[c.metric].kind
            f = (lambda v: f"{v:.1f}%") if kind == "pct" else (lambda v: f"{v:.2f}")
            if b.value is None:
                bad = True if c.mandatory else bad
                continue
            if np.isnan(proj):
                (missing if c.mandatory else pref).append(f"{lab}: no projection")
                continue
            val = lo if conservative else proj
            inside = lo <= b.value <= hi
            if val >= b.value:
                note = (f"{lab} {f(proj)} projected vs bar {f(b.value)}"
                        + (" (bar inside his 80% range, so not a clear margin)" if inside and not conservative else ""))
                (why if c.mandatory else pref).append(note)
            else:
                gap = (b.value - val) / abs(b.value) if b.value else np.inf
                note = (f"{lab} {f(val)} {'low end of range' if conservative else 'projected'} vs bar {f(b.value)}, "
                        f"{gap:.0%} short" + (" (bar inside his 80% range)" if inside else ""))
                if c.mandatory:
                    short.append((gap, note))
                    n_short += 1
            if c.mandatory and conf not in ok_conf:
                low.append(f"{lab}: {conf} confidence")
        near_ok = n_short <= max_short and all(g <= tolerance for g, _ in short)
        if bad:
            grp = None
        elif not missing and not low and n_short == 0:
            grp = "Meets requirements"
        elif not missing and not low and n_short and near_ok:
            grp = "Near matches"
        elif (n_short == 0 or near_ok) and (low or len(missing) == 1):
            grp = "Exploratory targets"
        else:
            grp = None
        groups.append(grp)
        reasons.append(why)
        shorts.append([s for _, s in short])
        prefs.append(pref)
        weak.append(low + missing)
    t["group"] = groups
    t["why"] = reasons
    t["short"] = shorts
    t["prefs"] = prefs
    t["weak"] = weak
    t["n_prefs"] = [sum(1 for x in p if "vs bar" in x and "short" not in x) for p in prefs]
    mand = [c for c in criteria if c.mandatory]
    t["worst_conf"] = [min((CONF_RANK.get(t[f"{c.metric}_conf"].iloc[i], 0) for c in mand), default=0) for i in range(n)]
    t["reliability"] = t["worst_conf"].map({3: "High", 2: "Medium", 1: "Low", 0: "None"})
    return t


def exclusion_causes(t: pd.DataFrame, criteria: list[Criterion], bars: list[Bar], conservative=False,
                     allow_low=False) -> list[str]:
    """Why players miss the strict requirements: per mandatory criterion, how many fail it, and how many fail
    only that one."""
    ok_conf = {"High", "Medium"} | ({"Low"} if allow_low else set())
    mand = [(c, b) for c, b in zip(criteria, bars) if c.mandatory and b.value is not None]
    if t.empty or not mand:
        return []
    fails = {}
    for c, b in mand:
        v = t[f"{c.metric}_lo80" if conservative else f"{c.metric}_proj"]
        fails[c.metric] = (v.isna() | (v < b.value)).to_numpy()
    allf = np.vstack(list(fails.values()))
    only = (allf.sum(0) == 1)
    out = []
    for (c, b), (m, f) in zip(mand, fails.items()):
        lab = METRICS[m].label
        best = t[f"{m}_proj"].max()
        out.append(f"{lab}: {int(f.sum())} of {len(t)} players fall short of {b.text.split(':')[0]}; "
                   f"{int((f & only).sum())} miss only this one. Highest projection in the pool: "
                   f"{best:.2f}." if pd.notna(best) else f"{lab}: no projections in the pool.")
        lowc = (~t[f"{m}_conf"].isin(ok_conf)).sum()
        if lowc:
            out[-1] += f" {int(lowc)} have below Medium confidence on it."
    return out


PRIORITY_PRESETS = {
    "Center forward": {
        "Chance getter": [Criterion("xg", "uslc_pctl", 60), Criterion("np_goals", "uslc_median", mandatory=False)],
        "More xG than the RIFC median, carries the ball": [Criterion("xg", "rifc_median"),
                                                           Criterion("succ_dribbles", "uslc_median")],
        "More xG than our best current option": [Criterion("xg", "rifc_best")],
        "Aerial target": [Criterion("aerials_won", "uslc_p75"), Criterion("xg", "uslc_pctl", 40)],
        "Ball carrying forward": [Criterion("succ_dribbles", "uslc_p75"), Criterion("xg", "uslc_pctl", 40)],
        "Penalty box scorer": [Criterion("xg", "uslc_pctl", 60), Criterion("touches_att_box", "uslc_median"),
                               Criterion("shots", "uslc_median", mandatory=False)],
    },
    "Winger": {
        "Creator": [Criterion("xa", "uslc_pctl", 60), Criterion("key_passes", "uslc_median")],
        "Ball carrier": [Criterion("prog_carries", "uslc_p75"), Criterion("succ_dribbles", "uslc_pctl", 60)],
        "Box presence from wide": [Criterion("touches_att_box", "uslc_pctl", 60), Criterion("xa", "uslc_median")],
        "Goal threat from wide": [Criterion("xg", "uslc_pctl", 60), Criterion("succ_dribbles", "uslc_median")],
    },
    "Attacking midfielder": {
        "Chance creator": [Criterion("xa", "uslc_pctl", 60), Criterion("key_passes", "uslc_pctl", 60)],
        "Progressor": [Criterion("prog_passes", "uslc_pctl", 60), Criterion("prog_carries", "uslc_median")],
    },
    "Central or defensive midfielder": {
        "Progressive passer": [Criterion("prog_passes", "uslc_p75"), Criterion("pass_pct", "uslc_median")],
        "Ball winner": [Criterion("succ_def_actions", "uslc_p75"), Criterion("interceptions", "uslc_median")],
        "Above our options on progression": [Criterion("prog_passes", "rifc_mean"),
                                             Criterion("interceptions", "uslc_pctl", 40)],
    },
    "Fullback": {
        "Attacking fullback": [Criterion("accurate_crosses", "uslc_pctl", 60), Criterion("prog_carries", "uslc_median")],
        "Defensive fullback": [Criterion("def_duels_won", "uslc_pctl", 60), Criterion("interceptions", "uslc_median")],
        "Progressive fullback": [Criterion("prog_passes", "uslc_pctl", 60), Criterion("prog_carries", "uslc_median")],
    },
    "Center back": {
        "Aerial defender": [Criterion("aerials_won", "uslc_p75"), Criterion("def_duels_won", "uslc_median")],
        "Secure in possession": [Criterion("pass_pct", "uslc_pctl", 60), Criterion("interceptions", "uslc_median")],
        "Ball playing center back": [Criterion("prog_passes", "uslc_p75"), Criterion("pass_pct", "uslc_median")],
    },
}


def rifc_reference(eng: Engine, groups: list[str], min_minutes: int = 450) -> pd.DataFrame:
    a = eng.agg[(eng.agg["league"] == USLC) & eng.agg["team"].fillna("").str.lower().str.contains("rhode island")]
    if a.empty:
        return a
    season = a.sort_values("season_start")["season"].iloc[-1]
    return a[(a["season"] == season) & a["position_group"].isin(groups) & (a["minutes"] >= min_minutes)]
