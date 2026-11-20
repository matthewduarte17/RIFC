"""Public API of the prediction engine.

The Streamlit app only talks to this module, so the engine can later be called
from the club's scouting database or another front end:

    from engine.api import Engine
    eng = Engine.load()                    # latest fitted model and backtest
    res = eng.project(player, "USL Championship", scenario)
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from . import db
from .backtest import run_backtest, validation_for
from .config import MODEL_DIR, ModelSettings
from .model import SUPPORT_METRICS, MetricModel, Readiness, fit_metric, readiness, variant_label
from .normalize import norm_team
from .schema import METRICS, RIFC_TEAM_HINTS, USLC
from .transfers import build_pairs, metric_pairs, team_context


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
@dataclass
class PlayerInput:
    name: str
    position_group: str
    league: str
    season: str
    minutes: float
    team: str | None = None
    age: float | None = None           # age during the source season
    starts: float | None = None
    matches: float | None = None
    per90: dict = field(default_factory=dict)   # metric -> per 90 (or % for pct metrics)
    provider: str | None = None
    penalty_goals: float | None = None
    penalties_taken: float | None = None
    src_team_idx: float | None = None


@dataclass
class Scenario:
    minutes: float = 2200
    start_share: float = 0.85          # starter ~0.85, rotation ~0.5, substitute ~0.2
    years_ahead: float = 1.0           # gap between source and target season (ages the player)
    target_team_idx: float | None = None   # team attacking index for the team specific projection
    penalty_taker: bool = False        # manual assumption
    penalty_share: float = 1.0         # manual assumption: share of team penalties taken
    usage_adjust_pct: float = 0.0      # manual assumption, shown separately


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
class Engine:
    def __init__(self, settings: ModelSettings, models: dict, pairs: pd.DataFrame, agg: pd.DataFrame,
                 review: pd.DataFrame, team_ctx: pd.DataFrame, fingerprint: str, run_id=None,
                 validation: dict | None = None, backtest_preds: dict | None = None,
                 backtest_summary: pd.DataFrame | None = None, backtest_id=None):
        self.settings, self.models, self.pairs, self.agg = settings, models, pairs, agg
        self.review, self.team_ctx, self.fingerprint, self.run_id = review, team_ctx, fingerprint, run_id
        self.validation = validation or {}
        self.backtest_preds = backtest_preds or {}
        self.backtest_summary = backtest_summary if backtest_summary is not None else pd.DataFrame()
        self.backtest_id = backtest_id

    # ---- build ------------------------------------------------------------
    @classmethod
    def fit(cls, settings: ModelSettings | None = None, db_path=None, save=True) -> "Engine":
        settings = settings or ModelSettings()
        pairs, agg, review = build_pairs(settings, db_path=db_path)
        teams = db.load_table("team_seasons", db_path, active_only=True)
        ctx = team_context(teams)
        models = {}
        for m in METRICS:
            p = metric_pairs(pairs, m, settings)
            if p.empty:
                continue
            mm = fit_metric(p, m, settings, agg[f"{m}_p90"])
            if mm is not None:
                models[m] = mm
            # variant that also reads the player's underlying numbers (xG, xA)
            for opt in SUPPORT_METRICS.get(m, []):
                if all(p[f"src_{x}"].notna().mean() >= 0.8 for x in opt):
                    ms = fit_metric(p, m, settings, agg[f"{m}_p90"],
                                    support_refs={x: agg[f"{x}_p90"] for x in opt})
                    if ms is not None:
                        models[f"{m}+{','.join(opt)}"] = ms
                    break
        eng = cls(settings, models, pairs, agg, review, ctx, db.data_fingerprint(db_path))
        if save:
            eng._save_run("model_runs", db_path)
        return eng

    def run_backtest(self, db_path=None, save=True):
        preds, summary, validation = run_backtest(self.pairs, self.agg, self.settings, list(self.models))
        self.backtest_preds, self.backtest_summary, self.validation = preds, summary, validation
        if save:
            self._save_run("backtest_runs", db_path)
        return summary

    def _save_run(self, table, db_path):
        with db.connect(db_path) as con:
            rid = db.next_id(con, "run_seq")
            path = MODEL_DIR / f"{table}_{rid:04d}.pkl"
            with open(path, "wb") as f:
                pickle.dump(self.__dict__, f)
            summary = self.summary_table().to_dict("records") if table == "model_runs" else \
                self.backtest_summary.to_dict("records")
            con.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?)",
                        [rid, datetime.now(), json.dumps(self.settings.to_dict(), default=str),
                         json.dumps(summary, default=str), str(path), self.fingerprint])
        if table == "model_runs":
            self.run_id = rid
        else:
            self.backtest_id = rid

    @classmethod
    def load(cls, db_path=None) -> "Engine | None":
        runs = db.query("SELECT * FROM model_runs ORDER BY run_id DESC LIMIT 1", path=db_path)
        if runs.empty:
            return None
        with open(MODEL_DIR / Path(runs.iloc[0]["artifact_path"]).name, "rb") as f:   # portable across machines
            state = pickle.load(f)
        eng = cls.__new__(cls)
        eng.__dict__.update(state)
        eng.run_id = int(runs.iloc[0]["run_id"])
        bt = db.query("SELECT * FROM backtest_runs WHERE run_id > ? ORDER BY run_id DESC LIMIT 1",
                      [int(runs.iloc[0]["run_id"])], path=db_path)
        if not bt.empty:
            with open(MODEL_DIR / Path(bt.iloc[0]["artifact_path"]).name, "rb") as f:
                bstate = pickle.load(f)
            if bstate.get("fingerprint") == eng.fingerprint:
                eng.validation = bstate["validation"]
                eng.backtest_preds = bstate["backtest_preds"]
                eng.backtest_summary = bstate["backtest_summary"]
                eng.backtest_id = int(bt.iloc[0]["run_id"])
        return eng

    def is_stale(self, db_path=None) -> bool:
        return db.training_subset(self.fingerprint) != db.training_subset(db.data_fingerprint(db_path))

    # ---- summaries --------------------------------------------------------
    def summary_table(self) -> pd.DataFrame:
        rows = []
        keys = list(METRICS) + [k for k in self.models if "+" in k]
        for m in keys:
            base = m.partition("+")[0]
            meta = METRICS[base]
            mm = self.models.get(m)
            p = metric_pairs(self.pairs, base, self.settings) if not self.pairs.empty else pd.DataFrame()
            for x in (m.partition("+")[2].split(",") if "+" in m else []):
                p = p[p[f"src_{x}"].notna()] if not p.empty else p
            rows.append({
                "metric": m, "label": variant_label(m), "group": meta.group,
                "pairs total": 0 if p.empty else len(p),
                "cross league pairs": 0 if p.empty else int((p["pair_type"] == "cross").sum()),
                "fitted": mm is not None,
                "enough data": bool(mm is not None and mm.n_cross >= self.settings.min_cross_league_pairs),
                "slope": float(mm.beta[1]) if mm else np.nan,
                "penalty multiplier": mm.penalty_mult if mm else np.nan,
                "tuned by time CV": mm.tuned if mm else False,
                "LOO 80% coverage": mm.loo_coverage80 if mm else np.nan,
            })
        return pd.DataFrame(rows)

    def leagues(self) -> list[str]:
        return sorted(self.agg["league"].dropna().unique()) if not self.agg.empty else []

    # ---- team context -----------------------------------------------------
    def team_idx(self, team: str | None, league: str, season: str | None = None) -> tuple[float | None, str]:
        if self.team_ctx.empty or not team:
            return None, "no team data"
        t = self.team_ctx[(self.team_ctx["team_norm"] == norm_team(team)) & (self.team_ctx["league"] == league)]
        if season is not None:
            t2 = t[t["season"] == season]
            t = t2 if not t2.empty else t
        if t.empty:
            return None, "team not found in team data"
        r = t.sort_values("season").iloc[-1]
        return float(r["team_attack_idx"]), f"{r['team']} {r['season']} ({r['attack_basis']} based)"

    def rifc_rows(self) -> pd.DataFrame:
        if self.team_ctx.empty:
            return pd.DataFrame()
        m = self.team_ctx["team_norm"].fillna("").map(lambda s: any(h in s for h in RIFC_TEAM_HINTS))
        return self.team_ctx[m].sort_values("season")

    def league_penalty_conversion(self, league: str) -> tuple[float | None, int]:
        a = self.agg[self.agg["league"] == league]
        att = a["penalties_taken_total"].sum()
        if att < 20:
            a = self.agg
            att = a["penalties_taken_total"].sum()
        if att < 20:
            return None, int(att)
        return float(a["penalty_goals_total"].sum() / att), int(att)

    # ---- benchmarks -------------------------------------------------------
    def benchmark(self, league: str, position: str, metric: str, season: str | None = None,
                  provider: str | None = None) -> pd.Series:
        a = self.agg[(self.agg["league"] == league) & (self.agg["position_group"] == position)
                     & (self.agg["minutes"] >= self.settings.benchmark_min_minutes)]
        if season is None and not a.empty:
            season = a.sort_values("season_start")["season"].iloc[-1]
        a = a[a["season"] == season] if season else a
        if provider and (a["provider"] == provider).sum() >= 10 and not METRICS[metric].definition_stable:
            a = a[a["provider"] == provider]
        return a[f"{metric}_p90"].dropna()

    @staticmethod
    def percentile(dist: pd.Series, value) -> float | None:
        if dist is None or len(dist) < 10 or value is None or np.isnan(value):
            return None
        # mid rank, so ties (e.g. many zeros) land in the middle instead of at 100
        return float(((dist < value).sum() + 0.5 * (dist == value).sum()) / len(dist) * 100)

    # ---- projection -------------------------------------------------------
    def _row(self, p: PlayerInput, metric: str, target_league: str, sc: Scenario, team_idx,
             mm: MetricModel | None = None) -> pd.DataFrame:
        extra = {f"src_{x}": p.per90.get(x) for x in (mm.design.support if mm else {})}
        return pd.DataFrame([{**extra, 
            "src_minutes": p.minutes, "dst_minutes": sc.minutes, "position_group": p.position_group,
            "src_league": p.league, "dst_league": target_league,
            "age_dst": (p.age + sc.years_ahead) if p.age is not None else np.nan,
            "src_team_idx": p.src_team_idx if p.src_team_idx is not None else 0.0,
            "dst_team_idx": team_idx if team_idx is not None else 0.0,
            "dst_start_share": sc.start_share, f"src_{metric}": p.per90.get(metric),
        }])

    def model_key(self, metric: str, p: PlayerInput) -> str:
        """Use the variant with underlying numbers when the player has them."""
        cands = [k for k in self.models if k.partition("+")[0] == metric and
                 all(p.per90.get(x) is not None for x in k.partition("+")[2].split(",") if x)]
        if not cands:
            return metric
        if self.validation:
            scored = [k for k in cands if k in self.validation]
            if scored:   # the variant that was more accurate out of time
                return min(scored, key=lambda k: self.validation[k]["overall"]["mae_model"])
        return max(cands, key=lambda k: len(k))   # not backtested yet: prefer underlying numbers

    def readiness(self, metric, p: PlayerInput, target_league: str) -> Readiness:
        key = self.model_key(metric, p)
        v = validation_for(self.validation, key, p.position_group, self.settings.min_test_pairs_for_validation)
        return readiness(self.models.get(key), p.position_group, p.league, target_league, self.settings, v)

    def confidence(self, key: str, position: str, src: str, dst: str, status: str) -> dict:
        """How much to trust one projection, from the backtest evidence closest to this case.

        None   : not projected (insufficient data)
        Low    : not validated, or under 20 backtest moves for this transition or this position,
                 or the model failed the checks in that transition or position cell
        Medium : validated, and at least 20 backtest moves for both this transition and this position
        High   : validated, and at least 50 for both
        """
        from .audit import LIMITED, ADEQUATE, _cell, _verdict, transition_name
        P = self.backtest_preds.get(key)
        out = {"confidence": "None", "confidence_reason": "", "n_transition": 0, "n_position": 0}
        if status == "insufficient":
            out["confidence_reason"] = "Not projected."
            return out
        if P is None or P.empty:
            out.update(confidence="Low", confidence_reason="No backtest for this metric yet.")
            return out
        cells = {"transition": P[(P["src_league"] == src) & (P["dst_league"] == dst)],
                 "position": P[P["position_group"] == position]}
        out["n_transition"], out["n_position"] = len(cells["transition"]), len(cells["position"])
        notes, failed = [], []
        for name, d in cells.items():
            label = transition_name(src, dst) if name == "transition" else position
            if len(d) < LIMITED:
                notes.append(f"only {len(d)} backtest moves for {label}")
                continue
            v = _verdict(_cell(d), self.settings)
            if v.startswith("Not reliable"):
                failed.append(f"{label}: {v[len('Not reliable: '):]}")
        if status != "validated":
            level = "Low"
            notes.insert(0, "not validated")
        elif notes or failed:
            level = "Low"
        elif min(out["n_transition"], out["n_position"]) >= ADEQUATE:
            level = "High"
        else:
            level = "Medium"
        out["confidence"] = level
        out["confidence_reason"] = "; ".join(notes + failed) or (
            f"{out['n_transition']} backtest moves for {transition_name(src, dst)}, {out['n_position']} for {position}")
        return out

    def project(self, p: PlayerInput, target_league: str = USLC, sc: Scenario | None = None,
                team_idx: float | None = None) -> pd.DataFrame:
        """One row per metric the player has. team_idx=None gives the neutral projection."""
        sc = sc or Scenario()
        rows = []
        for m, meta in METRICS.items():
            v = p.per90.get(m)
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            key = self.model_key(m, p)
            mm: MetricModel | None = self.models.get(key)
            rd = self.readiness(m, p, target_league)
            row = {"metric": m, "label": meta.label, "group": meta.group, "kind": meta.kind,
                   "current_p90": v, "status": rd.status, "reasons": " ".join(rd.reasons),
                   "model_key": key, "based_on": variant_label(key).partition(", using ")[2] or "own record"}
            row.update(self.confidence(key, p.position_group, p.league, target_league, rd.status))
            if rd.can_project:
                df = self._row(p, m, target_league, sc, team_idx, mm)
                pr = mm.predict(df, dst_minutes=sc.minutes).iloc[0]
                row.update({k: float(pr[k]) for k in ("pred", "lo50", "hi50", "lo80", "hi80")})
                row["uses_team_context"] = mm.design.use_context
                row["uses_role"] = mm.design.use_role
            rows.append(row)
        out = pd.DataFrame(rows)
        if out.empty:
            return out
        rate = out["kind"] == "rate"
        for c in ("pred", "lo50", "hi50", "lo80", "hi80"):
            if c in out:
                out[f"{c}_season"] = np.where(rate, out[c] * sc.minutes / 90, np.nan)
        return out

    def drivers(self, p: PlayerInput, metric: str, target_league: str, sc: Scenario, team_idx=None):
        mm = self.models.get(self.model_key(metric, p))
        if mm is None:
            return pd.DataFrame()
        return mm.drivers(self._row(p, metric, target_league, sc, team_idx, mm))

    def penalty_component(self, p: PlayerInput, target_team: str | None, target_league: str, sc: Scenario):
        """Expected penalty goals per 90 at the target team. Team rate and conversion come from data;
        being the taker and the share are manual assumptions."""
        if not sc.penalty_taker:
            return None
        if self.team_ctx.empty or not target_team:
            return {"ok": False, "why": "No team data for the target team."}
        t = self.team_ctx[(self.team_ctx["team_norm"] == norm_team(target_team))
                          & (self.team_ctx["league"] == target_league)].sort_values("season")
        if t.empty or t["pens_per_match"].isna().all():
            return {"ok": False, "why": "Target team penalty data not imported."}
        team_rate = float(t["pens_per_match"].dropna().iloc[-1])
        conv, n = self.league_penalty_conversion(target_league)
        if conv is None:
            return {"ok": False, "why": "Fewer than 20 penalty attempts in the data to estimate conversion."}
        k = 10.0
        pg, pt = (p.penalty_goals or 0.0), (p.penalties_taken or 0.0)
        player_conv = (pg + k * conv) / (pt + k)
        p90 = team_rate * sc.penalty_share * player_conv  # a match is ~90 minutes of team time
        return {"ok": True, "pen_goals_p90": p90, "team_pens_per_match": team_rate, "conversion": player_conv,
                "league_conversion": conv, "season": t["season"].iloc[-1]}

    def similar_rifc(self, position: str, metrics: list[str]) -> pd.DataFrame:
        a = self.agg
        if a.empty:
            return a
        m = a["team"].fillna("").str.lower().map(lambda s: any(h in s for h in RIFC_TEAM_HINTS))
        r = a[m & (a["position_group"] == position) & (a["minutes"] >= 450)]
        if r.empty:
            return r
        latest = r["season_start"].max()
        r = r[r["season_start"] == latest]
        cols = ["player_name", "season", "minutes"] + [f"{x}_p90" for x in metrics]
        return r[cols].sort_values("minutes", ascending=False)


def player_from_agg(row: pd.Series) -> PlayerInput:
    per90 = {m: (None if pd.isna(row.get(f"{m}_p90")) else float(row[f"{m}_p90"])) for m in METRICS}
    return PlayerInput(
        name=row["player_name"], position_group=row["position_group"], league=row["league"],
        season=row["season"], minutes=float(row["minutes"]), team=row.get("team"),
        age=None if pd.isna(row.get("age_at_season")) else float(row["age_at_season"]),
        starts=None if pd.isna(row.get("starts")) else float(row["starts"]),
        matches=None if pd.isna(row.get("matches")) else float(row["matches"]),
        per90={k: v for k, v in per90.items() if v is not None}, provider=row.get("provider"),
        penalty_goals=None if pd.isna(row.get("penalty_goals_total")) else float(row["penalty_goals_total"]),
        penalties_taken=None if pd.isna(row.get("penalties_taken_total")) else float(row["penalties_taken_total"]),
        src_team_idx=None if pd.isna(row.get("team_attack_idx")) else float(row["team_attack_idx"]),
    )
