"""Phase 6 and 12: does StatsBomb team context improve projections out of sample?

Rules fixed before the test was run (6 Nov 2026):

Feature, per metric family, from the destination club's StatsBomb profile in the
season BEFORE the destination season (what a recruiter knows when signing):
  attacking output   -> log(club non penalty xG per match / league mean)
  possession volume  -> log(club passes per match / league mean)
  defensive volume   -> log(opponent passes per match / league mean)
For a move between two USLC clubs the feature is destination minus source (both
clubs' source season profiles are known). For a move into USLC from MLS NEXT Pro
or USL League One the source club has no StatsBomb data: the source is treated as
league average, and that limitation is reported.

Test design: neutral predictions are the existing model's own out of time
predictions (refit on moves completed before the test season). The adjustment is
fitted on the residuals of 2025 destination moves and scored on 2026 destination
moves only. 2024 destination moves can't be used: no 2023 team data.

Adopted for a metric only if, on 2026 moves: error falls by at least 2%, at least
90% of 1,000 bootstrap resamples favour it, at least 30 test moves, and the effect
keeps the same sign when refitted on all moves. Otherwise the RIFC projection stays
the neutral projection and team context is shown as information only.

The neutral model is not changed by anything in this file.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import teamstats as T
from .model import fit_metric, variant_label
from .schema import METRICS, USLC
from .transfers import metric_pairs

FAMILY = {
    "attack": ("Non Penalty xG", "club non penalty xG per match"),
    "possession": ("Passes", "club passes per match"),
    "defence": ("Passes Conceded", "opponent passes per match"),
}
METRIC_FAMILY = {
    **{m: "attack" for m in ["goals", "np_goals", "xg", "npxg", "assists", "xa", "goal_contributions", "shots",
                              "key_passes", "touches_att_box", "passes_to_box", "sca"]},
    **{m: "possession" for m in ["prog_passes", "prog_carries", "final_third_entries", "pass_pct", "succ_dribbles",
                                  "crosses", "accurate_crosses"]},
    **{m: "defence" for m in ["interceptions", "def_duels_won", "succ_def_actions", "aerials_won", "aerial_win_pct",
                               "def_duel_win_pct", "tackles", "recoveries", "ground_duels_won"]},
}
MIN_GAIN, MIN_P, MIN_N, RIDGE_N0 = 0.02, 0.90, 30, 20.0
PHASE = "combined"


def club_index(stats: pd.DataFrame) -> pd.DataFrame:
    """Per club and season: log ratio to the league mean of the three family metrics."""
    rows = []
    for (season, phase), g in stats.groupby(["season", "phase"]):
        for fam, (metric, _) in FAMILY.items():
            x = g[g["metric"] == metric].set_index("team_key")["per_match"].dropna()
            if x.empty:
                continue
            idx = np.log(x / x.mean())
            for k, v in idx.items():
                rows.append({"season": season, "phase": phase, "team_key": k, "family": fam, "idx": float(v)})
    return pd.DataFrame(rows)


def _features(pairs: pd.DataFrame, cidx: pd.DataFrame, family: str) -> pd.Series:
    """Feature per pair (NaN when the data needed is missing). Source season = the team data season."""
    from .normalize import norm_team
    c = cidx[(cidx["family"] == family) & (cidx["phase"] == PHASE)].set_index(["season", "team_key"])["idx"]
    dk = pairs["dst_team"].map(norm_team)
    sk = pairs["src_team"].map(norm_team)
    dst = pd.Series([c.get((s, k), np.nan) for s, k in zip(pairs["src_season"], dk)], index=pairs.index)
    src = pd.Series([c.get((s, k), np.nan) for s, k in zip(pairs["src_season"], sk)], index=pairs.index)
    within = pairs["src_league"] == pairs["dst_league"]
    return pd.Series(np.where(within, dst - src, dst), index=pairs.index)


def _eligible(pairs: pd.DataFrame) -> pd.Series:
    """Moves into USLC from another league, or between two different USLC clubs."""
    into = (pairs["dst_league"] == USLC) & (pairs["src_league"] != USLC)
    club_change = (pairs["src_league"] == USLC) & (pairs["dst_league"] == USLC) & (pairs["src_team"] != pairs["dst_team"])
    return into | club_change


def oot_neutral(eng, key: str, years) -> pd.DataFrame:
    """The existing model's out of time predictions for eligible moves into each year in `years`."""
    base, _, sup = key.partition("+")
    sup = sup.split(",") if sup else None
    p = metric_pairs(eng.pairs, base, eng.settings)
    for x in sup or []:
        p = p[p[f"src_{x}"].notna()]
    out = []
    for Y in years:
        train = p[p["dst_season_end"] < Y + 0.2]
        test = p[(np.floor(p["dst_season_start"]) == Y) & _eligible(p)]
        if test.empty or (train["pair_type"] == "cross").sum() < eng.settings.min_cross_league_pairs:
            continue
        hist = eng.agg[eng.agg["season_end"] < Y + 0.2]
        mm = fit_metric(train, base, eng.settings, hist[f"{base}_p90"],
                        support_refs={x: hist[f"{x}_p90"] for x in sup} if sup else None)
        if mm is None:
            continue
        ok = test.apply(lambda r: r["src_league"] == r["dst_league"] or (
            mm.cross_by_league.get(r["src_league"], 0) >= eng.settings.min_league_pairs and mm.connected(
                r["src_league"], r["dst_league"])), axis=1)
        test = test[ok]
        if test.empty:
            continue
        pr = mm.predict(test, levels=(0.8,))
        means = hist[hist["minutes"] >= eng.settings.benchmark_min_minutes].groupby(
            ["league", "position_group"])[f"{base}_p90"].mean()
        out.append(test.assign(year=Y, actual=test[f"dst_{base}"].to_numpy(), source=test[f"src_{base}"].to_numpy(),
                               t_mu=pr["t_mu"].to_numpy(), t_sd=pr["t_sd"].to_numpy(), neutral=pr["pred"].to_numpy(),
                               lo80=pr["lo80"].to_numpy(), hi80=pr["hi80"].to_numpy(),
                               positional=[means.get((USLC, g), np.nan) for g in test["position_group"]],
                               transform=[mm.transform] * len(test)))
    return pd.concat(out) if out else pd.DataFrame()


