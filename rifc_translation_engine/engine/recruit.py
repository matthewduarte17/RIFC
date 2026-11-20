"""Recruitment layer: turns the validated engine output into scouting answers.

Nothing here changes a projection. It selects role relevant metrics, adds the
benchmarks, historical error and RIFC comparisons, and writes cautious summary
text from rules that only use numbers shown on the page.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import db
from . import teamcontext as TC
from . import teamstats as T
from .api import Engine, PlayerInput, Scenario, player_from_agg
from .audit import LIMITED, transition_name
from .normalize import norm_team
from .schema import METRICS, POSITION_LABELS, RIFC_TEAM_HINTS, USLC

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
ROLES = {
    "Center forward": {"groups": ["ST"], "metrics": [
        "goals", "np_goals", "xg", "shots", "assists", "xa", "succ_dribbles", "aerials_won", "aerial_win_pct",
        "touches_att_box"]},
    "Winger": {"groups": ["W"], "metrics": [
        "goals", "xg", "assists", "xa", "prog_carries", "succ_dribbles", "key_passes", "passes_to_box",
        "touches_att_box", "final_third_entries"]},
    "Attacking midfielder": {"groups": ["AM"], "metrics": [
        "key_passes", "xa", "passes_to_box", "prog_passes", "prog_carries", "goal_contributions", "xg",
        "final_third_entries"]},
    "Central or defensive midfielder": {"groups": ["CM", "DM"], "metrics": [
        "prog_passes", "final_third_entries", "pass_pct", "interceptions", "succ_def_actions", "def_duels_won",
        "def_duel_win_pct", "aerials_won"]},
    "Fullback": {"groups": ["FB"], "metrics": [
        "prog_passes", "prog_carries", "crosses", "accurate_crosses", "key_passes", "xa", "def_duels_won",
        "def_duel_win_pct", "interceptions", "succ_def_actions"]},
    "Center back": {"groups": ["CB"], "metrics": [
        "aerials_won", "aerial_win_pct", "def_duels_won", "def_duel_win_pct", "prog_passes", "pass_pct",
        "interceptions", "succ_def_actions"]},
}
DEFAULT_ROLE = {"ST": "Center forward", "W": "Winger", "AM": "Attacking midfielder", "CM": "Central or defensive midfielder",
                "DM": "Central or defensive midfielder", "FB": "Fullback", "CB": "Center back"}
NOT_IN_DATA = {
    "Center forward": [],
    "Winger": [],
    "Attacking midfielder": ["shot creating actions"],
    "Central or defensive midfielder": ["ball recoveries", "tackles"],
    "Fullback": ["ball recoveries"],
    "Center back": ["ground duels (Wyscout splits duels into defensive and offensive, shown as defensive duels)"],
}
STRIKER_QUESTIONS = [("Goals", ["goals", "np_goals"]), ("Expected goals", ["xg"]),
                     ("Chance creation", ["xa", "key_passes", "assists"]),
                     ("Ball carrying", ["succ_dribbles", "prog_carries"]), ("Aerial contribution", ["aerials_won", "aerial_win_pct"])]


# Headline projection cards per role (the first metrics a GM asks about)
PRIMARY = {
    "Center forward": ["goals", "np_goals", "xg", "assists", "goal_contributions"],
    "Winger": ["goals", "xg", "assists", "xa", "succ_dribbles"],
    "Attacking midfielder": ["xa", "key_passes", "goal_contributions", "prog_carries"],
    "Central or defensive midfielder": ["prog_passes", "final_third_entries", "interceptions", "succ_def_actions"],
    "Fullback": ["prog_passes", "accurate_crosses", "def_duels_won", "interceptions"],
    "Center back": ["aerials_won", "def_duels_won", "interceptions", "prog_passes"],
}
# Season total wording for counting metrics
UNIT = {"goals": "goals", "np_goals": "non penalty goals", "xg": "xG", "assists": "assists", "xa": "xA",
        "goal_contributions": "goals and assists", "shots": "shots", "key_passes": "key passes",
        "prog_passes": "progressive passes", "prog_carries": "progressive carries", "succ_dribbles": "dribbles",
        "final_third_entries": "final third entries", "touches_att_box": "box touches", "interceptions": "interceptions",
        "aerials_won": "aerial duels won", "def_duels_won": "defensive duels won", "succ_def_actions": "defensive actions",
        "passes_to_box": "passes into the box", "crosses": "crosses", "accurate_crosses": "accurate crosses",
        "npxg": "npxG", "tackles": "tackles"}
PHRASE = {"goals": "goal scoring rate", "np_goals": "non penalty scoring rate", "xg": "expected goals rate",
          "assists": "assist rate", "xa": "expected assists rate", "goal_contributions": "goal contribution rate",
          "pass_pct": "pass completion", "aerial_win_pct": "aerial win rate", "def_duel_win_pct": "defensive duel win rate"}


def role_groups(role: str) -> list[str]:
    return ROLES[role]["groups"]


# ---------------------------------------------------------------------------
# Player lookup
# ---------------------------------------------------------------------------
def player_key(r) -> str:
    return f"{r['player_name']}|{r['team']}|{r['league']}|{r['season']}"


def player_table(eng: Engine) -> pd.DataFrame:
    a = eng.agg[(eng.agg["position_group"].notna()) & (eng.agg["position_group"] != "GK")].copy()
    a["key"] = a.apply(player_key, axis=1)
    a["label"] = (a["player_name"] + " | " + a["team"].astype(str) + " | " + a["league"] + " " + a["season"] + " | "
                  + a["position_group"] + " | " + a["minutes"].round().astype(int).map("{:,}".format) + " min")
    return a.sort_values(["season_start", "minutes"], ascending=[False, False])


def find_player(eng: Engine, key: str) -> pd.Series | None:
    t = player_table(eng)
    hit = t[t["key"] == key]
    return None if hit.empty else hit.iloc[0]


def is_rifc(team) -> bool:
    return isinstance(team, str) and any(h in team.lower() for h in RIFC_TEAM_HINTS)


# ---------------------------------------------------------------------------
# Benchmarks and historical error
# ---------------------------------------------------------------------------
def latest_season(eng: Engine, league: str = USLC) -> str | None:
    a = eng.agg[eng.agg["league"] == league]
    return None if a.empty else a.sort_values("season_start")["season"].iloc[-1]


def positional_dist(eng: Engine, metric: str, groups: list[str], league: str = USLC, season: str | None = None):
    season = season or latest_season(eng, league)
    a = eng.agg[(eng.agg["league"] == league) & (eng.agg["season"] == season) & eng.agg["position_group"].isin(groups)
                & (eng.agg["minutes"] >= eng.settings.benchmark_min_minutes)]
    return a[f"{metric}_p90"].dropna()


def historical_error(eng: Engine, key: str, src: str, dst: str, position: str) -> dict:
    """Most specific backtest cell with at least LIMITED moves: transition and position, transition, position, all."""
    P = eng.backtest_preds.get(key)
    if P is None or P.empty:
        return {"mae": np.nan, "n": 0, "scope": "no backtest", "baseline_mae": np.nan, "coverage80": np.nan}
    cells = [("transition and position", (P["src_league"] == src) & (P["dst_league"] == dst) & (P["position_group"] == position)),
             ("transition", (P["src_league"] == src) & (P["dst_league"] == dst)),
             ("position", P["position_group"] == position), ("all moves", pd.Series(True, index=P.index))]
    for scope, m in cells:
        d = P[m]
        if len(d) >= LIMITED:
            label = {"transition and position": f"{transition_name(src, dst)}, {position}",
                     "transition": transition_name(src, dst), "position": f"{position}, all transitions",
                     "all moves": "all transitions and positions"}[scope]
            return {"mae": float((d["model"] - d["actual"]).abs().mean()), "n": len(d), "scope": label,
                    "baseline_mae": float((d["persistence"] - d["actual"]).abs().mean()),
                    "coverage80": float(((d["actual"] >= d["lo80"]) & (d["actual"] <= d["hi80"])).mean())}
    return {"mae": np.nan, "n": 0, "scope": "too few moves", "baseline_mae": np.nan, "coverage80": np.nan}


# ---------------------------------------------------------------------------
# Projection table
# ---------------------------------------------------------------------------
@dataclass
class Evaluation:
    player: PlayerInput
    row: pd.Series | None
    role: str
    minutes: float
    table: pd.DataFrame
    rifc_note: str
    rifc_season: str | None
    summary: dict = field(default_factory=dict)
    start_share: float = 0.85
    years_ahead: float = 1.0


def evaluate(eng: Engine, player: PlayerInput, role: str, minutes: float, start_share: float = 0.85,
             years_ahead: float = 1.0, row: pd.Series | None = None, target_team: str = "Rhode Island",
             metrics: list[str] | None = None) -> Evaluation:
    sc = Scenario(minutes=minutes, start_share=start_share, years_ahead=years_ahead)
    proj = eng.project(player, USLC, sc).set_index("metric") if player.per90 else pd.DataFrame()
    metrics = metrics or list(dict.fromkeys(PRIMARY.get(role, []) + ROLES[role]["metrics"]))
    groups = role_groups(role)
    saved = TC.load_saved()
    stats = T.load()
    seasons = sorted(stats["season"].unique()) if not stats.empty else []
    rifc_season = seasons[-1] if seasons else None
    dst_key, src_key = norm_team(target_team), norm_team(player.team) if player.team else None
    rows = []
    for m in metrics:
        meta = METRICS[m]
        r = {"metric": m, "Metric": meta.label, "kind": meta.kind, "group": meta.group}
        cur = player.per90.get(m)
        r["current"] = cur
        if m not in proj.index or cur is None:
            r.update({"status": "not available", "confidence": "None",
                      "why": "Not in this player's data." if cur is None else "No model for this metric."})
            rows.append(r)
            continue
        p = proj.loc[m]
        r.update({"status": p["status"], "confidence": p.get("confidence", "None"), "why": p["reasons"],
                  "reads": p.get("based_on", ""), "model_key": p.get("model_key", m)})
        dist = positional_dist(eng, m, groups)
        # never rank a player against his own league season when that season is a partial dataset
        partial = row is not None and bool(row.get("display_only"))
        r["current_pctl_src"] = None if partial else eng.percentile(
            positional_dist(eng, m, groups, player.league, player.season), cur)
        if p["status"] != "insufficient":
            r.update({k: float(p[k]) for k in ("pred", "lo50", "hi50", "lo80", "hi80")})
            r["pctl_uslc"] = eng.percentile(dist, r["pred"])
            r["pctl_uslc_lo"] = eng.percentile(dist, r["lo80"])
            r["pctl_uslc_hi"] = eng.percentile(dist, r["hi80"])
            if meta.kind == "rate":
                for k in ("pred", "lo80", "hi80"):
                    r[f"{k}_season"] = r[k] * minutes / 90
            he = historical_error(eng, r["model_key"], player.league, USLC, player.position_group)
            r.update({"hist_mae": he["mae"], "hist_n": he["n"], "hist_scope": he["scope"],
                      "hist_carry_mae": he["baseline_mae"], "hist_cov80": he["coverage80"]})
            # RIFC adjustment, only where the team context test adopted it
            shift, note = (None, "No StatsBomb team data.")
            if rifc_season:
                shift, note = TC.rifc_adjustment(m, saved, stats, dst_key, src_key, player.league, rifc_season)
            if shift is not None:
                mm = eng.models[r["model_key"]]
                pr = mm.predict(eng._row(player, m, USLC, sc, None, mm), dst_minutes=minutes).iloc[0]
                t = mm.transform
                r["rifc"] = float(t.inv(np.array([pr["t_mu"] + shift]))[0])
                r["rifc_lo80"] = float(t.inv(np.array([pr["t_mu"] + shift - 1.2816 * pr["t_sd"]]))[0])
                r["rifc_hi80"] = float(t.inv(np.array([pr["t_mu"] + shift + 1.2816 * pr["t_sd"]]))[0])
            r["rifc_note"] = note
        rows.append(r)
    tab = pd.DataFrame(rows)
    for c in ("pred", "lo50", "hi50", "lo80", "hi80", "pred_season", "lo80_season", "hi80_season", "pctl_uslc",
              "pctl_uslc_lo", "pctl_uslc_hi", "hist_mae", "hist_n", "hist_scope", "hist_carry_mae", "hist_cov80",
              "rifc", "rifc_lo80", "rifc_hi80", "rifc_note", "model_key", "reads", "current_pctl_src"):
        if c not in tab:
            tab[c] = np.nan      # every column exists even when nothing can be projected
    rifc_note = ("RIFC adjustment validated only for pass completion % (provisional); every other metric shows the "
                 "neutral Championship projection. RIFC team context is shown on the RIFC Team Profile page.")
    ev = Evaluation(player, row, role, minutes, tab, rifc_note, rifc_season, start_share=start_share,
                    years_ahead=years_ahead)
    ev.summary = summarize(eng, ev)
    return ev


# ---------------------------------------------------------------------------
# Plain language
# ---------------------------------------------------------------------------
def fmt(v, kind="rate", d=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v:.1f}%" if kind == "pct" else f"{v:.{d}f}"


def ordinal(n):
    n = int(round(n))
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def ev_scenario(ev: Evaluation) -> Scenario:
    return Scenario(minutes=ev.minutes, start_share=ev.start_share, years_ahead=ev.years_ahead)


NUM_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
             "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]


def approx(x: float) -> str:
    n = int(round(x))
    return NUM_WORDS[n] if 0 <= n <= 20 else f"{n:,}"


def change_words(cur, pred, kind) -> tuple[str, str]:
    """('decrease', '0.18 lower than now') style words; neutral, no colour judgement."""
    tol = 0.5 if kind == "pct" else 0.005
    if cur is None or pred is None or np.isnan(cur) or np.isnan(pred):
        return "", ""
    d = pred - cur
    if abs(d) < tol:
        return "stay about the same", "about the same as now"
    unit = " points" if kind == "pct" else ""
    return ("decrease" if d < 0 else "increase"), f"{fmt(abs(d), 'rate', 1 if kind == 'pct' else 2)}{unit} {'lower' if d < 0 else 'higher'} than now"


def metric_sentence(ev: Evaluation, m: str, lead: bool = True) -> str:
    t = ev.table.set_index("metric")
    if m not in t.index or pd.isna(t.loc[m].get("pred")):
        return ""
    r = t.loc[m]
    kind = r["kind"]
    verb, _ = change_words(r["current"], r["pred"], kind)
    who = "His" if lead else "His"
    what = PHRASE.get(m, f"rate of {METRICS[m].label.lower()}")
    unit = "" if kind == "pct" else " per 90"
    if verb == "stay about the same":
        s = f"{who} {what} is projected to stay about the same, {fmt(r['current'], kind)} now and {fmt(r['pred'], kind)}{unit} in the Championship"
    else:
        s = f"{who} {what} is projected to {verb} from {fmt(r['current'], kind)} to {fmt(r['pred'], kind)}{unit} in the Championship"
    if kind == "rate" and pd.notna(r.get("pred_season")):
        unit_ = UNIT.get(m, METRICS[m].label.lower())
        if int(round(r["pred_season"])) == 1:
            unit_ = {"goals": "goal", "assists": "assist", "non penalty goals": "non penalty goal",
                     "goals and assists": "goal or assist"}.get(unit_, unit_)
        s += (f", approximately {approx(r['pred_season'])} {unit_} across "
              f"{int(ev.minutes):,} minutes (80% range {r['lo80_season']:.0f} to {r['hi80_season']:.0f})")
    else:
        s += f" (80% range {fmt(r['lo80'], kind)} to {fmt(r['hi80'], kind)})"
    return s + "."


def headline(ev: Evaluation) -> str:
    """Two plain sentences on the role's first projected metrics, plus how far to trust them."""
    t = ev.table.set_index("metric")
    prim = [m for m in PRIMARY.get(ev.role, []) if m in t.index and pd.notna(t.loc[m].get("pred"))]
    if not prim:
        return "No metric for this role can be projected from the available evidence."
    out = [metric_sentence(ev, prim[0])]
    second = next((m for m in prim[1:] if m not in ("np_goals",) and not (prim[0] == "goals" and m == "xg")), None)
    if ev.role == "Center forward" and "xg" in prim and "assists" in prim:
        second = "assists"
    if second:
        out.append(metric_sentence(ev, second))
    r = t.loc[prim[0]]
    if pd.notna(r.get("hist_mae")) and r.get("hist_n"):
        out.append(f"Confidence {r['confidence']}: on {int(r['hist_n'])} past moves ({r['hist_scope']}) projections like "
                   f"this missed by {fmt(r['hist_mae'], r['kind'])} per 90 on average.")
    return " ".join(out)


