from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import teamstats as T
from ui import df_show, setup

setup("Championship Team Benchmarks")
st.title("Championship team benchmarks")
st.caption("Every club's StatsBomb profile, per match where the stat is a count, ranked within one verified season.")

stats = T.load()
if stats.empty:
    st.info("No StatsBomb team data imported yet.")
    st.stop()
b = T.batches()
c1, c2, c3 = st.columns([1, 1.4, 1.4])
season = c1.selectbox("Season", sorted(stats["season"].unique(), reverse=True))
phase = b[(b["season"] == season) & b["active"]]["phase"].iloc[0]
prof = c2.selectbox("Profile", list(T.PROFILES) + ["All metrics"])
bt = T.benchmark_table(stats, season, phase)
metrics = sorted(bt["metric"].unique()) if prof == "All metrics" else T.PROFILES[prof]
sort_m = c3.selectbox("Sort by", metrics)
w = bt[bt["metric"].isin(metrics)].pivot_table(index="team", columns="metric", values="comparable")[metrics]
rk = bt[bt["metric"] == sort_m].set_index("team")["rank"]
w = w.join(rk.rename(f"Rank: {sort_m}")).sort_values(f"Rank: {sort_m}")
st.caption(f"Scope: {T.PHASES[phase]}. Higher rank number = worse where a direction is defined (e.g. goals conceded); "
           "style metrics like PPDA or passes are ranked high to low with no judgement.")


def hl(row):
    return ["background-color: rgba(227,178,60,.28)" if "rhode island" in row.name.lower() else "" for _ in row]


st.dataframe(w.style.apply(hl, axis=1).format("{:.2f}", subset=metrics).format("{:.0f}", subset=[f"Rank: {sort_m}"]),
             width="stretch", height=min(900, 40 + 35 * len(w)))
mean = bt[bt["metric"].isin(metrics)].groupby("metric")["league_mean"].first().reindex(metrics)
df_show(pd.DataFrame({"Metric": metrics, "League mean (clubs)": mean.values.round(3)}))
with st.expander("Definitions and units"):
    for m in metrics:
        meta = T.REGISTRY.get(m)
        if meta:
            unit = {"total": "per match", "derived": "per match, StatsBomb OBV", "pct": "%", "ratio": "ratio",
                    "average": "average", "share": "0 to 1 share"}[meta.kind]
            st.markdown(f"**{m}** ({unit}): {meta.definition}")
la = T.db.query("SELECT a.* FROM team_stat_league_avg a JOIN team_stat_batches b USING (batch_id) WHERE b.season = ? "
                "AND b.active", [season])
with st.expander("Provider League Average row (verification only)"):
    df_show(la[la["metric"].isin(metrics)][["metric", "reported", "mean_of_teams", "median_of_teams", "note"]].round(3))
