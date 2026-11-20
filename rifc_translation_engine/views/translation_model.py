from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from engine.api import Engine
from engine.config import ModelSettings
from engine.model import REF_AGE, variant_label
from engine.schema import METRICS
from engine.transfers import coverage_table, metric_pairs
from ui import NAVY, df_show, get_engine, setup, stale_warning

setup("Translation Model")
st.title("Translation model")
st.caption("One model per metric, fitted on paired seasons of players who moved. League effects are learned "
           "from those moves and pulled toward 'no adjustment' when evidence is thin. No league factors are set by hand.")

eng = get_engine()
stale_warning(eng)
base = eng.settings if eng is not None else ModelSettings()

with st.expander("Settings", expanded=eng is None):
    c1, c2, c3 = st.columns(3)
    s = ModelSettings(
        min_minutes_each_side=c1.number_input("Minimum minutes each season", 90, 2000, base.min_minutes_each_side, 90),
        max_gap_years=c1.number_input("Max gap between seasons (years)", 0.3, 2.0, base.max_gap_years, 0.1),
        include_within_league_pairs=c1.checkbox("Use same league season to season pairs (aging, regression to the mean)",
                                                base.include_within_league_pairs),
        allow_cross_provider_for_stable_metrics=c1.checkbox("Allow mixed providers for goals, assists, shots",
                                                            base.allow_cross_provider_for_stable_metrics),
        min_cross_league_pairs=c2.number_input("Min cross league pairs per metric", 5, 500, base.min_cross_league_pairs),
        min_league_pairs=c2.number_input("Min cross league pairs touching a league", 3, 200, base.min_league_pairs),
        min_test_pairs_for_validation=c2.number_input("Min backtest pairs to validate", 5, 500,
                                                      base.min_test_pairs_for_validation),
        coverage_tolerance=c2.number_input("80% interval coverage tolerance", 0.02, 0.3, base.coverage_tolerance, 0.01),
        alpha_league=c3.number_input("Prior strength: league effects", 0.1, 100.0, base.alpha_league),
        alpha_position_league=c3.number_input("Prior strength: position x league", 0.1, 500.0, base.alpha_position_league),
        alpha_pair=c3.number_input("Prior strength: league pair deviation", 0.1, 500.0, base.alpha_pair),
        auto_tune=c3.checkbox("Tune overall prior strength with time based CV", base.auto_tune),
        benchmark_min_minutes=c3.number_input("Benchmark minimum minutes", 90, 3000, base.benchmark_min_minutes, 90),
    )
    st.caption("Higher prior strength = more pooling toward no league adjustment. Every run stores its settings.")

if st.button("Build pairs and fit model", type="primary"):
    with st.spinner("Linking players, building pairs, fitting models"):
        eng = Engine.fit(s)
        st.session_state["engine"] = eng
    st.success(f"Model run #{eng.run_id} saved. Run the backtest next.")

if eng is None:
    st.stop()

pairs = eng.pairs
if pairs.empty:
    have = eng.agg.groupby("league")["season"].apply(lambda s: ", ".join(sorted(s.unique()))).reset_index()
    st.error("No paired seasons found, so no translation model can be fitted and nothing will be projected. "
             "A pair needs the same player in one season and again in the next season. Below is what is "
             "imported; add the previous seasons of these leagues (for example 2023 to 2025).")
    df_show(have.rename(columns={"league": "League", "season": "Seasons imported"}))
    multi = eng.agg.groupby("pid")["league"].nunique()
    if (multi > 1).any():
        st.caption(f"{int((multi > 1).sum())} players appear in two leagues within the same season (mid season "
                   "moves or loans). The order of those spells is not in the data, so they are not used as pairs.")
    st.info("Benchmarks and percentiles on the Player Projection page already work with this data.")
    st.stop()

cross = pairs[pairs["pair_type"] == "cross"]
a, b, c, d = st.columns(4)
a.metric("Players linked", f"{eng.agg['pid'].nunique():,}")
b.metric("Cross league pairs", f"{len(cross):,}")
c.metric("Same league pairs", f"{(pairs['pair_type'] == 'within').sum():,}")
d.metric("Leagues", len(eng.leagues()))

st.subheader("Readiness by metric")
summ = eng.summary_table()
summ["status"] = np.where(~summ["fitted"], "No model",
                          np.where(summ["enough data"], "Fitted", "Fitted, below minimum pairs"))
df_show(summ[["label", "group", "pairs total", "cross league pairs", "status", "slope", "tuned by time CV",
              "LOO 80% coverage"]].round(3))
st.caption("Slope below 1 means part of a player's current per 90 is noise and gets regressed toward the "
           "positional norm. LOO coverage: share of left out pairs inside the 80% interval (target 0.80).")

st.subheader("Where the moves are")
cov = coverage_table(pairs)
mat = cov.pivot(index="src_league", columns="dst_league", values="pairs").fillna(0)
fig = px.imshow(mat, text_auto=True, color_continuous_scale="Blues", aspect="auto",
                labels={"x": "To", "y": "From", "color": "Pairs"})
fig.update_layout(height=120 + 32 * len(mat), margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, width="stretch")
pos = cross["position_group"].value_counts().rename_axis("position").reset_index(name="cross league pairs")
df_show(pos)

st.subheader("Metric detail")
fitted = list(eng.models)
if not fitted:
    st.stop()
m = st.selectbox("Metric", fitted, format_func=variant_label)
mm = eng.models[m]
le = mm.league_effects()
scale = "log" if mm.transform.kind == "rate" else "logit"
st.markdown(f"**League effects** ({scale} scale, error bars = 1 standard error). Destination effect: how the "
            "league shifts output once a player plays there. Source effect: how output in that league is "
            "discounted when read as evidence. Leagues with few moves stay near zero.")
fig = go.Figure()
fig.add_bar(name="Destination", x=le["league"], y=le["destination effect"],
            error_y=dict(type="data", array=le["dest se"]), marker_color=NAVY)
fig.add_bar(name="Source", x=le["league"], y=le["source effect"],
            error_y=dict(type="data", array=le["source se"]), marker_color="#8DA9C4")
fig.update_layout(barmode="group", height=360, margin=dict(l=10, r=10, t=10, b=10))
st.plotly_chart(fig, width="stretch")
df_show(le.round(3))

ages = np.arange(18, 37)
coefs = mm.coefficients().set_index("term")["coef"]
x = (ages - REF_AGE) / 4
curve = coefs.get("age", 0) * x + coefs.get("age^2", 0) * x ** 2
if mm.transform.kind == "rate":
    curve = (np.exp(curve) - 1) * 100
    ylab = "% change vs age 26"
else:
    ylab = "change (logit)"
fig = px.line(x=ages, y=curve, labels={"x": "Age in destination season", "y": ylab})
fig.update_traces(line_color=NAVY)
fig.update_layout(height=280, margin=dict(l=10, r=10, t=30, b=10), title="Learned age effect")
st.plotly_chart(fig, width="stretch")

with st.expander("All coefficients"):
    df_show(mm.coefficients().round(4))
with st.expander("Pairs used for this metric"):
    b = m.partition("+")[0]
    p = metric_pairs(pairs, b, eng.settings)
    df_show(p[["player_name", "position_group", "src_league", "src_season", "src_minutes", f"src_{b}",
               "dst_league", "dst_season", "dst_minutes", f"dst_{b}", "pair_type"]].round(3))