def movers_history(eng: Engine, metric: str, src: str, groups: list[str], dst: str = USLC) -> dict:
    """Observed before and after averages for real players who made this league move (pairs used by the model)."""
    P = eng.pairs
    base = P[(P["pair_type"] == "cross") & (P["src_league"] == src) & (P["dst_league"] == dst)
             & P[f"src_{metric}"].notna() & P[f"dst_{metric}"].notna()] if f"src_{metric}" in P else P.iloc[0:0]
    pos = base[base["position_group"].isin(groups)]
    use, scope = (pos, "same position") if len(pos) >= 10 else (base, "all positions")
    if use.empty:
        return {"n": 0, "n_all": len(base), "scope": scope}
    return {"n": len(use), "n_all": len(base), "scope": scope, "before": float(use[f"src_{metric}"].mean()),
            "after": float(use[f"dst_{metric}"].mean())}


BASE_BLOCKS = {"intercept", "slope", "position", "position_slope"}
LEAGUE_BLOCKS = {"league", "position_league", "pair"}
REL_LABEL = {"High": "Reliable", "Medium": "Fairly reliable", "Low": "Rough estimate", "None": "Not projected"}


def explain(eng: Engine, ev: Evaluation, metric: str) -> dict | None:
    """Exact breakdown of one projection on the model's own scale (log of per 90 plus a small constant, or logit
    for percentages). Contributions add up exactly on that scale; only the start and end values are converted to
    per 90. Drivers:

    * regression: what players with his rate, minutes and position usually produce the next season if they stay in
      his league (intercept, slope, position terms, plus his league's own level). The split between the intercept and
      league terms is not identified on its own under ridge, so they are reported together;
    * support: his xG or xA;
    * league_change: projection into the Championship minus the same projection staying in his own league. Zero
      for a Championship player. It comes from league effects estimated jointly from every move;
    * age, context, role.
    """
    t = ev.table.set_index("metric")
    if metric not in t.index or pd.isna(t.loc[metric].get("pred")):
        return None
    r = t.loc[metric]
    mm = eng.models[r["model_key"]]
    row = eng._row(ev.player, metric, USLC, ev_scenario(ev), None, mm)
    mu, var, X, t_src = mm.predict_t(row)
    beta = mm.beta
    blocks = np.array(mm.design.blocks)
    c = X[0] * beta
    stay = row.copy()
    stay["dst_league"] = stay["src_league"]
    X2 = mm.design.build(stay, t_src)
    c2 = X2[0] * beta
    mm.design.build(row, t_src)        # leave the design's column names as they were
    part = lambda arr, bl: float(arr[np.isin(blocks, list(bl))].sum())
    t0 = float(t_src[0])
    d = {"regression": part(c, BASE_BLOCKS) + part(c2, LEAGUE_BLOCKS) - t0,
         "support": part(c, {"support"}),
         "league_change": part(c, LEAGUE_BLOCKS) - part(c2, LEAGUE_BLOCKS),
         "age": part(c, {"age"}), "context": part(c, {"context"}), "role": part(c, {"role"})}
    total_t = float(mu[0]) - t0
    assert abs(sum(d.values()) - total_t) < 1e-8, "decomposition does not add up"
    inv = lambda v: float(mm.transform.inv(np.array([v]))[0])
    cur, pred = inv(t0), inv(float(mu[0]))
    kind = METRICS[metric].kind
    p = ev.player
    lab = METRICS[metric].label
    gross = sum(abs(v) for v in d.values()) or 1.0
    P = eng.pairs
    direct = P[(P["pair_type"] == "cross") & (P["src_league"] == p.league) & (P["dst_league"] == USLC)]
    direct_m = direct[direct[f"src_{metric}"].notna() & direct[f"dst_{metric}"].notna()] if f"src_{metric}" in direct else direct
    n_cross = int(((P["pair_type"] == "cross") & P[f"src_{metric}"].notna() & P[f"dst_{metric}"].notna()).sum()) \
        if f"src_{metric}" in P else 0
    n_same = int(((P["pair_type"] == "within") & (P["src_league"] == p.league)).sum())
    sup = [x for x in (r.get("model_key") or "").partition("+")[2].split(",") if x]
    drivers = []
    for k, v in d.items():
        if abs(v) < 1e-9:
            continue
        up = v > 0
        if k == "regression":
            name = "Regression toward typical output"
            text = (f"From {int(p.minutes):,} minutes, players whose {lab.lower()} sits this far from the usual level "
                    f"for a {POSITION_LABELS.get(p.position_group, p.position_group).lower()} kept only part of it "
                    f"the next season. Learned from {n_same:,} same league season pairs in {p.league} and every other "
                    "pair in the data.")
        elif k == "support":
            sv = ", ".join(f"{METRICS[x].label} {fmt(p.per90.get(x), METRICS[x].kind)} per 90" for x in sup if x in METRICS)
            name = f"Underlying numbers ({', '.join(METRICS[x].label.split(' (')[-1].rstrip(')') for x in sup)})"
            text = (f"{sv} points {'higher' if up else 'lower'}. In past moves this was a steadier guide to the next "
                    f"season than {lab.lower()} alone.")
        elif k == "league_change":
            name = f"Move from {p.league} to the Championship"
            if len(direct_m):
                text = (f"Estimated jointly from {n_cross:,} moves between leagues, of which {len(direct_m)} went from "
                        f"{p.league} to the Championship; shrunk toward no change where evidence is thin.")
            else:
                text = (f"No direct moves from {p.league} to the Championship in the data. This comes from league "
                        f"effects estimated across all {n_cross:,} moves between leagues, shrunk toward no change.")
        elif k == "age":
            age = (p.age + ev.years_ahead) if p.age is not None else None
            name = "Age"
            text = (f"Age {age:.0f} in the target season; the learned age effect is small and imprecise."
                    if age is not None else "Age unknown.")
        elif k == "context":
            name, text = "Team context", "Team attacking context."
        else:
            name, text = "Role", "Starting role."
        drivers.append({"key": k, "name": name, "t": v, "share": abs(v) / gross, "up": up, "text": text})
    drivers.sort(key=lambda x: -abs(x["t"]))
    # running per 90 values for the technical view (exact at each point, increments depend on the order)
    run, acc = [], t0
    for k in ("regression", "support", "league_change", "age", "context", "role"):
        if abs(d[k]) < 1e-9 and k not in ("regression",):
            continue
        acc += d[k]
        run.append({"key": k, "t": d[k], "value": inv(acc)})
    return {"metric": metric, "label": lab, "kind": kind, "current": cur, "pred": pred, "lo80": float(r["lo80"]),
            "hi80": float(r["hi80"]), "total": pred - cur, "total_pct": (pred / cur - 1) if cur else np.nan,
            "total_t": total_t, "drivers": drivers, "running": run, "same_league": p.league == USLC,
            "n_direct": len(direct_m), "confidence": r["confidence"], "status": r["status"],
            "hist_mae": r.get("hist_mae"), "hist_n": r.get("hist_n"), "hist_scope": r.get("hist_scope"),
            "hist_carry_mae": r.get("hist_carry_mae"), "transform": mm.transform.__class__.__name__,
            "why": r.get("why", "")}


