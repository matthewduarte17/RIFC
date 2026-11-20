from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import charts as C
from engine import identity as I
from engine import recruit as R
from engine import report
from engine.api import player_from_agg
from engine.schema import METRICS, POSITION_LABELS, USLC
from ui import (CAND, CONF_HELP, FAINT, LEAGUE_COLORS, MUTED, STATUS_TEXT, banner, card, esc, get_engine, get_identity,
                meter, pill, player_search, plot, section, setup, stale_warning)

setup("Player Profile")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()
idn = get_identity(eng)
PL = idn.players.set_index("uid")

st.markdown("<div class='eyebrow'>Player profile</div>", unsafe_allow_html=True)
DEFAULT_UID = idn.records.loc[idn.records["key"] == "R. Carmichael|Forward Madison|USL League One|2026", "uid"]
uid = player_search(eng, "prof", DEFAULT_UID.iloc[0] if len(DEFAULT_UID) else None)
if uid is None:
    st.stop()
P = PL.loc[uid]
recs = I.records_of(idn, uid)
out_recs = recs[recs["position_group"].notna() & (recs["position_group"] != "GK")]
if out_recs.empty:
    st.info("Goalkeeper or no position in the data: no outfield projection available.")
    st.stop()

# season record: the one asked for, else his latest record with 450+ minutes, else his latest record
want = st.session_state.get("player_rec")
elig = out_recs[out_recs["minutes"] >= 450]
default_rec = want if want in set(out_recs["rec_key"]) else (elig["rec_key"].iloc[0] if not elig.empty
                                                             else out_recs["rec_key"].iloc[0])
LS = I.LEAGUE_SHORT


def rec_label(k):
    r = out_recs.set_index("rec_key").loc[k]
    tag = "  (latest eligible)" if not elig.empty and k == elig["rec_key"].iloc[0] else ""
    return f"{r['season']} {LS.get(r['league'], r['league'])}, {r['team']}, {int(r['minutes']):,} min{tag}"


# ---------------------------------------------------------------------------
# Header: the player, not one season (nothing truncated: wraps on narrow screens)
# ---------------------------------------------------------------------------
cs = I.club_status(idn, uid)
by = P["birth_year"]
age_now = f"about {int(date.today().year - by)} (born {int(by)})" if pd.notna(by) else "Not in data"
positions = ", ".join(dict.fromkeys(POSITION_LABELS.get(x, x) for x in recs["position_group"].dropna()))
cur = (f"{cs['current_club'] or cs['status']}, verified {cs['verified_on']} ({cs['source']})" if cs["verified"]
       else "Current club unverified")
items = [("Age", age_now), ("Position", positions or "Not in data"),
         ("Last recorded club", f"{cs['last_club']} ({LS.get(cs['last_league'], cs['last_league'])} {cs['last_season']})"),
         ("Current club", cur),
         ("Contract expiry", f"{cs['contract_expiry']} ({cs['contract_source']})" if cs["contract_expiry"]
          else "Not in data"),
         ("Seasons in data", f"{P['first_season']} to {P['last_season']}" if P["first_season"] != P["last_season"]
          else P["last_season"]),
         ("Total minutes", f"{int(P['total_minutes']):,}"),
         ("Nationality", P["nationality"] or "Not in data")]
if pd.notna(P["height_cm"]):
    items.append(("Height", f"{P['height_cm']:.0f} cm" + (f", {P['foot']} foot" if P["foot"] and P["foot"] != "unknown" else "")))
items.append(("Data provider", ", ".join(sorted({str(x).title() for x in recs["provider"].dropna()})) or "Not in data"))
lvl_kind = {"Verified": "val", "Confirmed by scout": "val", "Single season": "", "Probable": "exp", "Uncertain": "exp"}
grid = "".join(f"<div class='ph-item'><div class='k'>{esc(k)}</div><div class='v'>{esc(v)}</div></div>" for k, v in items)
notes = []
if not cs["verified"]:
    rec_note = {"In the latest data": f"Last record is from the latest season in the data ({cs['last_season']}).",
                }.get(cs["recency"], f"{cs['recency']}: his club since {cs['last_season']} is unknown.")
    notes.append(rec_note + " A club in the data is not proof of where he plays or his contract today.")
