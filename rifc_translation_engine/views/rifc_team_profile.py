from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import teamcontext as TC
from engine import teamprofile as TP
from engine import teamstats as T
from ui import GOLD, NAVY, card, df_show, setup

setup("RIFC Team Profile")
st.title("Rhode Island FC team profile")
st.caption("StatsBomb team data against the rest of the USL Championship, one verified season at a time.")

stats = T.load()
if stats.empty:
    st.info("No StatsBomb team data imported yet. Use Import team data (Admin).")
    st.stop()
b = T.batches()
seasons = sorted(stats["season"].unique(), reverse=True)
c1, c2 = st.columns([1, 3])
season = c1.selectbox("Season", seasons)
phase = b[(b["season"] == season) & b["active"]]["phase"].iloc[0]
A = TP.answers(stats, season, phase)
if not A:
    st.warning("Rhode Island is not in this season's team data.")
    st.stop()
note = b[(b["season"] == season)]["notes"].iloc[0] or ""
c2.caption(f"Scope: {A['phase']}, {int(A['matches'])} RIFC matches, {A['n_teams']} clubs. Per match values divide "
           "season totals by each club's own matches. Ranks compare clubs within this season only. " +
           ("Playoffs in progress at export." if "in progress" in note else ""))

st.markdown("### The questions")
card("Do we generate more or fewer expected goals than the league?", A["xg"])
card("What type of chance creation contributes most?", [A["creation_main"], A["creation"]])
card("How often do we work the ball into the penalty area?", A["box"])
card("Open play versus set pieces", A["open_vs_set"])
card("Pressing and defensive activity", A["pressing"])

st.markdown("### Where RIFC sits in the league")
prof = st.selectbox("Profile", list(T.PROFILES))
bt = A["bench"]
r = bt[(bt["team_key"] == TP.RIFC_KEY) & bt["metric"].isin(T.PROFILES[prof])].copy()
r["order"] = r["metric"].map({m: i for i, m in enumerate(T.PROFILES[prof])})
r = r.sort_values("order")
fig = go.Figure(go.Bar(x=r["percentile"], y=r["metric"], orientation="h", marker_color=GOLD,
                       text=[f"{v:.2f}" for v in r["comparable"]], textposition="outside",
                       customdata=np.stack([r["league_mean"], r["rank"], r["n_teams"]], axis=1),
                       hovertemplate="%{y}: %{text} (league %{customdata[0]:.2f}), rank %{customdata[1]:.0f} of "
                                     "%{customdata[2]:.0f}<extra></extra>"))
fig.add_vline(x=50, line=dict(color="grey", dash="dot"))
fig.update_layout(height=80 + 34 * len(r), margin=dict(l=10, r=40, t=10, b=10), xaxis=dict(range=[0, 110],
                  title="Percentile within the league (higher = better where 'better' is defined; style metrics: higher value)"),
                  yaxis=dict(autorange="reversed"))
st.plotly_chart(fig, width="stretch")
tab = r[["metric", "comparable", "league_mean", "diff_pct", "rank", "n_teams", "kind"]].rename(columns={
    "metric": "Metric", "comparable": "RIFC", "league_mean": "League mean", "diff_pct": "vs league %", "rank": "Rank",
    "n_teams": "Clubs", "kind": "Unit"})
tab["Unit"] = tab["Unit"].map({"total": "per match", "derived": "per match (StatsBomb OBV)", "pct": "%", "ratio": "ratio",
                               "average": "average", "share": "share"})
df_show(tab.round(2))
with st.expander("Definitions"):
    for m in T.PROFILES[prof]:
        meta = T.REGISTRY.get(m)
        if meta:
            st.markdown(f"**{m}** ({meta.kind}): {meta.definition}")
    st.caption("OBV is StatsBomb's own model; Wyscout player numbers have no direct equivalent.")

st.markdown("### Changes across seasons")
tr = TP.trend(stats)
card("Biggest moves against the league, last two seasons", TP.changes(tr))
piv = tr.assign(cell=[f"{v:.2f} ({r}/{n})" for v, r, n in zip(tr["value"], tr["rank"], tr["n"])]) \
    .pivot(index="metric", columns="season", values="cell").reindex(TP.KEY_TREND)
df_show(piv.reset_index().rename(columns={"metric": "Metric (value, rank/clubs)"}))
st.caption("Each season is ranked within its own season. Values are per match for counts. 2024 and 2025 include "
           "playoff matches; 2026 includes playoff matches played by the export date.")

st.markdown("### What this means for signings")
saved = TC.load_saved()
lines = []
if saved:
    eff = saved.get("effects", {})
    lines.append(f"Out of sample test (train 2025 moves, test 2026 moves, {saved['n_tested']} metrics): team context "
                 f"improved projections only for {', '.join(eff) or 'no metric'}. That adjustment is applied in the "
                 "RIFC adjusted column and labelled provisional.")
    w = stats[(stats["season"] == season) & (stats["metric"] == "Passes")]
    if not w.empty:
        x = w.set_index("team_key")["per_match"]
        if TP.RIFC_KEY in x:
            lines.append(f"RIFC attempted {x[TP.RIFC_KEY]:.0f} passes per match in {season} vs a league mean of "
                         f"{x.mean():.0f} ({x[TP.RIFC_KEY] / x.mean() - 1:+.0%}). In testing, players joining higher "
                         "passing clubs completed a slightly higher share of their passes.")
lines.append("For goals, xG, chance creation, carrying and defending, team context did not improve accuracy on past moves, "
             "so player projections stay neutral. RIFC's attacking numbers above are context for the coaching staff, "
             "not evidence of any candidate's ability.")
card("Evidence based", lines)
card("Needs video", ["How chances after high regains are created: who wins the ball, where, and who receives next.",
                     "Set piece routines and delivery: the totals show output, not design or repeatability.",
                     "Pressing triggers and shape: PPDA and aggression show intensity, not the system.",
                     "Role fit: which positions get the box touches and shots in our structure."])