def _w(df):
    a, b = df["src_minutes"].to_numpy(float), df["dst_minutes"].to_numpy(float)
    return np.clip(2 * a * b / (a + b) / 1800.0, 0.25, 1.5)


def _fit_b(df) -> float:
    tr = df["transform"].iloc[0]
    r = tr.fwd(df["actual"].to_numpy(float)) - df["t_mu"].to_numpy(float)
    x = df["feature"].to_numpy(float)
    w = _w(df)
    lam = RIDGE_N0 * np.mean(w) * np.var(x) if np.var(x) > 0 else 1.0
    return float(np.sum(w * x * r) / (np.sum(w * x * x) + lam))


def evaluate(eng, path=None) -> tuple[pd.DataFrame, dict]:
    """Returns (results per metric, details). Uses the variant each projection uses (xG/xA where available)."""
    from .audit import selected_keys
    stats = T.load(path)
    if stats.empty:
        return pd.DataFrame(), {"reason": "No StatsBomb team data imported."}
    cidx = club_index(stats)
    seasons = sorted(stats["season"].unique())
    # destination seasons whose previous season has team data
    years = sorted(int(s) + 1 for s in seasons)
    rows, details = [], {"years": years, "team_seasons": seasons}
    rng = np.random.default_rng(0)
    for base, key in selected_keys(eng).items():
        fam = METRIC_FAMILY.get(base)
        if fam is None:
            continue
        P = oot_neutral(eng, key, years)
        if P.empty:
            continue
        P["feature"] = _features(P, cidx, fam)
        P = P[P["feature"].notna()]
        ys = sorted(P["year"].unique())
        if len(ys) < 2:
            rows.append({"metric": base, "label": METRICS[base].label, "decision": "Not testable",
                         "why": "Team data covers only one destination season, so there is nothing to train on "
                                "before the test season."})
            continue
        train, test = P[P["year"].isin(ys[:-1])], P[P["year"] == ys[-1]]
        details["train_years"], details["test_year"] = [int(y) for y in ys[:-1]], int(ys[-1])
        r = {"metric": base, "label": METRICS[base].label, "model reads": variant_label(key).partition(", using ")[2]
             or "own record", "team feature": FAMILY[fam][1], "train moves": len(train), "test moves": len(test)}
        if len(train) < 10 or test.empty:
            r.update({"decision": "Not testable", "why": "Too few moves with team data."})
            rows.append(r)
            continue
        b = _fit_b(train)
        tr = test["transform"].iloc[0]
        adj = tr.inv(test["t_mu"].to_numpy(float) + b * test["feature"].to_numpy(float))
        a = test["actual"].to_numpy(float)
        e_n, e_a = np.abs(test["neutral"].to_numpy(float) - a), np.abs(adj - a)
        pos = test["positional"].to_numpy(float)
        e_p = np.abs(pos - a)
        idx = rng.integers(0, len(test), (1000, len(test)))
        p_better = float((e_a[idx].mean(1) < e_n[idx].mean(1)).mean())
        b_all = _fit_b(P)
        gain = 1 - e_a.mean() / e_n.mean()
        adopt = (gain >= MIN_GAIN and p_better >= MIN_P and len(test) >= MIN_N and np.sign(b_all) == np.sign(b))
        why = []
        if len(test) < MIN_N:
            why.append(f"only {len(test)} test moves")
        if gain < MIN_GAIN:
            why.append(f"error change {(-gain) * 100:+.1f}% (needs -2% or better)")
        if p_better < MIN_P:
            why.append(f"wins {p_better:.0%} of resamples (needs 90%)")
        if np.sign(b_all) != np.sign(b):
            why.append("effect changes direction when refitted on all moves")
        by_t = test.assign(t=np.where(test["src_league"] == USLC, "USLC club to club",
                                      test["src_league"].map({"MLS Next Pro": "MLSNP", "USL League One": "USL1"}) + " to USLC"))
        r.update({"MAE neutral": e_n.mean(), "MAE team adjusted": e_a.mean(), "MAE positional baseline": np.nanmean(e_p),
                  "error change %": -gain * 100, "P(adjusted better)": p_better, "effect (train)": b,
                  "effect (all moves)": b_all, "decision": "Adopt" if adopt else "Not adopted",
                  "why": "passes every rule" if adopt else "; ".join(why),
                  "test moves by transition": ", ".join(f"{k}: {v}" for k, v in by_t["t"].value_counts().items())})
        rows.append(r)
    res = pd.DataFrame(rows)
    details["adopted"] = res.loc[res.get("decision", pd.Series(dtype=str)) == "Adopt", "metric"].tolist() if not res.empty else []
    return res, details