if cs["wyscout_team_field"] and cs["wyscout_team_field"] != cs["last_club"]:
    notes.append(f"Wyscout's Team field on the {cs['wyscout_date']} export says {cs['wyscout_team_field']}; that field "
                 "is not consistent across files, so it is shown as a lead to check, not as his club.")
if P["flags"]:
    notes.append("Identity notes: " + "; ".join(P["flags"]) + ".")
st.markdown(f"""<div class='ph'><div class='ph-top'><div class='ph-name'>{esc(P['display_name'])}</div>
{pill(f"Identity: {P['identity_level']}", lvl_kind.get(P['identity_level'], ''))}
<span class='muted'>Player ID {esc(uid)}</span></div>
<div class='ph-sub'>{esc(", ".join(P['clubs']))}</div>
<div class='ph-grid'>{grid}</div>{''.join(f"<div class='ph-note'>{esc(n)}</div>" for n in notes)}</div>""",
            unsafe_allow_html=True)

with st.expander("Verify current club and contract" if not cs["verified"] else "Update current club verification"):
    st.caption("Saved with a date and source. History is kept; data records and the last recorded club never change.")
    with st.form(f"verify_{uid}", clear_on_submit=True):
        a, b, c = st.columns(3)
        vstatus = a.selectbox("Status", I.STATUSES)
        vclub = b.text_input("Current club", placeholder="Club name, if at a club")
        vdate = c.date_input("Verified on", value=date.today())
        a, b, c = st.columns(3)
        vsrc = a.text_input("Source", placeholder="Agent call, club website, Transfermarkt...")
        vce = b.text_input("Contract expiry", placeholder="Optional")
        vca = c.date_input("Contract information as of", value=date.today())
        vnote = st.text_input("Note", "")
        if st.form_submit_button("Save verification", type="primary"):
            if not vsrc.strip():
                st.error("Add the source so the verification can be checked later.")
            else:
                I.add_verification(uid, vstatus, vclub.strip() or None, vdate, vsrc.strip(), vce.strip(), vca, vnote)
                st.toast("Verification saved.")
                st.rerun()
    hist = I.verifications(uid)
    if not hist.empty:
        st.dataframe(hist[["entered_at", "status", "current_club", "verified_on", "source", "contract_expiry",
                           "contract_as_of", "note"]], hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Career record (observed only)
# ---------------------------------------------------------------------------
section("Career in the data", "Observed numbers, season by season. Projections are further down and kept separate.")
st_all = idn.stints[idn.stints["uid"] == uid].sort_values(["season_start", "minutes"], ascending=[False, False])
role0 = R.DEFAULT_ROLE.get(out_recs["position_group"].iloc[0], "Center forward")
show_m = [m for m in dict.fromkeys(["goals", "assists", "xg", "xa"] + R.PRIMARY.get(role0, [])) if m in METRICS][:7]
rows = []
for (season, league), g in recs.sort_values("season_start", ascending=False).groupby(["season", "league"], sort=False):
    r = g.iloc[0]
    sp = st_all[(st_all["season"] == season) & (st_all["league"] == league)]
    clubs = ", ".join(f"{x.team} ({int(x.minutes):,})" if len(sp) > 1 else x.team for x in sp.itertuples())
    row_ = {"Season": season, "League": LS.get(league, league) + (" (partial dataset, not used by the model)"
                                                               if bool(r.get("display_only")) else ""), "Club": clubs, "Pos": r["position_group"],
            "Minutes": int(r["minutes"]), "Matches": int(r["matches"]) if pd.notna(r["matches"]) else None,
            "Goals": round(r["goals_total"]) if pd.notna(r.get("goals_total")) else None,
            "Assists": round(r["assists_total"]) if pd.notna(r.get("assists_total")) else None}
    for m in show_m:
        k = METRICS[m].kind
        row_[f"{METRICS[m].label}{'' if k == 'pct' else ' /90'}"] = (round(r[f"{m}_p90"], 1 if k == "pct" else 2)
                                                                     if pd.notna(r.get(f"{m}_p90")) else None)
    rows.append(row_)
career = pd.DataFrame(rows)
multi = recs.groupby("season")["league"].nunique()
multi = multi[multi > 1]
tab1, tab2, tab3 = st.tabs(["Season by season", "Trends", "Club history"])
with tab1:
    st.dataframe(career, hide_index=True, width="stretch")
    if not multi.empty:
        for s_ in multi.index:
            g = recs[recs["season"] == s_]
            st.caption(f"{s_}: played in {len(g)} competitions ({', '.join(g['league'])}); season total "
                       f"{int(g['minutes'].sum()):,} minutes, {int(g['goals_total'].sum(min_count=1) or 0)} goals, "
                       f"{int(g['assists_total'].sum(min_count=1) or 0)} assists. Per 90 rates are not combined across "
                       "competitions of different levels.")
with tab2:
    tm = st.multiselect("Metrics", [m for m in METRICS if recs[f"{m}_p90"].notna().any()],
                        default=[m for m in R.PRIMARY.get(role0, [])[:3] if recs[f"{m}_p90"].notna().any()],
                        format_func=lambda m: METRICS[m].label, max_selections=4, key=f"trend_{uid}")
    rr = recs.sort_values("season_start")
    if tm and len(rr):
        fig = make_subplots(rows=1, cols=len(tm) + 1, subplot_titles=["Minutes"] + [METRICS[m].label for m in tm],
                            horizontal_spacing=0.06)
        for lg, g in rr.groupby("league"):
            fig.add_trace(go.Bar(x=g["season"], y=g["minutes"], name=LS.get(lg, lg), marker_color=LEAGUE_COLORS.get(lg),
                                 legendgroup=lg, hovertemplate="%{x}: %{y:,} min<extra>" + lg + "</extra>"), 1, 1)
        for j, m in enumerate(tm, start=2):
            fig.add_trace(go.Scatter(x=rr["season"], y=rr[f"{m}_p90"], mode="lines", line=dict(color=FAINT, width=1),
                                     showlegend=False, hoverinfo="skip"), 1, j)
            for lg, g in rr.groupby("league"):
                fig.add_trace(go.Scatter(x=g["season"], y=g[f"{m}_p90"], mode="markers", showlegend=False,
                                         legendgroup=lg, marker=dict(color=LEAGUE_COLORS.get(lg), size=10),
                                         customdata=np.c_[g["team"], g["minutes"]],
                                         hovertemplate="%{x} " + LS.get(lg, lg) + ", %{customdata[0]}: %{y:.2f} "
                                                       "(%{customdata[1]:,.0f} min)<extra></extra>"), 1, j)
        fig.update_layout(height=280, barmode="stack", margin=dict(l=8, r=8, t=30, b=8),
                          legend=dict(orientation="h", y=-0.2, x=0))
        fig.update_xaxes(type="category")
        for a_ in fig.layout.annotations:
            a_.font = dict(size=12, color="#E6EAF2")
        plot(fig, key=f"trend_fig_{uid}", config={"displayModeBar": False})
        st.caption("Observed per 90 in each league. League levels differ, so a change between leagues is not a "
                   "change in ability by itself; the projection below translates the selected season.")
with tab3:
    h = st_all.sort_values("season_start")[["season", "league", "team", "minutes", "matches", "position_raw",
                                            "export_date"]].rename(columns={
        "season": "Season", "league": "League", "team": "Club", "minutes": "Minutes", "matches": "Matches",
        "position_raw": "Positions", "export_date": "Export date"})
    st.dataframe(h, hide_index=True, width="stretch")
    moves = []
    prev = None
    for s_, g in st_all.sort_values("season_start").groupby("season", sort=False):
        clubs = list(g.sort_values("minutes", ascending=False)["team"])
        if prev and clubs[0] != prev:
            moves.append(f"{s_}: {prev} to {clubs[0]}")
        prev = clubs[0]
    st.caption("Club changes seen in the data: " + ("; ".join(moves) if moves else "none") + ". The data shows clubs "
               "by season; it does not record transfer dates or fees.")

# ---------------------------------------------------------------------------
# Projection from one season record
# ---------------------------------------------------------------------------
section("Projection", "Translated to an average Championship team from one season record. Pick the record.")
ks = out_recs["rec_key"].tolist()
rk = st.selectbox("Season record used for the projection", ks, index=ks.index(default_rec), format_func=rec_label,
                  key=f"rec_{uid}")
st.session_state["player_rec"] = rk
row = out_recs.set_index("rec_key").loc[rk].copy()
row["rec_key"] = rk
player = player_from_agg(row)
player.name = P["display_name"] if P["display_name"] else player.name
if rk != (elig["rec_key"].iloc[0] if not elig.empty else ks[0]):
    st.caption("This is not his latest eligible season.")
PARTIAL = bool(row.get("display_only"))
if PARTIAL:
    banner(f"<b>Source data: partial dataset.</b> His {esc(row['league'])} {esc(row['season'])} record comes from an "
           "export that is missing some players (Wyscout's 500 row limit). His own numbers are complete; the season is "
           "not used to train or test the model, and he is not ranked against that partial league. The projection "
           "uses the existing model. The confidence dots below rate the translation model on past moves; they say "
           "nothing about how complete the source data is.", "exp")

roles = list(R.ROLES)
c1, c2, c3 = st.columns([1.2, 2.2, 0.8], vertical_alignment="bottom")
role = c1.selectbox("Role to evaluate", roles,
                    index=roles.index(R.DEFAULT_ROLE.get(player.position_group, roles[0])), key=f"role_{uid}",
                    help="Sets the metrics, benchmarks and RIFC comparison group. Pick the role he would play for us.")
minutes = c2.slider("Projected Championship minutes", 450, 3150, 2200, 50,
                    key="minutes", help="Your assumption. Minutes change season totals, not per 90 output.")
with c3.popover("Advanced", width="stretch"):
    years = st.slider("Years until target season", 0.0, 3.0, 1.0, 0.5, help="Ages the player for the age effect.")

ev = R.evaluate(eng, player, role, minutes, start_share=0.85, years_ahead=years, row=row)
t = ev.table.set_index("metric")
S = ev.summary
groups = R.role_groups(role)
season_b = R.latest_season(eng)

st.markdown(f"<div class='headline'>{esc(R.headline(ev))}</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Projection cards
# ---------------------------------------------------------------------------
section("Projected Championship output",
        f"Neutral projection for an average Championship team, at {int(minutes):,} minutes. "
        f"Percentiles: {season_b} Championship {role.lower()}s with {eng.settings.benchmark_min_minutes}+ minutes.")


def proj_card(m: str) -> str:
    lab = METRICS[m].label
    if m not in t.index:
        return ""
    r = t.loc[m]
    kind = r["kind"]
    if pd.isna(r.get("pred")):
        why = "No data for this metric." if r["status"] == "not available" else "Not enough past moves to project it."
        return (f"<div class='pc na'><div class='lab'>{esc(lab)}</div><div class='big'>Not projected</div>"
                f"<div class='rng'>{esc(why)}</div><div class='foot'>{meter('None')}"
                f"<span>{esc(STATUS_TEXT.get(r['status'], r['status']))}</span></div></div>")
    f = lambda v: R.fmt(v, kind)
    if kind == "rate":
        big = f"{r['pred_season']:.1f}<small>{esc(R.UNIT.get(m, ''))} at {int(minutes):,} min</small>"
        rate = f"<b>{f(r['pred'])}</b> per 90 projected"
        rng = (f"80% range {f(r['lo80'])} to {f(r['hi80'])} per 90 ({r['lo80_season']:.1f} to "
               f"{r['hi80_season']:.1f} over the season)")
    else:
        big = f"{f(r['pred'])}<small>projected</small>"
        rate = "Rate metric, no season total"
        rng = f"80% range {f(r['lo80'])} to {f(r['hi80'])}"
    _, diff = R.change_words(r["current"], r["pred"], kind)
    pct = f"{R.ordinal(r['pctl_uslc'])}" if pd.notna(r.get("pctl_uslc")) else "n/a"
    rows = [(f"Now ({player.league})", f(r["current"]) + ("" if kind == "pct" else " per 90")), ("Change", diff),
            ("Championship percentile", pct)]
    rows_h = "".join(f"<div class='row'><span>{esc(a)}</span><span>{esc(b)}</span></div>" for a, b in rows)
    return (f"<div class='pc'><div class='lab'>{esc(lab)}</div><div class='big'>{big}</div><div class='rate'>{rate}</div>"
            f"{rows_h}<div class='rng'>{esc(rng)}</div>"
            + ("<div class='rng' style='color:#F7D58C'>Source: partial dataset</div>" if PARTIAL else "")
            + f"<div class='foot'>{meter(r['confidence'])}"
            f"<span title='{esc(r.get('why') or '')}'>{esc(STATUS_TEXT.get(r['status'], r['status']))}</span></div></div>")


prim = [m for m in R.PRIMARY.get(role, []) if m in t.index]
st.markdown("<div class='pc-grid'>" + "".join(proj_card(m) for m in prim) + "</div>", unsafe_allow_html=True)
with st.expander("How to read these cards"):
    st.markdown(
        "* **Season total** scales the per 90 projection by your minutes. **Change** compares with his current per 90 "
        "in his own league; it is not good or bad by itself.\n"
        "* **80% range**: past projections landed inside a range like this about four times in five.\n"
        "* **Confidence** (dots): " + "; ".join(f"{k}: {v}" for k, v in CONF_HELP.items()) + "\n"
        "* **Validated** means the backtest on past moves beat simple methods with honest ranges. Not validated "
        "projections are still the model's best estimate, but carry less weight.\n"
        f"* {ev.rifc_note}")

# ---------------------------------------------------------------------------
# Why this projection
# ---------------------------------------------------------------------------
projected = [m for m in t.index if pd.notna(t.loc[m].get("pred"))]
section("Why this projection?")


def _f(v, kind, sign=False):
    txt = R.fmt(abs(v) if sign else v, kind)
    return ("+" if v > 0 else "minus " if v < 0 else "") + txt if sign else txt


def interval_html(x) -> str:
    k = x["kind"]
    lo, hi, cur, pred = x["lo80"], x["hi80"], x["current"], x["pred"]
    a_, b_ = min(lo, cur), max(hi, cur)
    pad = (b_ - a_) * 0.08 or 0.05
    a_, b_ = a_ - pad, b_ + pad
    pos = lambda v: (v - a_) / (b_ - a_) * 100
    return (f"<div class='ivl'><div class='trk'><div class='band' style='left:{pos(lo):.1f}%;width:{pos(hi) - pos(lo):.1f}%'>"
            f"</div><div class='mk now' style='left:{pos(cur):.1f}%' title='Now {R.fmt(cur, k)}'></div>"
            f"<div class='mk proj' style='left:{pos(pred):.1f}%' title='Projected {R.fmt(pred, k)}'></div></div>"
            f"<div class='lbl'><span style='left:{pos(lo):.1f}%'>{R.fmt(lo, k)}</span>"
            f"<span style='left:{pos(hi):.1f}%'>{R.fmt(hi, k)}</span></div></div>")


SHORT = {"xg": "xG", "xa": "xA"}


def short_reason(d, x) -> str:
    p = ev.player
    down = "down" if not d["up"] else "up"
    if d["key"] == "regression":
        return (f"Pulled {down} toward typical output: from {int(p.minutes):,} minutes, a rate this far from the usual "
                f"level for his position only partly repeats the next season.")
    if d["key"] == "support":
        sup = [m_ for m_ in (t.loc[x["metric"], "model_key"] or "").partition("+")[2].split(",") if m_ in METRICS]
        vals = " and ".join(f"{SHORT.get(m_, METRICS[m_].label)} of {R.fmt(p.per90.get(m_), METRICS[m_].kind)}"
                            for m_ in sup)
        return (f"Pulled {down} by his {vals} per 90, which in past moves was a steadier guide than "
                f"{x['label'].lower()} alone.")
    if d["key"] == "league_change":
        tail = ("" if x["n_direct"] else " There are no direct moves between these leagues in the data, so this comes "
                "from league effects estimated across all moves.")
        return f"Pulled {down} by the step from {p.league} to the Championship.{tail}"
    if d["key"] == "age":
        return f"Pulled {down} slightly by age."
    return f"Pulled {down} by {d['name'].lower()}."


if projected:
    m_sel = st.columns([1, 2])[0].selectbox("Metric", projected, index=0, format_func=lambda m: METRICS[m].label,
                                             key="why_metric")
    x = R.explain(eng, ev, m_sel)
    k = x["kind"]
    unit = "" if k == "pct" else " per 90"
    pct = (f"{abs(x['total_pct']):.0%} {'lower' if x['total'] < 0 else 'higher'}" if k == "rate" and pd.notna(x["total_pct"])
           else ("points" if k == "pct" else ""))
    st.markdown(
        "<div class='why3'>"
        f"<div class='wc'><div class='lab'>Now, {esc(I.LEAGUE_SHORT.get(ev.player.league, ev.player.league))} "
        f"{esc(ev.player.season)}</div><div class='v'>{R.fmt(x['current'], k)}<small>{unit}</small></div></div>"
        f"<div class='wc'><div class='lab'>Total adjustment</div><div class='v'>"
        f"{('+' if x['total'] > 0 else '') + R.fmt(x['total'], k).replace('-', chr(8722))}<small>{unit}</small></div>"
        f"<div class='sub'>{esc(pct)}</div></div>"
        f"<div class='wc hi'><div class='lab'>Projected, Championship</div><div class='v'>{R.fmt(x['pred'], k)}"
        f"<small>{unit}</small></div></div></div>", unsafe_allow_html=True)
    a, b = st.columns([1.25, 1], gap="large")
    with a:
        st.markdown("<div class='eyebrow' style='margin-bottom:6px'>Why it changes</div>", unsafe_allow_html=True)
        shown = [d for d in x["drivers"] if d["share"] >= 0.05][:3]
        items = "".join(f"<li><span class='tag'>{esc(R.size_word(d['share']).capitalize())}</span>"
                        f"{esc(short_reason(d, x))}</li>" for d in shown)
        if x["same_league"]:
            items += "<li class='muted'>Already a Championship player, so there is no league change adjustment.</li>"
        st.markdown(f"<ul class='whyl'>{items}</ul>", unsafe_allow_html=True)
    with b:
        st.markdown("<div class='eyebrow' style='margin-bottom:6px'>80% range</div>", unsafe_allow_html=True)
        conf = x["confidence"]
        if pd.notna(x["hist_mae"]) and x["hist_n"]:
            miss = f"Similar past projections missed by {R.fmt(x['hist_mae'], k)}{unit} on average (n={int(x['hist_n'])})."
        else:
            miss = "No backtest error for this case."
        if x["same_league"]:
            miss += " Same league projections are not backtested; that figure comes from moves between leagues."
        dots = meter(conf).split("<b>")[0] + "</span>"
        st.markdown(interval_html(x) + f"<div class='rel'>{dots} <b>{esc(R.REL_LABEL.get(conf, conf))}</b> "
                    f"<span class='muted'>({esc(conf)} confidence)</span>"
                    f"<div class='muted' style='margin-top:4px'>{esc(miss)}</div></div>", unsafe_allow_html=True)
    with st.expander("View technical methodology"):
        rows_ = [{"step": "Current", "value": x["current"], "change": np.nan}]
        prev = x["current"]
        names = {d["key"]: d["name"] for d in x["drivers"]}
        for r_ in x["running"]:
            rows_.append({"step": {"regression": "Regression", "support": "Underlying", "league_change": "League change",
                                   "age": "Age"}.get(r_["key"], r_["key"].title()), "value": r_["value"],
                          "change": r_["value"] - prev})
            prev = r_["value"]
        rows_.append({"step": "Projection", "value": x["pred"], "change": np.nan})
        steps = pd.DataFrame(rows_)
        c1_, c2_ = st.columns([1, 1.1], gap="large")
        with c1_:
            plot(C.waterfall(steps, k), key="wf", config={"displayModeBar": False})
            st.caption("Running per 90 values are exact at each point, but each bar's size depends on the order of "
                       "the steps, because the model adds effects on a log scale (logit for percentages), not in per 90.")
        with c2_:
            tr = []
            for d in x["drivers"]:
                tr.append(f"<tr><td><b>{esc(d['name'])}</b><br><span class='muted'>{esc(d['text'])}</span></td>"
                          f"<td class='n'>{d['t']:+.3f}</td><td class='n'>{d['share']:.0%}</td></tr>")
            st.markdown(f"<div class='card'><table class='steps'><tr><td class='muted'>Driver</td>"
                        f"<td class='n muted'>Model scale</td><td class='n muted'>Share</td></tr>{''.join(tr)}"
                        f"<tr class='fin'><td>Total</td><td class='n'>{x['total_t']:+.3f}</td><td></td></tr></table></div>",
                        unsafe_allow_html=True)
        st.markdown(
            f"* **Scale.** The model works on {'log(per 90 + small constant)' if k == 'rate' else 'logit of the percentage'}. "
            "Driver contributions add up exactly on that scale; share is each driver's part of the total movement "
            "(absolute values). Only the start and end values are converted to per 90.\n"
            "* **Regression toward typical output** combines the intercept, his rate, minutes and position terms with "
            "his league's own level: the projection if he stayed in his league. The ridge penalty spreads the level "
            "between the intercept and the league terms, so those parts are not reported separately.\n"
            "* **League change** is the Championship projection minus the same projection staying in his league. League "
            "effects are estimated jointly from every move and shrunk toward no change; they are never taken from one "
            "transition alone.\n"
            f"* **Validation.** {x['why']}\n"
            + (f"* **Historical error.** {x['hist_scope']}: average miss {R.fmt(x['hist_mae'], k)}, carrying his "
               f"current number over missed {R.fmt(x['hist_carry_mae'], k)} (n={int(x['hist_n'])})."
               if pd.notna(x["hist_mae"]) else ""))
else:
    st.info("Nothing can be projected for this player and role.")

# ---------------------------------------------------------------------------
# Benchmarks and uncertainty
# ---------------------------------------------------------------------------
rifc_all = R.rifc_players(eng, groups)
section("Where he would sit in the Championship",
        "Each row on its own per 90 scale. Band: middle half of Championship players in the role; tick: median.")
pick = []
if not rifc_all.empty:
    names = rifc_all["player_name"].tolist()
    pick = st.multiselect("RIFC players shown (observed, latest Championship season)", names, default=names[:3],
                          max_selections=4, key=f"rifc_pick_{role}")
rifc = rifc_all[rifc_all["player_name"].isin(pick)] if pick else rifc_all.iloc[0:0]
strip_rows, int_rows = [], []
for m in projected:
    r = t.loc[m]
    strip_rows.append({"metric": m, "kind": r["kind"], "dist": R.positional_dist(eng, m, groups).to_numpy(),
                       "pred": r["pred"], "lo80": r["lo80"], "hi80": r["hi80"], "current": r["current"],
                       "pctl": r.get("pctl_uslc"),
                       "rifc": [(x.player_name, getattr(x, f"{m}_p90")) for x in rifc.itertuples()
                                if pd.notna(getattr(x, f"{m}_p90"))]})
    int_rows.append({"metric": m, "kind": r["kind"], "pred": r["pred"], "lo80": r["lo80"], "hi80": r["hi80"],
                     "lo50": r.get("lo50"), "hi50": r.get("hi50"), "current": r["current"],
                     "hist_mae": r.get("hist_mae"), "hist_n": r.get("hist_n")})
a, b = st.columns([1.25, 1], gap="large")
with a:
    fig = C.percentile_strips(strip_rows, role, season_b)
    if fig is not None:
        plot(fig, key="strips", config={"displayModeBar": False})
with b:
    st.markdown("<div class='eyebrow'>Uncertainty</div>", unsafe_allow_html=True)
    fig = C.interval_chart(int_rows)
    if fig is not None:
        plot(fig, key="intervals", config={"displayModeBar": False})
    st.caption("Thick bar: 50% range. Thin bar: 80% range. Past error: average miss on past moves in the closest group "
               "with 20+ moves.")

# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------
section("Minutes scenario", "Season totals as minutes change. His per 90 projection stays the same; only the totals "
        "and the width of the range move.")
gx = np.arange(450, 3151, 50)
scen = [m for m in (["goals", "assists", "goal_contributions"] if role in ("Center forward", "Winger", "Attacking midfielder")
                    else [x for x in R.PRIMARY.get(role, []) if METRICS[x].kind == "rate"][:3]) if m in projected]
series = [{"label": METRICS[m].label, "curve": R.minutes_curve(eng, ev, m, gx)} for m in scen]
series = [s_ for s_ in series if s_["curve"] is not None]
a, b = st.columns([2, 1], gap="large")
with a:
    if series:
        plot(C.scenario_chart(series, minutes), key="scen", config={"displayModeBar": False})
with b:
    tiles = []
    for m in scen:
        r = t.loc[m]
        if pd.notna(r.get("pred_season")):
            tiles.append(f"<div class='pc' style='margin-bottom:10px'><div class='lab'>{esc(METRICS[m].label)} at "
                         f"{int(minutes):,} min</div><div class='big'>{r['pred_season']:.1f}</div><div class='rng'>"
                         f"80% range {r['lo80_season']:.1f} to {r['hi80_season']:.1f}</div></div>")
    st.markdown("".join(tiles), unsafe_allow_html=True)
st.caption("The model has no learned effect of starting versus coming off the bench, and team attacking effects did not "
           "improve accuracy on past moves, so neither is charted. Fewer minutes widen the range because a short "
           "season's per 90 is noisier.")

# ---------------------------------------------------------------------------
# RIFC comparison and scouting notes
# ---------------------------------------------------------------------------
cmp_all = R.compare(eng, ev, rifc_all) if not rifc_all.empty else pd.DataFrame()
section("Against current RIFC options", "His projection against their observed Championship numbers.")
a, b = st.columns(2, gap="large")
with a:
    card("Comparison", R.comparison_summary(cmp_all, ev) if not cmp_all.empty
         else ["No current RIFC players in this role with 450+ minutes."])
    try:
        st.page_link("views/compare_rifc.py", label="Open the full comparison", icon=":material/compare_arrows:")
    except Exception:
        pass
with b:
    card("Projected strengths (High or Medium confidence)", S["strengths"])
    card("Statistical concerns", S["concerns"])
card("Video scouting priorities", S["video"])
na = R.NOT_IN_DATA.get(role) or []
if na:
    st.caption("Not in the Wyscout data, so not projected for this role: " + ", ".join(na) + ".")

# ---------------------------------------------------------------------------
# Full table
# ---------------------------------------------------------------------------
with st.expander("Full projection table"):
    rows = []
    for r in ev.table.to_dict("records"):
        k = r["kind"]
        has = pd.notna(r.get("pred", np.nan))
        f = lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else R.fmt(v, k)
        rows.append({
            "Metric": r["Metric"], f"Now ({player.league})": f(r.get("current")),
            "Championship per 90": f(r.get("pred")) if has else "",
            "80% range": f"{f(r.get('lo80'))} to {f(r.get('hi80'))}" if has else "",
            f"Total at {int(minutes):,}": (f"{r['pred_season']:.1f}" if has and k == "rate" else ""),
            "USLC pctl": int(round(r["pctl_uslc"])) if has and pd.notna(r.get("pctl_uslc")) else None,
            "RIFC adjusted": (f(r.get("rifc")) if pd.notna(r.get("rifc", np.nan)) else ("neutral" if has else "")),
            "Confidence": r["confidence"], "Status": STATUS_TEXT.get(r["status"], r["status"]),
            "Past error": f(r.get("hist_mae")) if has else "",
            "Error basis": (f"{r.get('hist_scope')}, n={int(r.get('hist_n', 0))}" if has else ""),
            "Why": r.get("why", "")})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                 column_config={"USLC pctl": st.column_config.ProgressColumn("USLC pctl", min_value=0, max_value=100,
                                                                              format="%d"),
                                "Why": st.column_config.TextColumn("Why", width="large")})

# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
section("Next steps")
a, b, c = st.columns([2, 1, 1], vertical_alignment="bottom")
note_txt = a.text_input("Shortlist note", "", placeholder="Optional note for the shortlist")
if b.button("Add to shortlist", type="primary", width="stretch"):
    R.shortlist_add(uid, role, note_txt)
    st.toast(f"{player.name} added to the shortlist.")
html = report.build(eng, ev, cmp_all, club=cs)
c.download_button("Download one page report", html, width="stretch",
                  file_name=f"{player.name.replace(' ', '_').replace('.', '')}_{player.season}_projection.html", mime="text/html",
                  help="Opens in your browser. Print, then Save as PDF, for a one page PDF.")
