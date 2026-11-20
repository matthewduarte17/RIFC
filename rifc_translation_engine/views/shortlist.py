from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import recruit as R
from engine import report
from engine.api import player_from_agg
from engine.schema import METRICS
from engine import identity as I
from ui import GOLD, NAVY, df_show, get_engine, get_identity, player_search, setup, stale_warning

setup("Recruitment Shortlist")
st.title("Recruitment shortlist")
st.caption("Candidates from any league, side by side on translated Championship numbers at the same minutes. No overall "
           "rating: the aim is to decide who gets video time next.")

eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

idn = get_identity(eng)
R.shortlist_migrate(idn)
with st.expander("Add a candidate", expanded=R.shortlist().empty):
    uid_add = player_search(eng, "sl_add", label="Search player")
    if uid_add:
        a, b, c = st.columns([1.2, 2, 0.8], vertical_alignment="bottom")
        roles = list(R.ROLES)
        pg = idn.players.set_index("uid").loc[uid_add, "last_position"]
        r_role = a.selectbox("Role", roles, index=roles.index(R.DEFAULT_ROLE.get(pg, roles[0])), key="sl_role")
        r_note = b.text_input("Note", key="sl_note")
        if c.button("Add", type="primary"):
            R.shortlist_add(uid_add, r_role, r_note)
            st.rerun()

sl = R.shortlist()
if sl.empty:
    st.info("The shortlist is empty. Add candidates here, from Recruitment Discovery, Potential Targets or a profile.")
    st.stop()

# one entry per player; projections use his latest eligible season (450+ minutes, outfield)
le = I.latest_eligible(idn, 450).set_index("uid")
sl = sl[sl["key"].isin(le.index)].copy()
tab = le.loc[sl["key"]].reset_index().set_index("uid")
sl = sl.join(tab[["player_name", "team", "league", "season", "position_group", "minutes"]], on="key")
pl = idn.players.set_index("uid")
sl["player_name"] = sl["key"].map(pl["display_name"])

st.markdown("### Filters and scenario")
c1, c2, c3, c4 = st.columns(4)
f_role = c1.multiselect("Role", list(R.ROLES), default=sorted(sl["role"].unique()))
f_league = c2.multiselect("League (season used)", sorted(sl["league"].unique()), default=sorted(sl["league"].unique()))
minutes = c3.slider("Standard minutes for every candidate", 450, 3200, 2000, 50)
show_low = c4.checkbox("Include Low confidence numbers", True)
view = sl[sl["role"].isin(f_role) & sl["league"].isin(f_league)]
if view.empty:
    st.info("No candidates match the filters.")
    st.stop()

roles_in = view["role"].unique()
metric_pool = list(dict.fromkeys(m for r in roles_in for m in R.ROLES[r]["metrics"]))
metrics = st.multiselect("Metrics", metric_pool, default=metric_pool[:6], format_func=lambda m: METRICS[m].label)
if not metrics:
    st.stop()

evals = {}
rows = []
verified = I.verified_uids()
for r in view.itertuples():
    ag = tab.loc[r.key]
    ev = R.evaluate(eng, player_from_agg(ag), r.role, minutes, row=ag, metrics=metrics)
    evals[r.key] = ev
    t = ev.table.set_index("metric")
    part_ = " (partial dataset)" if bool(tab.loc[r.key].get("display_only")) else ""
    out = {"key": r.key, "Player": r.player_name,
           "Season used": f"{r.season} {I.LEAGUE_SHORT.get(r.league, r.league)}{part_}",
           "Club that season": r.team, "Current club": "Verified" if r.key in verified else "Unverified",
           "Role": r.role, "Minutes played": int(r.minutes), "Note": r.note,
           "Confidence": ev.summary["overall_confidence"]}
    for m in metrics:
        k = METRICS[m].kind
        x = t.loc[m] if m in t.index else None
        ok = x is not None and pd.notna(x.get("pred")) and (show_low or x.get("confidence") != "Low")
        out[f"{METRICS[m].label} now"] = x.get("current") if x is not None else None
        out[f"{METRICS[m].label} proj"] = x["pred"] if ok else None
        out[f"{METRICS[m].label} 80%"] = (f"{x['lo80']:.2f} to {x['hi80']:.2f}" if k == "rate" else
                                         f"{x['lo80']:.1f} to {x['hi80']:.1f}") if ok else ""
        out[f"{METRICS[m].label} conf"] = x.get("confidence") if x is not None else "None"
        out[f"{METRICS[m].label} pctl"] = x.get("pctl_uslc") if ok else None
    rows.append(out)