def club_feature_now(path, team_key: str, season: str) -> dict:
    """Family indices for one club and season (for display)."""
    stats = T.load(path)
    c = club_index(stats)
    c = c[(c["team_key"] == team_key) & (c["season"] == season) & (c["phase"] == PHASE)]
    return dict(zip(c["family"], c["idx"]))


# ---------------------------------------------------------------------------
# Saved result, used by projections
# ---------------------------------------------------------------------------
def _store():
    from .config import MODEL_DIR
    return MODEL_DIR / "team_context.json"


def run_and_save(eng, path=None) -> dict:
    import json
    from datetime import datetime
    from . import db
    res, det = evaluate(eng, path)
    effects = {}
    if not res.empty and "decision" in res:
        for r in res[res["decision"] == "Adopt"].itertuples():
            effects[r.metric] = {"family": METRIC_FAMILY[r.metric], "effect": float(getattr(r, "_14", np.nan))}
        # effect refitted on all moves with team data (column name has spaces)
        for m in effects:
            effects[m]["effect"] = float(res.set_index("metric").loc[m, "effect (all moves)"])
    out = {"created": datetime.now().isoformat(timespec="seconds"), "model_run": eng.run_id,
           "fingerprint": eng.fingerprint, "team_fingerprint": team_fingerprint(path), "details": det,
           "n_tested": int(len(res)), "effects": effects,
           "results": res.drop(columns=[c for c in res.columns if c == "transform"], errors="ignore")
           .replace({np.nan: None}).to_dict("records")}
    _store().write_text(json.dumps(out, default=str, indent=1))
    return out


def team_fingerprint(path=None) -> str:
    b = T.batches(path)
    return "|".join(f"{r.batch_id}:{r.season}:{r.phase}:{r.active}" for r in b.itertuples())


def load_saved() -> dict | None:
    import json
    f = _store()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except Exception:
        return None


def rifc_adjustment(metric: str, saved: dict | None, stats: pd.DataFrame, dst_key: str, src_key: str | None,
                    src_league: str, season: str):
    """Shift on the model scale for an adopted metric, or None. Returns (shift, explanation)."""
    if not saved or metric not in saved.get("effects", {}):
        return None, "Team context not validated for this metric: neutral projection shown."
    fam = saved["effects"][metric]["family"]
    b = saved["effects"][metric]["effect"]
    c = club_index(stats)
    c = c[(c["family"] == fam) & (c["phase"] == PHASE) & (c["season"] == season)].set_index("team_key")["idx"]
    if dst_key not in c:
        return None, f"No {season} StatsBomb data for the target club."
    f = c[dst_key]
    note = f"{FAMILY[fam][1]} {np.exp(f) - 1:+.0%} vs league ({season})"
    if src_league == USLC and src_key in c:
        f = f - c[src_key]
        note += f", minus the current club's {np.exp(c[src_key]) - 1:+.0%}"
    elif src_league != USLC:
        note += "; source club has no StatsBomb data, treated as league average"
    return b * f, note
