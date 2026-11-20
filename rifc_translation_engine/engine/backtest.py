"""Phase 3b: time based backtest against historical moves.

For every test year Y the model is refitted using only pairs whose destination
season ended before Y started, then scored on cross league moves into seasons
starting in Y. No information from Y or later is used, including the penalty
tuning and the transform offset.

Baselines
  persistence : destination per 90 = source per 90 (no translation at all)
  pair ratio  : source per 90 x minutes weighted ratio for that league pair in
                training data (falls back to all cross league moves). This is
                the naive 'conversion factor' approach.
  dest mean   : destination league positional average from earlier seasons

Interval coverage uses the actual destination minutes (in use, minutes are a
scenario input you set), so coverage reflects per 90 noise at the minutes
the player really played.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ModelSettings
from .model import fit_metric, variant_label
from .schema import METRICS
from .transfers import metric_pairs


def _bins(minutes):
    return pd.cut(minutes, [0, 900, 1800, 10000], labels=["450 to 900", "900 to 1800", "1800+"], right=False)


def backtest_metric(pairs: pd.DataFrame, agg: pd.DataFrame, metric: str, settings: ModelSettings,
                    support: list | None = None):
    p = metric_pairs(pairs, metric, settings)
    for sm in support or []:
        p = p[p[f"src_{sm}"].notna()]
    if p.empty:
        return None
    years = sorted({int(np.floor(v)) for v in p.loc[p["pair_type"] == "cross", "dst_season_start"]})
    preds = []
    for Y in years:
        train = p[p["dst_season_end"] < Y + 0.2]
        test = p[(np.floor(p["dst_season_start"]) == Y) & (p["pair_type"] == "cross")]
        if test.empty or (train["pair_type"] == "cross").sum() < settings.min_cross_league_pairs:
            continue
        hist = agg[agg["season_end"] < Y + 0.2]
        model = fit_metric(train, metric, settings, hist[f"{metric}_p90"],
                           support_refs={sm: hist[f"{sm}_p90"] for sm in support} if support else None)
        if model is None:
            continue
        # only score test pairs whose leagues the training data can support
        ok = test.apply(lambda r: model.cross_by_league.get(r["src_league"], 0) >= settings.min_league_pairs
                        and model.cross_by_league.get(r["dst_league"], 0) >= settings.min_league_pairs
                        and model.connected(r["src_league"], r["dst_league"]), axis=1)
        test = test[ok]
        if test.empty:
            continue
        pr = model.predict(test, levels=(0.5, 0.8))
        out = test[["pid", "player_name", "position_group", "src_league", "dst_league", "src_season",
                    "dst_season", "src_minutes", "dst_minutes"]].copy()
        out["test_year"] = Y
        out["actual"] = test[f"dst_{metric}"].to_numpy()
        out["source"] = test[f"src_{metric}"].to_numpy()
        out["model"] = pr["pred"].to_numpy()
        for c in ("lo50", "hi50", "lo80", "hi80"):
            out[c] = pr[c].to_numpy()
        # baselines
        out["persistence"] = out["source"]
        cross_train = train[train["pair_type"] == "cross"]
        w = np.clip(np.minimum(cross_train["src_minutes"], cross_train["dst_minutes"]) / 900, 0.5, 3)
        if METRICS[metric].kind == "rate":
            def ratio(src, dst):
                g = cross_train[(cross_train["src_league"] == src) & (cross_train["dst_league"] == dst)]
                gw = w.loc[g.index]
                if len(g) >= 5 and (g[f"src_{metric}"] * gw).sum() > 0:
                    return (g[f"dst_{metric}"] * gw).sum() / (g[f"src_{metric}"] * gw).sum()
                return (cross_train[f"dst_{metric}"] * w).sum() / max((cross_train[f"src_{metric}"] * w).sum(), 1e-9)
            out["pair_ratio"] = [s * ratio(a, b) for s, a, b in zip(out["source"], out["src_league"], out["dst_league"])]
        else:
            def diff(src, dst):
                g = cross_train[(cross_train["src_league"] == src) & (cross_train["dst_league"] == dst)]
                g = g if len(g) >= 5 else cross_train
                return float((g[f"dst_{metric}"] - g[f"src_{metric}"]).mean())
            out["pair_ratio"] = [s + diff(a, b) for s, a, b in zip(out["source"], out["src_league"], out["dst_league"])]
        hist = agg[(agg["season_end"] < Y + 0.2) & (agg["minutes"] >= settings.benchmark_min_minutes)]
        means = hist.groupby(["league", "position_group"])[f"{metric}_p90"].mean()
        fallback = cross_train[f"dst_{metric}"].mean()
        out["dest_mean"] = [means.get((lg, pg), np.nan) for lg, pg in zip(out["dst_league"], out["position_group"])]
        out["dest_mean"] = out["dest_mean"].fillna(fallback)
        preds.append(out)
    if not preds:
        return None
    return pd.concat(preds, ignore_index=True)


def _score(df: pd.DataFrame) -> dict:
    res = {"n_test": int(len(df))}
    for c in ("model", "persistence", "pair_ratio", "dest_mean"):
        e = df[c] - df["actual"]
        res[f"mae_{c}"] = float(np.abs(e).mean())
        res[f"rmse_{c}"] = float(np.sqrt((e ** 2).mean()))
    # paired bootstrap: how often the model beats carry over / the best baseline on resampled test sets
    rng = np.random.default_rng(0)
    em = np.abs(df["model"].to_numpy() - df["actual"].to_numpy())
    ep = np.abs(df["persistence"].to_numpy() - df["actual"].to_numpy())
    best = min(("pair_ratio", "dest_mean"), key=lambda c: res[f"mae_{c}"])
    eb = np.abs(df[best].to_numpy() - df["actual"].to_numpy())
    if len(df) >= 10:
        idx = rng.integers(0, len(df), (1000, len(df)))
        res["p_beats_carry_over"] = float((em[idx].mean(1) < ep[idx].mean(1)).mean())
        res["p_beats_best_baseline"] = float((em[idx].mean(1) < eb[idx].mean(1)).mean())
    res["coverage50"] = float(((df["actual"] >= df["lo50"]) & (df["actual"] <= df["hi50"])).mean())
    res["coverage80"] = float(((df["actual"] >= df["lo80"]) & (df["actual"] <= df["hi80"])).mean())
    res["mean_actual"] = float(df["actual"].mean())
    return res


def summarize(preds: pd.DataFrame, by: list[str] | None = None) -> pd.DataFrame:
    if preds is None or preds.empty:
        return pd.DataFrame()
    if not by:
        return pd.DataFrame([_score(preds)])
    rows = []
    for key, g in preds.groupby(by, observed=True):
        key = key if isinstance(key, tuple) else (key,)
        rows.append({**dict(zip(by, key)), **_score(g)})
    return pd.DataFrame(rows).sort_values("n_test", ascending=False)


def run_backtest(pairs: pd.DataFrame, agg: pd.DataFrame, settings: ModelSettings, metrics=None):
    """metrics: model keys like 'goals' or 'goals+xg'. Returns {key: predictions}, summary, validation."""
    metrics = metrics or list(METRICS)
    all_preds, rows, validation = {}, [], {}
    for m in metrics:
        base, _, sup = m.partition("+")
        pr = backtest_metric(pairs, agg, base, settings, sup.split(",") if sup else None)
        if pr is None:
            continue
        pr["src_minutes_bin"] = _bins(pr["src_minutes"])
        pr["dst_minutes_bin"] = _bins(pr["dst_minutes"])
        all_preds[m] = pr
        s = _score(pr)
        validation[m] = {"overall": s,
                         "by_position": summarize(pr, ["position_group"]).set_index("position_group")
                         .to_dict("index")}
        rows.append({"metric": m, "label": variant_label(m), **s})
    return all_preds, pd.DataFrame(rows), validation


def validation_for(validation: dict, metric: str, position: str, min_n: int) -> dict | None:
    """Position level backtest when it has enough pairs, else the metric overall."""
    v = validation.get(metric) if validation else None
    if not v:
        return None
    pos = v["by_position"].get(position)
    if pos and pos["n_test"] >= min_n:
        return pos
    return v["overall"]