def size_word(share: float) -> str:
    return "main factor" if share >= 0.5 else "large factor" if share >= 0.3 else "smaller factor" if share >= 0.12 \
        else "minor factor"


def minutes_curve(eng: Engine, ev: Evaluation, metric: str, grid) -> pd.DataFrame | None:
    """Season totals across a grid of minutes. The per 90 projection does not depend on minutes; the range does
    (fewer minutes, noisier per 90), so the range is recomputed at every point with the same model."""
    t = ev.table.set_index("metric")
    if metric not in t.index or pd.isna(t.loc[metric].get("pred")) or METRICS[metric].kind != "rate":
        return None
    mm = eng.models[t.loc[metric, "model_key"]]
    row = eng._row(ev.player, metric, USLC, ev_scenario(ev), None, mm)
    grid = np.asarray(grid, float)
    df = pd.concat([row] * len(grid), ignore_index=True)
    df["dst_minutes"] = grid
    pr = mm.predict(df, levels=(0.8,))
    return pd.DataFrame({"minutes": grid, "pred": pr["pred"] * grid / 90, "lo80": pr["lo80"] * grid / 90,
                         "hi80": pr["hi80"] * grid / 90})


def summarize(eng: Engine, ev: Evaluation) -> dict:
    """Strengths, concerns, benchmarks and video priorities, each tied to a number on the page."""
    t = ev.table
    p = ev.player
    ok = t[t["status"].isin(["validated", "unvalidated"]) & t["pred"].notna()] if "pred" in t else t.iloc[0:0]
    trusted = ok[ok["confidence"].isin(["High", "Medium"])]
    strengths, concerns, video = [], [], []
    pos_lab = ev.role.lower()
    for r in trusted.itertuples():
        if r.pctl_uslc is not None and r.pctl_uslc >= 70:
            sure = r.pctl_uslc_lo is not None and r.pctl_uslc_lo >= 50
            strengths.append(f"{r.Metric}: projects at {fmt(r.pred, r.kind)} per 90, {ordinal(r.pctl_uslc)} percentile "
                             f"among Championship {pos_lab}s" + (" (above the median even at the low end of the range)."
                                                                  if sure else "."))
        elif r.pctl_uslc is not None and r.pctl_uslc <= 30:
            concerns.append(f"{r.Metric}: projects at {fmt(r.pred, r.kind)} per 90, {ordinal(r.pctl_uslc)} percentile "
                            f"among Championship {pos_lab}s.")
    low = ok[ok["confidence"] == "Low"]["Metric"].tolist()
    if low:
        concerns.append("Low confidence projections, treat as rough: " + ", ".join(low) + ".")
    na = t[t["status"].isin(["insufficient", "not available"])]["Metric"].tolist()
    if na:
        concerns.append("Not projected: " + ", ".join(na) + ".")
    if p.minutes < 900:
        concerns.append(f"Small sample: {int(p.minutes):,} minutes in {p.league} {p.season}. Ranges are wide for a reason.")
    # finishing versus chance quality (source season, observed)
    g, xg = p.per90.get("goals"), p.per90.get("xg")
    if g is not None and xg is not None and p.minutes >= 450:
        gt, xt = g * p.minutes / 90, xg * p.minutes / 90
        if gt - xt >= 3:
            video.append(f"Finishing: scored {gt:.0f} goals from {xt:.1f} xG in {p.season}. Check whether the shot quality "
                         "and finishing repeat, because the model expects most of that gap to close.")
        elif xt - gt >= 3:
            video.append(f"Finishing: {gt:.0f} goals from {xt:.1f} xG in {p.season}. Review the misses: chance quality is "
                         "there, conversion was not.")
    if ev.role in ("Center forward", "Center back"):
        aw, ap = p.per90.get("aerials_won"), p.per90.get("aerial_win_pct")
        if aw is not None and ap is not None:
            video.append(f"Aerial duels: {aw:.2f} won per 90 at {ap:.0f}% success. Check timing and the type of service "
                         "he competes against.")
    if ev.role in ("Winger", "Center forward", "Attacking midfielder"):
        d = p.per90.get("succ_dribbles")
        if d is not None:
            video.append(f"1v1 play: {d:.2f} successful dribbles per 90. Check where on the pitch they happen and the "
                         "level of the defenders beaten.")
    if ev.role in ("Fullback", "Center back", "Central or defensive midfielder"):
        dp = p.per90.get("def_duel_win_pct")
        if dp is not None:
            video.append(f"Defending: {dp:.0f}% of defensive duels won. Check body shape, recovery runs and decisions "
                         "when isolated.")
    video.append(f"Role and minutes: {int(p.minutes):,} minutes"
                 + (f", {int(p.starts)} starts" if p.starts else "") + f" for {p.team or 'his club'}. Confirm the role he "
                 "played matches the one he would have at RIFC; the projection assumes similar usage.")
    if p.league != USLC:
        video.append(f"Level of opposition: the projection is built from {p.league} data. Check how his actions hold up "
                     "against the stronger opponents in that league.")
    # confidence overall
    conf = t["confidence"].value_counts().to_dict() if "confidence" in t else {}
    n_ok = int(len(ok))
    headline_conf = ("High" if conf.get("High", 0) >= max(1, n_ok // 2) else
                     "Medium" if conf.get("High", 0) + conf.get("Medium", 0) >= max(1, n_ok // 2) else "Low")
    return {"strengths": strengths, "concerns": concerns, "video": video, "confidence_counts": conf,
            "overall_confidence": headline_conf}


def rifc_team_context(season: str | None = None, phase: str = "combined") -> dict:
    """StatsBomb RIFC team strengths and weaknesses relative to the league (rank in the top or bottom fifth).
    Team context only: says nothing about which positions need a signing."""
    stats = T.load()
    if stats.empty:
        return {}
    season = season or sorted(stats["season"].unique())[-1]
    bt = T.benchmark_table(stats, season, phase)
    r = bt[(bt["team_key"] == "rhode island") & bt["higher_is"].isin(["high", "low"])]
    if r.empty:
        return {}
    skip = {"Goal Difference", "Non Penalty Goal Difference", "xG Difference w/Pen", "Non Penalty xG Difference",
            "Own Goals", "Own Goals Conceded", "Goalkeeper OBV", "Goalkeeper OBV Conceded", "Shot OBV", "Shot OBV Faced",
            "Penalties", "Penalty Goals", "Penalties Faced", "Penalty Goals Conceded"}
    r = r[~r["metric"].isin(skip)]
    n = int(r["n_teams"].iloc[0])
    cut = max(1, round(n / 5))

    def line(x):
        u = " per match" if x["kind"] == "total" else ("%" if x["kind"] == "pct" else "")
        d = 3 if abs(x["league_mean"]) < 0.1 else (1 if x["kind"] == "pct" else 2)
        return (f"{x['metric']}: {x['comparable']:.{d}f}{u} vs league {x['league_mean']:.{d}f}{u}, "
                f"{ordinal(x['rank'])} of {n}")
    top = r[r["rank"] <= cut].sort_values(["rank", "metric"]).head(6)
    bot = r[r["rank"] > n / 2].sort_values(["rank", "metric"], ascending=[False, True]).head(6)
    return {"season": season, "n": n, "strengths": [line(x) for _, x in top.iterrows()],
            "weaknesses": [line(x) for _, x in bot.iterrows()]}


def contribution_line(ev: Evaluation) -> str:
    t = ev.table.set_index("metric")
    bits = []
    for m in ("goals", "xg", "assists", "goal_contributions", "prog_passes", "interceptions", "aerials_won"):
        if m in t.index and pd.notna(t.loc[m].get("pred_season")) and m in ROLES[ev.role]["metrics"]:
            r = t.loc[m]
            name = {"goals": "goals", "xg": "xG", "assists": "assists", "goal_contributions": "goal contributions",
                    "prog_passes": "progressive passes", "interceptions": "interceptions",
                    "aerials_won": "aerial duels won"}[m]
            bits.append(f"{r['pred_season']:.1f} {name} (80% range {r['lo80_season']:.1f} to {r['hi80_season']:.1f})")
        if len(bits) == 3:
            break
    if not bits:
        return ""
    return f"At {int(ev.minutes):,} Championship minutes: " + "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# RIFC comparisons
# ---------------------------------------------------------------------------
def rifc_players(eng: Engine, groups: list[str] | None = None, season: str | None = None, min_minutes=450) -> pd.DataFrame:
    a = eng.agg[eng.agg["team"].map(is_rifc) & (eng.agg["league"] == USLC)]
    season = season or (a.sort_values("season_start")["season"].iloc[-1] if not a.empty else None)
    a = a[(a["season"] == season) & (a["minutes"] >= min_minutes)]
    if groups:
        a = a[a["position_group"].isin(groups)]
    a = a.copy()
    a["key"] = a.apply(player_key, axis=1)
    return a.sort_values("minutes", ascending=False)


def compare(eng: Engine, ev: Evaluation, rifc: pd.DataFrame) -> pd.DataFrame:
    """Candidate projection (projected) against RIFC players' own Championship numbers (observed)."""
    t = ev.table.set_index("metric")
    groups = role_groups(ev.role)
    rows = []
    for m in t.index:
        c = t.loc[m]
        if pd.isna(c.get("pred")):
            continue
        dist = positional_dist(eng, m, groups)
        for r in rifc.itertuples():
            obs = getattr(r, f"{m}_p90")
            if pd.isna(obs):
                continue
            if c["lo80"] > obs:
                verdict = "Higher (whole 80% range above)"
            elif c["hi80"] < obs:
                verdict = "Lower (whole 80% range below)"
            elif c["pred"] > obs:
                verdict = "Similar, leaning higher"
            elif c["pred"] < obs:
                verdict = "Similar, leaning lower"
            else:
                verdict = "Similar"
            rows.append({"metric": m, "Metric": c["Metric"], "kind": c["kind"], "rifc_player": r.player_name,
                         "rifc_minutes": r.minutes, "rifc_obs": obs, "rifc_pctl": eng.percentile(dist, obs),
                         "cand_pred": c["pred"], "cand_lo80": c["lo80"], "cand_hi80": c["hi80"],
                         "cand_pctl": c.get("pctl_uslc"), "diff": c["pred"] - obs, "verdict": verdict,
                         "confidence": c["confidence"]})
    return pd.DataFrame(rows)


def comparison_summary(cmp: pd.DataFrame, ev: Evaluation) -> list[str]:
    """Per RIFC player: where the candidate's whole range clears him, and where it falls short. No overall verdict."""
    out = []
    if cmp.empty:
        return out
    for name, g in cmp.groupby("rifc_player", sort=False):
        hi = g[g["verdict"].str.startswith("Higher")]["Metric"].tolist()
        lo = g[g["verdict"].str.startswith("Lower")]["Metric"].tolist()
        sim = len(g) - len(hi) - len(lo)
        s = f"Against {name} ({int(g['rifc_minutes'].iloc[0]):,} min): "
        s += ("clearly higher on " + ", ".join(hi) if hi else "not clearly higher on any metric")
        s += ("; clearly lower on " + ", ".join(lo) if lo else "")
        s += f"; {sim} of {len(g)} within the projection range (no clear difference)."
        out.append(s)
    return out


def striker_view(cmp: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for q, ms in STRIKER_QUESTIONS:
        g = cmp[cmp["metric"].isin(ms)]
        for name, h in g.groupby("rifc_player", sort=False):
            hi = (h["verdict"].str.startswith("Higher")).sum()
            lo = (h["verdict"].str.startswith("Lower")).sum()
            rows.append({"question": q, "rifc_player": name, "metrics": ", ".join(h["Metric"]),
                         "answer": "Yes, clearly" if hi and not lo else "No, clearly less" if lo and not hi else
                         "Mixed" if hi and lo else "Not clearly different"})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Shortlist (stored locally in the engine database)
# ---------------------------------------------------------------------------
SL_SQL = """CREATE TABLE IF NOT EXISTS shortlist (key VARCHAR PRIMARY KEY, role VARCHAR, note VARCHAR, added_at TIMESTAMP)"""


def shortlist(path=None) -> pd.DataFrame:
    with db.connect(path) as con:
        con.execute(SL_SQL)
        return con.execute("SELECT * FROM shortlist ORDER BY added_at").df()


def shortlist_migrate(idn, path=None) -> int:
    """Old entries keyed by one season (name|club|league|season) become one entry per player ID."""
    sl = shortlist(path)
    if sl.empty:
        return 0
    m = dict(zip(idn.records["key"], idn.records["uid"]))
    n = 0
    with db.connect(path) as con:
        con.execute(SL_SQL)
        for r in sl.itertuples():
            if "|" in str(r.key) and r.key in m:
                uid = m[r.key]
                con.execute("DELETE FROM shortlist WHERE key = ?", [r.key])
                if not con.execute("SELECT 1 FROM shortlist WHERE key = ?", [uid]).fetchall():
                    con.execute("INSERT INTO shortlist VALUES (?,?,?,?)", [uid, r.role, r.note, r.added_at])
                n += 1
    return n


def shortlist_add(key: str, role: str, note: str = "", path=None):
    """key: the player ID (one entry per player)."""
    from datetime import datetime
    with db.connect(path) as con:
        con.execute(SL_SQL)
        con.execute("DELETE FROM shortlist WHERE key = ?", [key])
        con.execute("INSERT INTO shortlist VALUES (?,?,?,?)", [key, role, note, datetime.now()])


def shortlist_remove(key: str, path=None):
    with db.connect(path) as con:
        con.execute(SL_SQL)
        con.execute("DELETE FROM shortlist WHERE key = ?", [key])
