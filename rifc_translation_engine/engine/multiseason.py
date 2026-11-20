"""Does a player's earlier season help? An experiment, not a model change.

The production model reads one source season: the player's league season just before the move.
This module tests two multi season inputs with the SAME model code and the SAME time based
backtest (train only on moves that ended before the test season):

  two_season   : the source season blended with his previous season in the same league
                 (same provider), weighted by minutes
  recency      : the same, with the earlier season's minutes counted at half weight

Only seasons before the source season are used, so nothing from the destination season or
later reaches the input. Players without a qualifying earlier season keep their one season
input, so the two runs differ only where history exists.

It also checks the existing backtest's errors against history features (consistency, club
change, league change, sample size, age) to see whether there is signal the model misses.

Run:  python -m engine.multiseason      (writes data/models/multiseason_test.json)
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from .config import MODEL_DIR
from .schema import METRICS

MODES = {"two_season": 1.0, "recency": 0.5}
MIN_PRIOR_MINUTES = 450
OUT = MODEL_DIR / "multiseason_test.json"


def _with_prior(agg: pd.DataFrame) -> pd.DataFrame:
    """Each player league season with his previous season in the same league and provider (if any)."""
    a = agg[agg["minutes"] >= MIN_PRIOR_MINUTES].sort_values("season_start")
    cols = ["pid", "league", "provider", "season", "season_start", "minutes", "team"] + [f"{m}_p90" for m in METRICS]
    a = a[cols].copy()
    prev = a.groupby(["pid", "league", "provider"]).shift(1)
    prev.columns = [f"prev_{c}" for c in prev.columns]
    out = pd.concat([a, prev], axis=1)
    gap = out["season_start"] - out["prev_season_start"]
    keep = gap.between(0.5, 1.2)
    for c in prev.columns:
        out.loc[~keep, c] = np.nan
    return out


def blended_pairs(pairs: pd.DataFrame, agg: pd.DataFrame, decay: float) -> pd.DataFrame:
    """Copy of the pairs with src_<metric> and src_minutes replaced by a minutes weighted blend of the source
    season and the previous same league season. Destination values are untouched."""
    w = _with_prior(agg)
    key = ["pid", "league", "provider", "season"]
    p = pairs.merge(w.rename(columns={"league": "src_league", "provider": "src_provider", "season": "src_season"})
                    [["pid", "src_league", "src_provider", "src_season", "prev_minutes"]
                     + [f"prev_{m}_p90" for m in METRICS]],
                    on=["pid", "src_league", "src_provider", "src_season"], how="left")
    has = p["prev_minutes"].notna()
    w1, w0 = p["src_minutes"], p["prev_minutes"].fillna(0) * decay
    for m in METRICS:
        cur, prv = p[f"src_{m}"], p[f"prev_{m}_p90"]
        ok = has & cur.notna() & prv.notna()
        p.loc[ok, f"src_{m}"] = (cur[ok] * w1[ok] + prv[ok] * w0[ok]) / (w1[ok] + w0[ok])
    p.loc[has, "src_minutes"] = w1[has] + w0[has]
    p["has_prior"] = has
    return p.drop(columns=["prev_minutes"] + [f"prev_{m}_p90" for m in METRICS])


def _paired(base: pd.DataFrame, alt: pd.DataFrame, flag: pd.DataFrame) -> dict:
    k = ["pid", "src_season", "dst_season", "src_league", "dst_league"]
    d = base[k + ["actual", "model", "lo80", "hi80", "position_group"]].merge(
        alt[k + ["model", "lo80", "hi80"]], on=k, suffixes=("", "_alt")).merge(flag, on=k, how="left")
    d["has_prior"] = d["has_prior"].fillna(False).astype(bool)
    out = {}
    for name, g in (("all test moves", d), ("moves with a previous season", d[d["has_prior"]])):
        if g.empty:
            continue
        e0, e1 = (g["model"] - g["actual"]).abs(), (g["model_alt"] - g["actual"]).abs()
        rng = np.random.default_rng(0)
        idx = rng.integers(0, len(g), (1000, len(g))) if len(g) >= 10 else None
        out[name] = {
            "n": int(len(g)), "mae_current": float(e0.mean()), "mae_multi": float(e1.mean()),
            "change_pct": float((e1.mean() / e0.mean() - 1) * 100) if e0.mean() > 0 else 0.0,
            "p_multi_better": float((e1.to_numpy()[idx].mean(1) < e0.to_numpy()[idx].mean(1)).mean()) if idx is not None else None,
            "cov80_current": float(((g["actual"] >= g["lo80"]) & (g["actual"] <= g["hi80"])).mean()),
            "cov80_multi": float(((g["actual"] >= g["lo80_alt"]) & (g["actual"] <= g["hi80_alt"])).mean())}
    return out


def diagnostics(eng) -> list[dict]:
    """Existing backtest errors split by history features. Positive bias = model projected too high."""
    from .audit import selected_keys
    w = _with_prior(eng.agg)
    rows = []
    for base, key in selected_keys(eng).items():
        P = eng.backtest_preds.get(key)
        if P is None or P.empty:
            continue
        d = P.merge(w.rename(columns={"league": "src_league", "season": "src_season"})
                    [["pid", "src_league", "src_season", "team", "prev_team", f"{base}_p90", f"prev_{base}_p90",
                      "prev_minutes"]], on=["pid", "src_league", "src_season"], how="left")
        d["err"] = d["model"] - d["actual"]
        ratio = np.log((d[f"{base}_p90"] + 0.05) / (d[f"prev_{base}_p90"] + 0.05)).abs()
        feats = {
            "Previous season in same league": np.where(d["prev_minutes"].notna(), "yes", "no"),
            "Consistency (change vs previous season)": pd.cut(ratio, [-0.01, 0.2, 0.5, 10],
                                                               labels=["steady", "moderate", "large swing"]).astype(str),
            "Changed club before the source season": np.where(d["prev_minutes"].isna(), "no previous season",
                                                              np.where(d["prev_team"] != d["team"], "yes", "no")),
            "Source minutes": pd.cut(d["src_minutes"], [0, 900, 1800, 10000],
                                     labels=["450 to 900", "900 to 1800", "1800+"], right=False).astype(str),
        }
        for fname, f in feats.items():
            for lv, g in d.groupby(pd.Series(f, index=d.index)):
                if lv in ("nan", "None") or len(g) < 15:
                    continue
                rows.append({"metric": METRICS[base].label, "feature": fname, "group": lv, "n": int(len(g)),
                             "mae_model": float(g["err"].abs().mean()),
                             "mae_carry_over": float((g["persistence"] - g["actual"]).abs().mean()),
                             "bias": float(g["err"].mean())})
    return rows


def run(eng, progress=print) -> dict:
    from .audit import selected_keys
    from .backtest import run_backtest
    res = {"run_at": datetime.now().isoformat(timespec="seconds"), "model_run": eng.run_id,
           "modes": {}, "verdict": {}}
    keys = list(selected_keys(eng).values())
    base_preds = eng.backtest_preds
    for mode, decay in MODES.items():
        progress(f"Backtesting the {mode} input")
        bp = blended_pairs(eng.pairs, eng.agg, decay)
        flag = bp[bp["pair_type"] == "cross"][["pid", "src_season", "dst_season", "src_league", "dst_league",
                                                "has_prior"]]
        preds, _, _ = run_backtest(bp.drop(columns=["has_prior"]), eng.agg, eng.settings, keys)
        res["modes"][mode] = {k: _paired(base_preds[k], preds[k], flag) for k in keys if k in preds and k in base_preds}
        res["prior_share"] = float(flag["has_prior"].mean())
    # verdict per metric: adopt only if a mode beats the current input on all test moves by 2%+ with P >= 0.9
    for k in keys:
        best = None
        for mode in MODES:
            r = res["modes"].get(mode, {}).get(k, {}).get("all test moves")
            if r and r["change_pct"] <= -2 and (r["p_multi_better"] or 0) >= 0.9 and 0.7 <= r["cov80_multi"] <= 0.9:
                if best is None or r["change_pct"] < best[1]:
                    best = (mode, r["change_pct"])
        res["verdict"][k] = {"adopt": bool(best), "mode": best[0] if best else None}
    res["diagnostics"] = diagnostics(eng)
    OUT.write_text(json.dumps(res, indent=1, default=float))
    return res


def load_saved() -> dict | None:
    try:
        return json.loads(OUT.read_text())
    except Exception:
        return None


if __name__ == "__main__":
    from .api import Engine
    r = run(Engine.load())
    print(json.dumps(r["verdict"], indent=1))
