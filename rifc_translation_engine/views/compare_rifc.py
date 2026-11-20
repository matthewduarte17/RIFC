from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

import charts as C
from engine import recruit as R
from engine.api import player_from_agg
from engine.schema import METRICS
from engine import identity as I
from ui import (CAND, MUTED, RIFC_SHADES, card, df_show, esc, get_engine, get_identity, player_search, plot, section,
                setup, stale_warning)

setup("Compare with RIFC")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

st.markdown("<div class='eyebrow'>Compare with RIFC</div>", unsafe_allow_html=True)
st.title("Candidate against current Rhode Island players")
st.caption("The candidate's projected Championship numbers against what RIFC players actually produced. Projected and "
           "observed are labelled separately; differences inside the candidate's 80% range are not treated as real.")

idn = get_identity(eng)
_d = idn.records.loc[idn.records["key"] == "R. Carmichael|Forward Madison|USL League One|2026", "uid"]
uid = player_search(eng, "cmp", _d.iloc[0] if len(_d) else None, label="Candidate")
if uid is None:
    st.stop()
recs = I.records_of(idn, uid)
recs = recs[recs["position_group"].notna() & (recs["position_group"] != "GK")]
if recs.empty:
    st.info("No outfield season record for this player.")
    st.stop()
elig = recs[recs["minutes"] >= 450]
want = st.session_state.get("player_rec")
ks = recs["rec_key"].tolist()
d_rec = want if want in ks else (elig["rec_key"].iloc[0] if not elig.empty else ks[0])
lab = recs.set_index("rec_key")
rk = st.selectbox("Season record", ks, index=ks.index(d_rec), key=f"cmp_rec_{uid}",
                  format_func=lambda k: f"{lab.loc[k, 'season']} {I.LEAGUE_SHORT.get(lab.loc[k, 'league'], lab.loc[k, 'league'])}, "
                                        f"{lab.loc[k, 'team']}, {int(lab.loc[k, 'minutes']):,} min")
row = lab.loc[rk].copy()
row["rec_key"] = rk
player = player_from_agg(row)
player.name = idn.players.set_index("uid").loc[uid, "display_name"]
roles = list(R.ROLES)
c1, c2 = st.columns([1, 2.4], vertical_alignment="bottom")
role = c1.selectbox("Role", roles, index=roles.index(R.DEFAULT_ROLE.get(player.position_group, roles[0])),
                    key=f"cmp_role_{uid}")
minutes = st.session_state.get("minutes", 2200)
ev = R.evaluate(eng, player, role, minutes, row=row)

all_rifc = R.rifc_players(eng, None, min_minutes=1)
season = all_rifc["season"].iloc[0] if not all_rifc.empty else None
default = R.rifc_players(eng, R.role_groups(role))
rl = all_rifc.set_index("key")
chosen = c2.multiselect(f"RIFC players ({season} Championship, any position)", all_rifc["key"].tolist(),
                        default=default["key"].tolist()[:3], max_selections=4, key=f"cmp_rifc_{role}",
                        format_func=lambda k: f"{rl.loc[k, 'player_name']} | {rl.loc[k, 'position_group']} | "
                                              f"{int(rl.loc[k, 'minutes']):,} min")
rifc = all_rifc[all_rifc["key"].isin(chosen)]
if rifc.empty:
    st.info("Pick at least one RIFC player.")
    st.stop()
cmp = R.compare(eng, ev, rifc)
t = ev.table.set_index("metric")

# ---------------------------------------------------------------------------
# Takeaways
# ---------------------------------------------------------------------------
section("Takeaways")
lines = R.comparison_summary(cmp, ev)
low = rifc[rifc["minutes"] < 900]["player_name"].tolist()
if low:
    lines.append(f"Under 900 minutes, so their own per 90 numbers are noisy: {', '.join(low)}.")
lowc = [t.loc[m, "Metric"] for m in dict.fromkeys(cmp["metric"]) if t.loc[m, "confidence"] == "Low"]
if lowc:
    lines.append("Low confidence projections, read as rough: " + ", ".join(lowc) + ".")
card(f"{player.name} ({player.team}, {player.league} {player.season}) as a {role.lower()}", lines)

if role == "Center forward":
    sv = R.striker_view(cmp)
    if not sv.empty:
        piv = sv.pivot(index="question", columns="rifc_player", values="answer").reindex(
            [q for q, _ in R.STRIKER_QUESTIONS]).dropna(how="all")
        section("Does he project to offer more than ...")
        df_show(piv.reset_index().rename(columns={"question": "Area"}))

# ---------------------------------------------------------------------------
# Small multiples
# ---------------------------------------------------------------------------
section("Metric by metric", "Per 90. Gold: candidate projected, with the 80% range. Grey: candidate now, in his own "
        "league. Teal: RIFC players, observed. Shaded band: middle half of Championship players in the role; dotted "
        "line: median.")
order = [m for m in t.index if m in set(cmp["metric"])]
pick = st.multiselect("Metrics", order, default=order[:8], format_func=lambda m: METRICS[m].label, key=f"cmp_m_{role}")
rows = []
groups = R.role_groups(role)
for m in pick:
    r = t.loc[m]
    k = r["kind"]
    d = R.positional_dist(eng, m, groups)
    bench = tuple(np.percentile(d, [25, 50, 75])) if len(d) >= 10 else None
    f = lambda v: R.fmt(v, k)
    rows.append({"metric": m, "label": f"{player.name} (proj.)", "value": r["pred"], "lo": r["lo80"], "hi": r["hi80"],
                 "color": CAND, "bench": bench,
                 "hover": f"{player.name}, projected: {f(r['pred'])} (80% {f(r['lo80'])} to {f(r['hi80'])}), "
                          f"{r['confidence']} confidence"})
    if pd.notna(r["current"]):
        rows.append({"metric": m, "label": f"{player.name} (now)", "value": r["current"], "color": "#5B6B85",
                     "bench": bench, "hover": f"{player.name}, now in {player.league}: {f(r['current'])}"})
    for i, x in enumerate(rifc.itertuples()):
        v = getattr(x, f"{m}_p90")
        if pd.notna(v):
            rows.append({"metric": m, "label": x.player_name, "value": v, "color": RIFC_SHADES[i % 4], "bench": bench,
                         "hover": f"{x.player_name}, RIFC observed: {f(v)} in {int(x.minutes):,} min"})
fig = C.compare_bars(rows, {m: METRICS[m].kind for m in pick}, player.name)
if fig is not None:
    plot(fig, key="cmp_bars", config={"displayModeBar": False})

with st.expander("Detailed table"):
    tab = cmp.assign(**{
        "Candidate (projected)": [f"{R.fmt(p, k)} ({R.fmt(lo, k)} to {R.fmt(hi, k)})" for p, lo, hi, k in
                                  zip(cmp["cand_pred"], cmp["cand_lo80"], cmp["cand_hi80"], cmp["kind"])],
        "RIFC (observed)": [R.fmt(v, k) for v, k in zip(cmp["rifc_obs"], cmp["kind"])],
        "Candidate pctl": cmp["cand_pctl"].round(0), "RIFC pctl": cmp["rifc_pctl"].round(0)})
    df_show(tab[["Metric", "rifc_player", "Candidate (projected)", "RIFC (observed)", "Candidate pctl", "RIFC pctl",
                 "verdict", "confidence"]].rename(columns={"rifc_player": "RIFC player", "verdict": "Read",
                                                           "confidence": "Projection confidence"}))
    st.caption("Clearly higher or lower: the candidate's whole 80% range is above or below the RIFC player's number. "
               "Anything else is not a clear difference.")