W = pd.DataFrame(rows)

st.markdown("### Side by side")
sort_by = st.selectbox("Sort by", [f"{METRICS[m].label} proj" for m in metrics] + [f"{METRICS[m].label} pctl" for m in metrics]
                       + ["Minutes played"])
W = W.sort_values(sort_by, ascending=False, na_position="last")
cfg = {f"{METRICS[m].label} pctl": st.column_config.ProgressColumn(f"{METRICS[m].label} pctl", min_value=0,
                                                                    max_value=100, format="%.0f") for m in metrics}
cfg.update({c: st.column_config.NumberColumn(format="%.2f") for c in W.columns if c.endswith(" now") or c.endswith(" proj")})
st.dataframe(W.drop(columns=["key"]), hide_index=True, width="stretch", column_config=cfg)
st.caption(f"'now' is observed per 90 in the source league; 'proj' is the neutral Championship projection per 90; "
           f"80% is the projection range; pctl is the Championship percentile in that role. Totals at {minutes:,} "
           "minutes are on each player's page.")

st.markdown("### One metric across candidates")
m = st.selectbox("Metric", metrics, format_func=lambda x: METRICS[x].label, key="sl_chart_metric")
fig = go.Figure()
names = []
for k, ev in evals.items():
    t = ev.table.set_index("metric")
    if m not in t.index or pd.isna(t.loc[m].get("pred")):
        continue
    x = t.loc[m]
    nm = f"{ev.player.name} ({ev.player.league[:4]} {ev.player.season})"
    names.append(nm)
    fig.add_shape(type="line", x0=x["lo80"], x1=x["hi80"], y0=nm, y1=nm, line=dict(color="rgba(11,37,69,.3)", width=10))
    fig.add_trace(go.Scatter(x=[x["pred"]], y=[nm], mode="markers", showlegend=False,
                             marker=dict(color=GOLD, size=13, symbol="diamond", line=dict(color=NAVY, width=1)),
                             hovertemplate=f"{nm}<br>projected %{{x:.2f}} ({x['lo80']:.2f} to {x['hi80']:.2f})<br>"
                                           f"confidence {x['confidence']}<extra></extra>"))
groups = sorted({g for r in roles_in for g in R.role_groups(r)})
rifc = R.rifc_players(eng, groups)
for r in rifc.itertuples():
    v = getattr(r, f"{m}_p90")
    if pd.notna(v):
        fig.add_vline(x=v, line=dict(color="#8DA9C4", dash="dash"), annotation_text=f"{r.player_name} (RIFC)",
                      annotation_position="top")
fig.update_layout(height=120 + 40 * max(1, len(names)), margin=dict(l=10, r=10, t=50, b=10),
                  xaxis_title=f"{METRICS[m].label}, Championship per 90" if METRICS[m].kind == "rate" else METRICS[m].label,
                  yaxis=dict(autorange="reversed"))
st.plotly_chart(fig, width="stretch")
st.caption("Dashed lines: current RIFC players in the same roles, observed this season (450+ minutes).")

st.markdown("### Manage")
a, b, c = st.columns(3)
pick = a.selectbox("Candidate", W["key"].tolist(), format_func=lambda k: W.set_index("key").loc[k, "Player"])
if b.button("Remove from shortlist"):
    R.shortlist_remove(pick)
    st.rerun()
ev = evals[pick]
rif = R.rifc_players(eng, R.role_groups(ev.role))
full = R.evaluate(eng, ev.player, ev.role, minutes, row=tab.loc[pick])
if a.button("Open profile"):
    from ui import open_profile
    open_profile(pick)
c.download_button("Download report", report.build(eng, full, R.compare(eng, full, rif) if not rif.empty else None, club=I.club_status(idn, pick)),
                  file_name=f"{ev.player.name.replace(' ', '_').replace('.', '')}_projection.html", mime="text/html")
