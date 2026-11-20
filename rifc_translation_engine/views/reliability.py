from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import audit as A
from ui import ACCENT, BORDER, CAND, CONF_HELP, MUTED, card, esc, get_engine, plot, section, setup, stale_warning

setup("Model Reliability")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

st.markdown("<div class='eyebrow'>Model reliability</div>", unsafe_allow_html=True)
st.title("How far to trust a projection")
st.caption("Every number here comes from the time based backtest: the model was trained only on moves before each test "
           "season, then asked to project real moves it had not seen.")


@st.cache_data(show_spinner=False)
def tables(run, bt, fp):
    return A.transition_coverage(eng), A.breakdown(eng, ["transition"]), A.breakdown(eng, ["position_group"])


cov, by_tr, by_pos = tables(eng.run_id, eng.backtest_id, eng.fingerprint)

section("League transitions", "How many real moves each projection path has been tested on.")
cols = st.columns(len(cov))
for c, r in zip(cols, cov.itertuples()):
    c.markdown(f"<div class='pc'><div class='lab'>{esc(r.transition)}</div><div class='big'>{r._4}"
               f"<small>tested moves</small></div><div class='rng'>{r._2} moves in the data</div>"
               f"<div class='foot'><span>{esc(r.trust)}</span></div></div>", unsafe_allow_html=True)
st.caption("Adequate: 50+ tested moves. Limited: 20 to 49. Too few: under 20, so accuracy is unknown.")

section("Verdict by metric and transition", "Reliable: beats every simple method with honest ranges on 50+ moves. "
        "Promising: the same on 20 to 49 moves.")
order = ["MLSNP to USLC", "USL1 to USLC", "USLC to USL1", "MLSNP to USL1", "USLC to MLSNP", "USL1 to MLSNP"]
piv = by_tr.pivot_table(index="metric", columns="transition", values="verdict", aggfunc="first")
piv = piv[[c for c in order if c in piv.columns]]
short = {"Reliable": "Reliable", "Promising, small sample": "Promising", "Unknown (too few moves)": "Too few",
         "Not reliable: simple baseline better": "Simple method better"}


def shade(v):
    s = short.get(v, v if isinstance(v, str) else "")
    return {"Reliable": f"background-color: rgba(76,141,255,.38); color: #E6EAF2",
            "Promising": "background-color: rgba(76,141,255,.16); color: #E6EAF2",
            "Simple method better": "background-color: rgba(242,181,68,.14); color: #F7D58C",
            "Too few": "color: #5B6B85"}.get(s, "")


st.dataframe(piv.style.map(shade).format(lambda v: short.get(v, v) if isinstance(v, str) else ""), width="stretch",
             height=min(38 * (len(piv) + 1) + 4, 900))

section("Accuracy against simple methods", "Average miss on past moves into the Championship, per 90.")
tr = st.selectbox("Transition", [t for t in order if t in set(by_tr["transition"])][:2] +
                  [t for t in order if t in set(by_tr["transition"])][2:], key="rel_tr")
d = by_tr[by_tr["transition"] == tr].copy()
d = d[d["metric"].notna()].sort_values("Model vs carry over %")
fig = go.Figure()
for r in d.itertuples():
    fig.add_shape(type="line", x0=r._6, x1=r._7, y0=r.metric, y1=r.metric, line=dict(color=BORDER, width=3))
fig.add_trace(go.Scatter(x=d["MAE carry over"], y=d["metric"], mode="markers", name="Carry the current number over",
                         marker=dict(color=MUTED, size=10, symbol="circle-open", line=dict(width=2)),
                         hovertemplate="%{y}: carry over misses %{x:.3f}<extra></extra>"))
fig.add_trace(go.Scatter(x=d["MAE model"], y=d["metric"], mode="markers", name="Translation model",
                         marker=dict(color=CAND, size=12, symbol="diamond"),
                         customdata=np.c_[d["n"], d["80% coverage"] * 100],
                         hovertemplate="%{y}: model misses %{x:.3f} (n=%{customdata[0]}, 80% range coverage "
                                       "%{customdata[1]:.0f}%)<extra></extra>"))
fig.update_layout(height=120 + 30 * len(d), xaxis=dict(title="Average miss per 90 (lower is better)", type="log"),
                  yaxis=dict(autorange="reversed"), legend=dict(orientation="h", yanchor="top", y=-0.12, x=0))
plot(fig, key="rel_mae", config={"displayModeBar": False})
st.caption("Log scale so that metrics of different sizes fit one chart. Pass completion % is in percentage points.")

from engine import multiseason as MS
from engine.schema import METRICS
from engine.model import variant_label
ms = MS.load_saved()
section("Does an earlier season help?", "Out of sample test of a two season input (current and previous season in the "
        "same league, weighted by minutes) against the current one season input. Same model, same time based backtest.")
if ms:
    rows = []
    for k, v in ms["modes"].get("two_season", {}).items():
        a_, b_ = v.get("all test moves", {}), v.get("moves with a previous season", {})
        rows.append({"Metric": variant_label(k), "Test moves": a_.get("n"),
                     "Error change, all moves": f"{a_.get('change_pct', 0):+.1f}%",
                     "P(two season better)": round(a_.get("p_multi_better") or 0, 2),
                     "Error change, players with a previous season": f"{b_.get('change_pct', 0):+.1f}% (n={b_.get('n')})",
                     "80% coverage, now and two season": f"{a_.get('cov80_current', 0):.0%} / {a_.get('cov80_multi', 0):.0%}",
                     "Passes the adoption rule": "Yes" if ms["verdict"].get(k, {}).get("adopt") else ""})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    yes = [variant_label(k) for k, v in ms["verdict"].items() if v.get("adopt")]
    st.caption(f"Run {ms['run_at'][:10]} on model run {ms['model_run']}. {ms['prior_share']:.0%} of test moves have a "
               "previous season in the same league. Adoption rule, set before the run: at least 2% lower error on all "
               "test moves, P of at least 0.9, and 80% coverage between 70% and 90%. "
               + (f"Passes for {', '.join(yes)}. " if yes else "No metric passes. ")
               + "Not switched on: the production model still reads one season until you decide to adopt it.")
else:
    st.info("Not run yet. Run: python -m engine.multiseason")

with st.expander("By position (all transitions)"):
    p = by_pos.pivot_table(index="metric", columns="position_group", values="verdict", aggfunc="first")
    st.dataframe(p.style.map(shade).format(lambda v: short.get(v, v) if isinstance(v, str) else ""), width="stretch")

with st.expander("Methodology"):
    st.markdown(
        "* **Model**: a regularised regression learned from players who appear in two consecutive seasons, across "
        "leagues and within a league. No league strength percentage is assumed; league effects are learned from movers.\n"
        "* **Backtest**: train on moves before 2025, test on moves into 2025; then train on everything before 2026, test "
        "on moves into 2026. Player level leakage was audited (see Validation Audit in Admin).\n"
        "* **Simple methods**: carry the current per 90 over; the average league pair ratio; the Championship "
        "positional average. A metric is validated only if the model beats all of them and its 80% ranges cover "
        "between 70% and 90% of outcomes.\n"
        "* **Confidence on player pages**: " + "; ".join(f"{k}: {v}" for k, v in CONF_HELP.items()) + "\n"
        "* **Limits**: no League One or MLS Next Pro team data; MLS Next Pro 2026 excluded until re-imported; moves "
        "into MLS Next Pro are too few to test.")
try:
    st.page_link("views/validation_audit.py", label="Full validation audit", icon=":material/fact_check:")
except Exception:
    pass
