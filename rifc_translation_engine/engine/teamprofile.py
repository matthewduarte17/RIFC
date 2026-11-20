"""Rhode Island FC team profile from StatsBomb team data: answers with numbers, no tactical guessing."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import teamstats as T

RIFC_KEY = "rhode island"


def _v(bt, metric, team_key=RIFC_KEY):
    r = bt[(bt["metric"] == metric) & (bt["team_key"] == team_key)]
    return None if r.empty else r.iloc[0]


def _line(r, unit="per match", d=2, pct=False):
    if r is None:
        return "not in the data"
    val = f"{r['comparable']:.{d}f}" + ("%" if pct else "")
    mean = f"{r['league_mean']:.{d}f}" + ("%" if pct else "")
    return (f"{val} {unit} vs league mean {mean} ({r['diff_pct']:+.0f}%), "
            f"{_ord(r['rank'])} of {int(r['n_teams'])}")


def _ord(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def share(stats, season, phase, num, den, team_key=RIFC_KEY):
    s = stats[(stats["season"] == season) & (stats["phase"] == phase)]
    w = s.pivot_table(index="team_key", columns="metric", values="value")
    if num not in w or den not in w:
        return None, None, None
    x = w[num] / w[den]
    return float(x.get(team_key, np.nan)), float(x.mean()), int((x > x.get(team_key, np.nan)).sum() + 1)


def answers(stats: pd.DataFrame, season: str, phase: str = "combined") -> dict:
    bt = T.benchmark_table(stats, season, phase)
    if bt.empty or bt[bt["team_key"] == RIFC_KEY].empty:
        return {}
    n = int(bt["n_teams"].iloc[0])
    g = _v(bt, "Goals")
    out = {"season": season, "phase": T.PHASES[phase], "n_teams": n, "matches": float(g["matches"]) if g is not None else None}
    npxg, gl = _v(bt, "Non Penalty xG"), g
    out["xg"] = (f"Non penalty xG: {_line(npxg)}. Goals: {_line(gl)}. "
                 + ("More expected goals than the league average." if npxg["diff"] > 0 else
                    "Fewer expected goals than the league average."))
    op, op_mean, op_rank = share(stats, season, phase, "Open Play xG", "Non Penalty xG")
    sp, sp_mean, sp_rank = share(stats, season, phase, "Set Piece xG", "Non Penalty xG")
    ca, hp = _v(bt, "Counter Attack Shots"), _v(bt, "High Press Shots")
    ca_sh, ca_mean, _ = share(stats, season, phase, "Counter Attack Shots", "Non Penalty Shots")
    hp_sh, hp_mean, _ = share(stats, season, phase, "High Press Shots", "Non Penalty Shots")
    out["creation"] = (f"Open play produces {op:.0%} of non penalty xG (league {op_mean:.0%}); set pieces "
                       f"{sp:.0%} (league {sp_mean:.0%}). High press shots: {_line(hp, d=1)}, {hp_sh:.0%} of shots "
                       f"(league {hp_mean:.0%}). Counter attack shots: {_line(ca, d=1)}, {ca_sh:.0%} of shots "
                       f"(league {ca_mean:.0%}).")
    gap = (sp - sp_mean) * 100
    out["creation_main"] = ("Open play is the main source of xG, as for every club. "
                            + ("The open play and set piece mix is close to the league average."
                               if abs(gap) < 3 else
                               f"Set pieces carry a {'larger' if gap > 0 else 'smaller'} share than the league average "
                               f"({gap:+.0f} points)."))
    pib, dc, bc = _v(bt, "Passes Inside Box"), _v(bt, "Deep Completions"), _v(bt, "Box Crosses")
    out["box"] = (f"Completed passes inside the box: {_line(pib, d=1)}. Deep completions: {_line(dc, d=1)}. Crosses into "
                  f"the box: {_line(bc, d=1)}. These measure how often the ball is worked into the box; StatsBomb's team "
                  "export has no direct count of box entries.")
    out["open_vs_set"] = (f"Open play xG {_line(_v(bt, 'Open Play xG'))}; set piece xG {_line(_v(bt, 'Set Piece xG'))}; "
                          f"set piece goals {_line(_v(bt, 'Set Piece Goals'))}.")
    ppda, dd, ag = _v(bt, "PPDA"), _v(bt, "Defensive Distance"), _v(bt, "Aggression")
    aa, dr = _v(bt, "Aggressive Actions"), _v(bt, "Defensive Regains")
    out["pressing"] = (f"PPDA {ppda['comparable']:.1f} vs league {ppda['league_mean']:.1f} "
                       f"({'fewer' if ppda['diff'] < 0 else 'more'} opponent passes per defensive action, so "
                       f"{'more' if ppda['diff'] < 0 else 'less'} intense pressure than average; "
                       f"{_ord((bt[bt['metric'] == 'PPDA']['comparable'] < ppda['comparable']).sum() + 1)} lowest of {n}). "
                       f"Defensive distance {dd['comparable']:.1f} vs {dd['league_mean']:.1f} "
                       f"({'about average' if abs(dd['diff_pct']) < 2 else ('higher than average' if dd['diff'] > 0 else 'deeper than average')}). Aggression "
                       f"{ag['comparable']:.0%} vs {ag['league_mean']:.0%}. Aggressive actions {_line(aa, d=0)}. "
                       f"Defensive regains {_line(dr, d=0)}.")
    out["table"] = bt[bt["team_key"] == RIFC_KEY]
    out["bench"] = bt
    return out


KEY_TREND = ["Goals", "Non Penalty xG", "Open Play xG", "Set Piece xG", "Non Penalty Shots", "Non Penalty xG/Shot",
             "Passes Inside Box", "Deep Completions", "High Press Shots", "Counter Attack Shots", "Goals Conceded",
             "Non Penalty xG Faced", "PPDA", "Defensive Distance", "Aggression", "Passes", "Passing%", "OBV"]


def trend(stats: pd.DataFrame, phase: str = "combined", team_key: str = RIFC_KEY) -> pd.DataFrame:
    """Each season ranked within its own season (never pooled)."""
    rows = []
    for season in sorted(stats[stats["phase"] == phase]["season"].unique()):
        bt = T.benchmark_table(stats, season, phase)
        for m in KEY_TREND:
            r = _v(bt, m, team_key)
            if r is None:
                continue
            rows.append({"metric": m, "season": season, "value": r["comparable"], "league_mean": r["league_mean"],
                         "rank": int(r["rank"]), "n": int(r["n_teams"]), "pct_vs_league": r["diff_pct"]})
    return pd.DataFrame(rows)


def changes(tr: pd.DataFrame) -> list[str]:
    """Largest moves in league relative position between the last two seasons."""
    if tr.empty or tr["season"].nunique() < 2:
        return []
    s = sorted(tr["season"].unique())
    a, b = tr[tr["season"] == s[-2]].set_index("metric"), tr[tr["season"] == s[-1]].set_index("metric")
    j = a.join(b, lsuffix="_a", rsuffix="_b", how="inner")
    j["move"] = j["pct_vs_league_b"] - j["pct_vs_league_a"]
    out = []
    for m, r in j.reindex(j["move"].abs().sort_values(ascending=False).index).head(5).iterrows():
        meta = T.REGISTRY.get(m)
        per = " per match" if meta and meta.kind in T.ADDITIVE else ""
        out.append(f"{m}{per}: {r['value_a']:.2f} ({r['pct_vs_league_a']:+.0f}% vs league, {_ord(r['rank_a'])}) in {s[-2]} "
                   f"to {r['value_b']:.2f} ({r['pct_vs_league_b']:+.0f}%, {_ord(r['rank_b'])}) in {s[-1]}.")
    return out
