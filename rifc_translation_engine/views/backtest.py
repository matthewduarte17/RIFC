from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from engine.backtest import summarize
from engine.model import variant_label
from engine.schema import METRICS
from ui import NAVY, df_show, get_engine, setup, stale_warning

setup("Backtest")
st.title("Historical validation")
st.caption("For each season, the model is refitted using only moves completed before that season, then "
           "scored on the moves that happened in it. No future information reaches past predictions.")

eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

if st.button("Run backtest", type="primary"):
    with st.spinner("Refitting season by season"):
        eng.run_backtest()
    st.success(f"Backtest run #{eng.backtest_id} saved.")

S = eng.backtest_summary
if S.empty:
    st.info("No backtest yet, or not enough history: each test season needs at least "
            f"{eng.settings.min_cross_league_pairs} earlier cross league pairs to train on.")
    st.stop()

st.subheader("Overall, cross league moves only")
t = S[["label", "n_test", "mean_actual", "mae_model", "mae_persistence", "mae_pair_ratio", "mae_dest_mean",
       "rmse_model", "rmse_persistence", "coverage50", "coverage80", "p_beats_carry_over",
       "p_beats_best_baseline"]].copy()
t["model beats carry over"] = t["mae_model"] < t["mae_persistence"]
t["model beats pair ratio"] = t["mae_model"] < t["mae_pair_ratio"]
t = t.rename(columns={"label": "Metric", "n_test": "Test pairs", "mean_actual": "Mean actual per 90",
                      "mae_model": "MAE model", "mae_persistence": "MAE carry over",
                      "mae_pair_ratio": "MAE pair ratio", "mae_dest_mean": "MAE league average",
                      "rmse_model": "RMSE model", "rmse_persistence": "RMSE carry over",
                      "coverage50": "50% coverage", "coverage80": "80% coverage",
                      "p_beats_carry_over": "Bootstrap: model beats carry over",
                      "p_beats_best_baseline": "Bootstrap: model beats best baseline"})
df_show(t.round(3))
st.markdown("""
**Baselines.** *Carry over*: assume the current per 90 holds. *Pair ratio*: the simple conversion factor
approach (average ratio for that league pair in earlier data). *League average*: destination league positional
average. A model that cannot beat these is flagged on the projection page.

**Bootstrap columns.** Share of 1,000 resampled test sets where the model had lower error. Near 0.5 means
the difference is noise; above 0.9 is a dependable edge.

**Coverage.** A calibrated 80% interval contains about 80% of actual outcomes. Much lower means intervals are
too narrow (overconfident); much higher means too wide.
""")

m = st.selectbox("Metric detail", list(eng.backtest_preds), format_func=variant_label)
P = eng.backtest_preds[m]

c1, c2 = st.columns(2)
with c1:
    lim = float(np.nanpercentile(np.r_[P["actual"], P["model"]], 99)) * 1.05
    fig = px.scatter(P, x="model", y="actual", color="position_group", opacity=0.6,
                     hover_data=["player_name", "src_league", "dst_league", "dst_season", "dst_minutes"],
                     labels={"model": "Projected per 90", "actual": "Actual per 90"})
    fig.add_trace(go.Scatter(x=[0, lim], y=[0, lim], mode="lines", line=dict(color="grey", dash="dot"),
                             showlegend=False))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), title="Projected vs actual")
    st.plotly_chart(fig, width="stretch")
with c2:
    cal = pd.DataFrame({"nominal": [0.5, 0.8],
                        "observed": [((P.actual >= P.lo50) & (P.actual <= P.hi50)).mean(),
                                     ((P.actual >= P.lo80) & (P.actual <= P.hi80)).mean()]})
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, 1], y=[0, 1], mode="lines", line=dict(color="grey", dash="dot"),
                             name="Perfect"))
    fig.add_trace(go.Scatter(x=cal["nominal"], y=cal["observed"], mode="markers+lines",
                             marker=dict(size=12, color=NAVY), name="Model"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10), title="Interval calibration",
                      xaxis_title="Nominal coverage", yaxis_title="Observed coverage")
    st.plotly_chart(fig, width="stretch")

cols = ["n_test", "mae_model", "mae_persistence", "mae_pair_ratio", "coverage80"]
for title, by in [("By position", ["position_group"]), ("By league pair", ["src_league", "dst_league"]),
                  ("By destination minutes", ["dst_minutes_bin"]), ("By source minutes", ["src_minutes_bin"]),
                  ("By test season", ["test_year"])]:
    st.markdown(f"**{title}**")
    df_show(summarize(P, by)[by + cols].round(3))

with st.expander("Every test prediction"):
    df_show(P.round(3))

st.subheader("Limitations")
st.markdown(f"""
* Only players who moved and played at least {eng.settings.min_minutes_each_side} minutes on both sides are
  scored. Players who moved and barely played are missing, so the backtest describes players who earned minutes.
* Coverage is computed at each player's actual destination minutes. In use, minutes are your scenario input.
* League pairs with few test moves have unstable error estimates; check the counts above.
* Definitions differ between providers; provider specific metrics are only paired within one provider.
""")
