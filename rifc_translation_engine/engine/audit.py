"""Validation audit. Reads the fitted engine and backtest; changes nothing in the model.

Sections
  leakage_audit      programmatic checks that no test move or future season reaches training
  breakdown          backtest accuracy by transition, position and metric, every cell with n and a trust flag
  identity_audit     possible duplicate players and same name collisions
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db
from .backtest import _score, backtest_metric
from .config import ModelSettings
from .model import fit_metric, variant_label
from .normalize import short_name, strip_accents
from .schema import METRICS
from .transfers import metric_pairs

ADEQUATE, LIMITED = 50, 20
LEAGUE_SHORT = {"USL Championship": "USLC", "USL League One": "USL1", "MLS Next Pro": "MLSNP"}
TRANSITIONS = [("MLS Next Pro", "USL Championship"), ("USL League One", "USL Championship"),
               ("USL Championship", "MLS Next Pro"), ("USL Championship", "USL League One"),
               ("MLS Next Pro", "USL League One"), ("USL League One", "MLS Next Pro")]


def trust(n: int) -> str:
    if n >= ADEQUATE:
        return "Adequate"
    if n >= LIMITED:
        return "Limited"
    return "Too few to trust"


def transition_name(src, dst):
    return f"{LEAGUE_SHORT.get(src, src)} to {LEAGUE_SHORT.get(dst, dst)}"


def _split(pairs, Y):
    train = pairs[pairs["dst_season_end"] < Y + 0.2]
    test = pairs[(np.floor(pairs["dst_season_start"]) == Y) & (pairs["pair_type"] == "cross")]
    return train, test


def _key_pairs(eng, key):
    base, _, sup = key.partition("+")
    p = metric_pairs(eng.pairs, base, eng.settings)
    for x in (sup.split(",") if sup else []):
        p = p[p[f"src_{x}"].notna()]
    return base, (sup.split(",") if sup else None), p


def selected_keys(eng) -> dict:
    """The model variant a Wyscout player (who has xG and xA) is projected with, per base metric."""
    out = {}
    probe = {m: 1.0 for m in METRICS}

    class _P:  # minimal stand in for PlayerInput
        per90 = probe
    for m in METRICS:
        if any(k.partition("+")[0] == m for k in eng.models):
            out[m] = eng.model_key(m, _P)
    return out


# ---------------------------------------------------------------------------
# 1. Leakage
# ---------------------------------------------------------------------------
def leakage_audit(eng, keys=None, disjoint_keys=("goals+xg", "xg", "goal_contributions", "shots",
                                                  "prog_passes", "interceptions")):
    """Returns (checks table, player disjoint comparison, future perturbation result)."""
    keys = keys or list(eng.backtest_preds)
    checks = []
    design_cols = {c for mm in eng.models.values() for c in mm.design.columns}
    player_terms = [c for c in design_cols if any(t in c.lower() for t in ("pid", "player", "name"))]
    checks.append({"check": "No player specific parameters in the model", "scope": "all models",
                   "result": "pass" if not player_terms else "FAIL", "detail":
                   "Design columns hold only league, position, age, minutes, team context and the player's "
                   "source numbers; a player cannot be memorised." if not player_terms else str(player_terms)})
    for key in keys:
        base, sup, p = _key_pairs(eng, key)
        years = sorted(eng.backtest_preds[key]["test_year"].unique())
        for Y in years:
            train, test = _split(p, Y)
            scored = eng.backtest_preds[key]
            scored = scored[scored["test_year"] == Y]
            tk = set(map(tuple, test[["pid", "src_league", "src_season", "dst_league", "dst_season"]].values.tolist()))
            rk = set(map(tuple, train[["pid", "src_league", "src_season", "dst_league", "dst_season"]].values.tolist()))
            out_rows = set(map(tuple, test[["pid", "dst_league", "dst_season"]].values.tolist()))
            src_rows = set(map(tuple, train[["pid", "src_league", "src_season"]].values.tolist()))
            dst_rows = set(map(tuple, train[["pid", "dst_league", "dst_season"]].values.tolist()))
            time_ok = train["dst_season_end"].max() < test["dst_season_start"].min()
            src_ok = (train["src_season_start"] < test["dst_season_start"].min()).all()
            checks += [
                {"check": "Training outcomes end before test seasons start", "scope": f"{key} {Y}",
                 "result": "pass" if time_ok else "FAIL",
                 "detail": f"latest training outcome season ends {train['dst_season_end'].max():.2f}, "
                           f"first test season starts {test['dst_season_start'].min():.2f}"},
                {"check": "No training source season is in or after the test season", "scope": f"{key} {Y}",
                 "result": "pass" if src_ok else "FAIL", "detail": ""},
                {"check": "No test move appears in training", "scope": f"{key} {Y}",
                 "result": "pass" if not (tk & rk) else "FAIL", "detail": f"{len(tk & rk)} shared moves"},
                {"check": "No test outcome season is used in training (as source or outcome)", "scope": f"{key} {Y}",
                 "result": "pass" if not (out_rows & (src_rows | dst_rows)) else "FAIL",
                 "detail": f"{len(out_rows & (src_rows | dst_rows))} overlaps"},
                {"check": "Test players with earlier history in training (allowed, see note)", "scope": f"{key} {Y}",
                 "result": "info", "detail": f"{scored['pid'].isin(train['pid']).sum()} of {len(scored)} scored "
                                             "moves; earlier seasons were known at prediction time"},
            ]
    checks = pd.DataFrame(checks)

    # Player disjoint rerun: drop every pair of every test player from training
    comp = []
    for key in [k for k in disjoint_keys if k in eng.backtest_preds]:
        base, sup, p = _key_pairs(eng, key)
        res_std, res_dis = [], []
        for Y in sorted(eng.backtest_preds[key]["test_year"].unique()):
            train, test = _split(p, Y)
            scored = eng.backtest_preds[key]
            scored = scored[scored["test_year"] == Y]
            hist = eng.agg[eng.agg["season_end"] < Y + 0.2]
            sref = {x: hist[f"{x}_p90"] for x in sup} if sup else None
            tr2 = train[~train["pid"].isin(scored["pid"])]
            mm = fit_metric(tr2, base, eng.settings, hist[f"{base}_p90"], support_refs=sref)
            t = test.merge(scored[["pid", "src_league", "src_season", "dst_season", "dst_league"]],
                           on=["pid", "src_league", "src_season", "dst_season", "dst_league"])
            if mm is None or t.empty:
                continue
            pr = mm.predict(t, levels=(0.8,))
            res_dis.append(pd.DataFrame({"actual": t[f"dst_{base}"].to_numpy(), "pred": pr["pred"].to_numpy(),
                                         "lo": pr["lo80"].to_numpy(), "hi": pr["hi80"].to_numpy()}))
            res_std.append(scored[["actual", "model", "lo80", "hi80"]].rename(
                columns={"model": "pred", "lo80": "lo", "hi80": "hi"}))
        if not res_dis:
            continue
        a, b = pd.concat(res_std), pd.concat(res_dis)
        comp.append({"metric": variant_label(key), "n": len(a), "n disjoint": len(b),
                     "MAE standard": (a.pred - a.actual).abs().mean(),
                     "MAE player disjoint": (b.pred - b.actual).abs().mean(),
                     "80% coverage standard": ((a.actual >= a.lo) & (a.actual <= a.hi)).mean(),
                     "80% coverage player disjoint": ((b.actual >= b.lo) & (b.actual <= b.hi)).mean()})
    comp = pd.DataFrame(comp)
    if not comp.empty:
        comp["change"] = (comp["MAE player disjoint"] / comp["MAE standard"] - 1) * 100

    # Future perturbation: scramble everything from the test season on; earlier predictions must not move
    pert = []
    for key in [k for k in ("goals+xg", "prog_passes") if k in eng.backtest_preds]:
        base, sup, p = _key_pairs(eng, key)
        Y = min(eng.backtest_preds[key]["test_year"])
        rng = np.random.default_rng(1)
        p2 = p.copy()
        fut = p2["dst_season_start"] >= Y + 0.2
        for c in [c for c in p2.columns if c.startswith("dst_") and p2[c].dtype.kind == "f"
                  and c not in ("dst_season_start", "dst_season_end", "dst_minutes")]:
            p2.loc[fut, c] = rng.permutation(p2.loc[fut, c].to_numpy()) * rng.uniform(0.5, 2, fut.sum())
        agg2 = eng.agg.copy()
        futa = agg2["season_end"] >= Y + 0.2
        agg2.loc[futa, f"{base}_p90"] = agg2.loc[futa, f"{base}_p90"] * 3 + 1
        a = backtest_metric(p, eng.agg, base, eng.settings, sup)
        b = backtest_metric(p2, agg2, base, eng.settings, sup)
        a, b = a[a["test_year"] == Y], b[b["test_year"] == Y]
        diff = float(np.max(np.abs(a["model"].to_numpy() - b["model"].to_numpy())))
        pert.append({"metric": variant_label(key), "test year": Y, "moves": len(a),
                     "future rows scrambled": int(fut.sum() + futa.sum()),
                     "max change in predictions": diff, "result": "pass" if diff < 1e-9 else "FAIL"})
    return checks, comp, pd.DataFrame(pert)


def duplicate_moves(eng) -> pd.DataFrame:
    """Same move recorded under two identities (would double count in the backtest)."""
    c = eng.pairs[eng.pairs["pair_type"] == "cross"]
    key = ["src_league", "src_season", "dst_league", "dst_season", "src_minutes", "dst_minutes",
           "src_goals", "dst_goals"]
    d = c[c.duplicated(key, keep=False)]
    return d[["pid", "player_name", "src_league", "src_season", "dst_league", "dst_season", "src_minutes",
              "dst_minutes"]].sort_values(key[:4])


# ---------------------------------------------------------------------------
# 2 to 4. Accuracy breakdowns
# ---------------------------------------------------------------------------
def _verdict(r, settings: ModelSettings):
    if r["n"] < LIMITED:
        return "Unknown (too few moves)"
    beats_carry = r["MAE model"] <= r["MAE carry over"]
    near_best = r["MAE model"] <= r["MAE best simple"] * (1 + getattr(settings, "baseline_tolerance", 0.05))
    cov_ok = abs(r["80% coverage"] - 0.8) <= settings.coverage_tolerance
    if beats_carry and near_best and cov_ok:
        return "Reliable" if r["n"] >= ADEQUATE else "Promising, small sample"
    why = []
    if not beats_carry:
        why.append("worse than carry over")
    elif not near_best:
        why.append("simple baseline better")
    if not cov_ok:
        why.append("ranges too wide" if r["80% coverage"] > 0.8 else "ranges too narrow")
    return "Not reliable: " + ", ".join(why)


def _cell(df):
    s = _score(df)
    best = min(s["mae_pair_ratio"], s["mae_dest_mean"])
    return {"n": s["n_test"], "MAE model": s["mae_model"], "MAE carry over": s["mae_persistence"],
            "MAE best simple": best, "Model vs carry over %": (s["mae_model"] / s["mae_persistence"] - 1) * 100
            if s["mae_persistence"] else np.nan,
            "Model vs best simple %": (s["mae_model"] / best - 1) * 100 if best else np.nan,
            "80% coverage": s["coverage80"], "P(model beats carry over)": s.get("p_beats_carry_over", np.nan)}


def breakdown(eng, by: list[str]) -> pd.DataFrame:
    """One row per (selected metric variant x grouping) with n, errors, coverage, trust flag, verdict."""
    rows = []
    for base, key in selected_keys(eng).items():
        P = eng.backtest_preds.get(key)
        if P is None:
            continue
        P = P.assign(transition=[transition_name(a, b) for a, b in zip(P["src_league"], P["dst_league"])])
        groups = [((), P)] if not by else P.groupby(by, observed=True)
        for g, d in groups:
            g = g if isinstance(g, tuple) else (g,)
            r = {"metric": METRICS[base].label, "group": METRICS[base].group, "model reads": variant_label(key)
                 .partition(", using ")[2] or "own record", **dict(zip(by, g)), **_cell(d)}
            r["trust"] = trust(r["n"])
            r["verdict"] = _verdict(r, eng.settings)
            rows.append(r)
    out = pd.DataFrame(rows)
    order = list(METRICS)
    lab_order = {METRICS[m].label: i for i, m in enumerate(order)}
    return out.sort_values(["metric"] + by, key=lambda s: s.map(lab_order) if s.name == "metric" else s) \
        .reset_index(drop=True)


def transition_coverage(eng) -> pd.DataFrame:
    """Every requested transition, including ones with no test moves at all."""
    c = eng.pairs[eng.pairs["pair_type"] == "cross"]
    P = eng.backtest_preds.get(selected_keys(eng).get("xg", "xg"))
    rows = []
    for a, b in TRANSITIONS:
        tot = c[(c["src_league"] == a) & (c["dst_league"] == b)]
        sc = P[(P["src_league"] == a) & (P["dst_league"] == b)] if P is not None else pd.DataFrame()
        rows.append({"transition": transition_name(a, b), "pairs in data": len(tot),
                     "by outcome season": ", ".join(f"{k}: {v}" for k, v in tot["dst_season"].value_counts()
                                                    .sort_index().items()) or "none",
                     "backtest moves (per metric)": len(sc), "trust": trust(len(sc))})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. Identity
# ---------------------------------------------------------------------------
def identity_audit(eng, db_path=None) -> pd.DataFrame:
    ps = db.load_table("player_seasons", db_path, active_only=True)
    ps = ps[ps["name_norm"].notna()]
    rows = []
    # a) duplicate profiles collapsed by the engine
    if eng.review is not None and not eng.review.empty:
        dp = eng.review[eng.review["issue"].str.startswith("Exact", na=False)]
        for r in dp.itertuples():
            rows.append({"type": "Duplicate profile collapsed", "severity": "handled", "player": r.player_name,
                         "league": r.league, "season": r.season, "team": r.team,
                         "detail": f"Same season stats as another profile (age {r.age}, {r.nationality}); "
                                   "this copy ignored."})
    # b) one identity with heavy minutes in two leagues in one season
    a = eng.agg
    heavy = a[a["minutes"] >= 1500].groupby(["pid", "season"])["league"].nunique()
    for (pid, season) in heavy[heavy > 1].index:
        g = a[(a["pid"] == pid) & (a["season"] == season)]
        rows.append({"type": "Heavy minutes in two leagues, same season", "severity": "check",
                     "player": g["player_name"].iloc[0], "league": ", ".join(g["league"]), "season": season,
                     "team": ", ".join(g["team"].astype(str)),
                     "detail": "; ".join(f"{r.league}: {int(r.minutes)} min, age {r.age_at_season:.0f}, "
                                         f"{r.nationality}" for r in g.itertuples())})
    # c) same name, different identities, overlapping seasons (collisions the engine kept apart)
    for nm, g in a.groupby("player_name"):
        if g["pid"].nunique() < 2:
            continue
        rows.append({"type": "Same name, kept as different players", "severity": "info", "player": nm,
                     "league": ", ".join(sorted(g["league"].unique())), "season": ", ".join(sorted(g["season"].unique())),
                     "team": ", ".join(sorted(g["team"].astype(str).unique()))[:80],
                     "detail": f"{g['pid'].nunique()} identities; birth years "
                               f"{sorted(set(int(x) for x in g['birth_year'].dropna()))}; countries "
                               f"{sorted(set(str(x) for x in g['nationality'].dropna()))}"})
    # c2) one identity built from Wyscout profiles with different ages. All files were exported the same
    #     day and Wyscout shows current age, so one real profile always carries one age; two ages means
    #     two profiles were linked (a merge to check), and any pair crossing them may be false.
    from .transfers import drop_duplicate_profiles, resolve_identities
    rp, _ = drop_duplicate_profiles(ps)
    rp, _ = resolve_identities(rp, db.load_table("transfers_ref", db_path, active_only=True))
    cross = eng.pairs[eng.pairs["pair_type"] == "cross"] if not eng.pairs.empty else eng.pairs
    for pid, g in rp[rp["age"].notna()].groupby("pid"):
        if g["age"].nunique() < 2:
            continue
        ages = g.groupby("age").agg(seasons=("season", lambda x: ", ".join(sorted(set(x)))),
                                    leagues=("league", lambda x: ", ".join(sorted(set(x)))),
                                    teams=("team", lambda x: ", ".join(sorted(set(map(str, x))))))
        n_cross = int((cross["pid"] == pid).sum()) if not cross.empty else 0
        # Stronger evidence than an age that is off by one (Wyscout ages are not fully consistent):
        # repeated rows for the same club (overlapping exports) count once
        gs = g.sort_values("minutes", ascending=False).drop_duplicates(["season", "team"])
        season_min = gs.groupby("season")["minutes"].sum()
        n_clubs = gs.groupby("season")["team"].nunique()
        impossible = season_min[(season_min > 3300) & (n_clubs > 1)]   # one club with playoffs can pass 3,300
        hts = g["height_cm"].dropna()
        height_gap = (hts.max() - hts.min()) if len(hts) > 1 else 0
        evidence = []
        if not impossible.empty:
            evidence.append(f"{int(impossible.iloc[0])} minutes in {impossible.index[0]} across two clubs, "
                            "more than one player can play")
        if height_gap > 3:
            evidence.append(f"heights differ by {height_gap:.0f} cm")
        if g["age"].max() - g["age"].min() > 1:
            evidence.append("ages differ by more than one year")
        likely_two = bool(evidence)
        rows.append({"type": "Likely two players merged" if likely_two else
                     "Same player, Wyscout age off by one (probably fine)",
                     "severity": ("check" if n_cross else "low impact") if likely_two else "info",
                     "player": g["player_name"].iloc[0],
                     "league": ", ".join(sorted(g["league"].unique())), "season": ", ".join(sorted(g["season"].unique())),
                     "team": ", ".join(sorted(g["team"].astype(str).unique()))[:80],
                     "detail": " | ".join(f"age {int(a)}: {r.seasons} {r.teams}" for a, r in ages.iterrows())
                               + (f" | evidence: {'; '.join(evidence)}" if evidence else "")
                               + f" | cross league moves built on this identity: {n_cross}",
                     "pid": pid, "cross_moves": n_cross})
    # d) accent or spelling variants on the same team and season
    ps["plain"] = ps["player_name"].map(lambda s: strip_accents(str(s)).lower().replace(" ", ""))
    for (team, season, plain), g in ps.groupby(["team", "season", "plain"]):
        if g["player_name"].nunique() > 1:
            same_stats = g["minutes"].nunique() == 1
            rows.append({"type": "Spelling variant on same team",
                         "severity": "handled" if same_stats else "info",
                         "player": " / ".join(sorted(g["player_name"].unique())), "league": g["league"].iloc[0],
                         "season": season, "team": team,
                         "detail": ("Identical minutes: duplicate profile, one copy ignored. " if same_stats else
                                    "Different minutes and ages: treated as two players. ") +
                                   "; ".join(f"{r.player_name}: age {r.age}, {r.nationality}, {int(r.minutes)} min"
                                             for r in g.itertuples())})
    # e) short name ('J. Smith') matching several full names: links refused as ambiguous
    full = a[~a["player_name"].str.match(r"^[A-Z]\.\s")]
    shorts = full.groupby(full["player_name"].map(lambda s: short_name(strip_accents(str(s)).lower())))["pid"].nunique()
    for sn in shorts[shorts > 1].index:
        g = full[full["player_name"].map(lambda s: short_name(strip_accents(str(s)).lower())) == sn]
        rows.append({"type": "Short name matches several full names", "severity": "info", "player": sn,
                     "league": ", ".join(sorted(g["league"].unique())), "season": "", "team": "",
                     "detail": ", ".join(sorted(g["player_name"].unique()))[:120]})
    out = pd.DataFrame(rows)
    sev = {"check": 0, "low impact": 1, "handled": 2, "info": 3}
    return out.sort_values(["severity", "type", "player"], key=lambda s: s.map(sev) if s.name == "severity" else s) \
        .reset_index(drop=True)


def player_detail(eng, name: str) -> pd.DataFrame:
    a = eng.agg[eng.agg["player_name"].str.contains(name, case=False, regex=False)]
    return a[["pid", "player_name", "league", "season", "team", "position_group", "minutes", "age_at_season",
              "birth_year", "nationality"]].sort_values(["pid", "season"])
