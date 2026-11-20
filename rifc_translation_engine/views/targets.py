from __future__ import annotations

import numpy as np
import pandas as pd
import streamlit as st

from engine import discovery as D
from engine import identity as I
from engine import recruit as R
from engine.schema import METRICS, USLC
from ui import (banner, card, discovery_table, esc, get_engine, get_identity, meter, open_profile, pill, section,
                setup, stale_warning)

setup("Potential Targets")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

st.markdown("<div class='eyebrow'>Potential targets</div>", unsafe_allow_html=True)
st.title("Players who meet a recruitment priority")
st.caption("Set the role, the requirements and what each one is measured against. Players land in one of three groups, "
           "each with the exact reason. Requirements are never loosened quietly; near matches are shown separately. "
           "No fit score, availability, fee or wage information: those are not in the data.")

idn = get_identity(eng)
T = discovery_table(eng)
PL = idn.players.set_index("uid")
have = sorted({k.partition("+")[0] for k in eng.models})
R.shortlist_migrate(idn)

# ---------------------------------------------------------------------------
# RIFC context (team level only)
# ---------------------------------------------------------------------------
ctx = R.rifc_team_context()
with st.expander(f"RIFC team context, StatsBomb {ctx.get('season', '')}" if ctx else "RIFC team context", expanded=False):
    if ctx:
        a, b = st.columns(2)
        with a:
            card(f"Highest ranked areas (top fifth of {ctx['n']})", ctx["strengths"])
        with b:
            card("Lowest ranked areas", ctx["weaknesses"])
        st.caption("Team level numbers per match, ranked so that 1st is best. They describe how the team played. They "
                   "do not show which position needs a signing, and team data never feeds a player's projection except "
                   "where the Team Context Test adopted it (pass completion %, provisional).")
    else:
        st.info("No StatsBomb team data imported.")
    st.caption("There is no League One or MLS Next Pro team data, so players from those leagues are projected for an "
               "average Championship team, without adjusting for the team they come from.")

# ---------------------------------------------------------------------------
# 1. Requirements
# ---------------------------------------------------------------------------
section("1. Recruitment priority", "Mandatory criteria decide the groups. Preferences are reported, never required.")
roles = list(R.ROLES)
c1, c2, c3 = st.columns([1, 1.6, 1])
role = c1.selectbox("Role", roles, key="tg_role")
presets = D.PRIORITY_PRESETS.get(role, {})
pname = c2.selectbox("Start from", list(presets) + ["Blank"], key=f"tg_pre_{role}")
rifc_min = c3.number_input("RIFC reference: minimum minutes", 0, 3000, 900, 90, key="tg_rifc_min",
                           help="RIFC players below this are left out of RIFC benchmarks: a short season's per 90 is "
                                "noisy.")
pool_m = [m for m in dict.fromkeys(R.PRIMARY[role] + R.ROLES[role]["metrics"]) if m in have]
lab2m = {METRICS[m].label: m for m in pool_m}
ref_labels = list(D.REFS.values())
lab2ref = {v: k for k, v in D.REFS.items()}
start = presets.get(pname, []) if pname != "Blank" else [D.Criterion(pool_m[0], "uslc_median")]
init = pd.DataFrame([{"Metric": METRICS[c.metric].label, "Measured against": D.REFS[c.ref],
                      "Value": float(c.value) if c.ref in ("uslc_pctl", "value") else None,
                      "Mandatory": c.mandatory} for c in start if c.metric in have],
                    columns=["Metric", "Measured against", "Value", "Mandatory"])
init = init.reset_index(drop=True)
if init.empty and pool_m:
    init.loc[0] = [METRICS[pool_m[0]].label, D.REFS["uslc_median"], None, True]
ed = st.data_editor(init, num_rows="dynamic", width="stretch", hide_index=True, key=f"tg_ed_{role}_{pname}",
                    column_config={
                        "Metric": st.column_config.SelectboxColumn(options=list(lab2m), required=True, width="medium"),
                        "Measured against": st.column_config.SelectboxColumn(options=ref_labels, required=True,
                                                                             width="medium"),
                        "Value": st.column_config.NumberColumn(help="Percentile for 'Championship percentile (choose)'; "
                                                                    "per 90 for 'Custom per 90 value'. Ignored "
                                                                    "otherwise.", format="%.2f"),
                        "Mandatory": st.column_config.CheckboxColumn(default=True)})
crit = []
for r in ed.dropna(subset=["Metric", "Measured against"]).itertuples():
    ref = lab2ref[r[2]]
    val = r.Value if pd.notna(r.Value) else (50.0 if ref == "uslc_pctl" else 0.0)
    crit.append(D.Criterion(lab2m[r.Metric], ref, float(val), bool(r.Mandatory)))
if not any(c.mandatory for c in crit):
    st.info("Mark at least one criterion as mandatory.")
    st.stop()

rifc = D.rifc_reference(eng, R.role_groups(role), rifc_min)
bench = D.benchmarks(eng, R.role_groups(role))
season_b = R.latest_season(eng)
bars = [D.resolve(c, bench, rifc, role, season_b) for c in crit]
lines = "".join(f"<li>{'<b>Mandatory</b>' if c.mandatory else 'Preference'}: {esc(D.describe(c, b))}</li>"
                for c, b in zip(crit, bars))
st.markdown(f"<div class='card'><h4>Benchmarks used</h4><ul>{lines}</ul></div>", unsafe_allow_html=True)
if any(c.ref.startswith("rifc") for c in crit):
    st.caption("RIFC benchmarks are what our players produced (observed). Candidate numbers are projections, which are "
               "pulled toward the average, so clearing an observed RIFC number is a high bar. A modelled expectation for "
               "our own players is not offered: the backtest tests moves between leagues, so same league projections "
               "are not validated.")
missing_bars = [METRICS[c.metric].label for c, b in zip(crit, bars) if b.value is None and c.mandatory]
if missing_bars:
    banner(f"No benchmark for: {', '.join(missing_bars)}. Change the reference point; nobody can meet a requirement "
           "without a benchmark.", "exp")

# ---------------------------------------------------------------------------
# 2. Pool and evidence
# ---------------------------------------------------------------------------
section("2. Player pool and evidence standard")
ALL_LEAGUES = sorted(T["league"].unique())
DEF = [x for x in ("USL League One", "MLS Next Pro") if x in ALL_LEAGUES] or ALL_LEAGUES
x1, x2 = st.columns([2.2, 1.3], vertical_alignment="bottom")
leagues = x1.multiselect("Leagues (of his latest eligible season)", ALL_LEAGUES, default=DEF, key="tg_lg") or ALL_LEAGUES
pool = x2.selectbox("Pool", ["Current recruitment pool", "Historical and unverified pool", "All players"],
                    key="tg_pool")
y1, y2, y3, y4 = st.columns(4, vertical_alignment="bottom")
min_min = y1.number_input("Minimum minutes", 0, 4000, 900, 90, key="tg_min")
max_age = y2.number_input("Maximum age", 15, 45, 45, 1, key="tg_age")
tol = y3.slider("Near match: within", 0, 30, 15, 1, format="%d%%", key="tg_tol",
                help="How far below a mandatory bar a player can be (relative) to count as a near match.") / 100
max_short = y4.selectbox("Near match: requirements missed", [1, 2], key="tg_short", help="At most this many.")
z1, z2 = st.columns(2)
conservative = z1.toggle("Whole 80% range must clear the bar", False, key="tg_cons",
                         help="Stricter: uses the low end of the projection range for every mandatory criterion.")
allow_low = z2.toggle("Allow Low confidence in Meets requirements", False, key="tg_low",
                      help="Off: Meets and Near matches need High or Medium confidence on every mandatory metric; "
                           "Low confidence players go to Exploratory targets.")

le = I.latest_eligible(idn, min_min)
cand = T[T["rec_key"].isin(set(le["rec_key"])) & T["position_group"].isin(R.role_groups(role))
         & T["league"].isin(leagues) & ~T["uid"].isin(set(PL.index[PL["is_rifc_now"]]))].copy()
cand["recency"] = cand["uid"].map(PL["recency"])
cur = cand["uid"].map(PL["recent"]).astype(bool)
if pool == "Current recruitment pool":
    cand = cand[cur]
elif pool == "Historical and unverified pool":
    cand = cand[~cur]
cand = cand[cand["age_at_season"].isna() | (cand["age_at_season"] <= max_age)]
res = D.classify(cand, crit, bars, tol, max_short, conservative, allow_low)

# ---------------------------------------------------------------------------
# 3. Results
# ---------------------------------------------------------------------------
G = ["Meets requirements", "Near matches", "Exploratory targets"]
n = {g: int((res["group"] == g).sum()) for g in G}
section(f"3. Results from {len(cand):,} players in the pool")
counts = st.columns(3)
for c_, g in zip(counts, G):
    c_.markdown(f"<div class='pc'><div class='lab'>{g}</div><div class='big'>{n[g]}</div></div>", unsafe_allow_html=True)
if n["Meets requirements"] == 0:
    causes = D.exclusion_causes(cand, crit, bars, conservative, allow_low)
    card("Nobody meets every mandatory requirement. Biggest causes", causes or ["No players in the pool."])

sort_opts = {f"{METRICS[c.metric].label} (projected)": f"{c.metric}_proj" for c in crit if c.mandatory}
sort_opts.update({"Preferences met": "n_prefs", "Minutes played": "minutes", "Age (youngest first)": "age_at_season"})
s1, s2 = st.columns([1.5, 3])
sort_by = s1.selectbox("Sort by", list(sort_opts), key="tg_sort")
s2.caption("Sorting only orders each list; it is not a ranking of suitability.")
sl = R.shortlist()
sl_keys = set(sl["key"]) if not sl.empty else set()
verified = I.verified_uids()


def video_areas(r) -> list[str]:
    out = []
    g, xg, mins = r.get("goals_now"), r.get("xg_now"), r["minutes"]
    if pd.notna(g) and pd.notna(xg):
        gt, xt = g * mins / 90, xg * mins / 90
        if gt - xt >= 3:
            out.append(f"Finishing: {gt:.0f} goals from {xt:.1f} xG. Check whether the shot quality repeats.")
        elif xt - gt >= 3:
            out.append(f"Finishing: {gt:.0f} goals from {xt:.1f} xG. Chance quality is there; review the misses.")
    if role in ("Center forward", "Center back") and pd.notna(r.get("aerials_won_now")):
        out.append(f"Aerial duels: {r['aerials_won_now']:.2f} won per 90"
                   + (f" at {r['aerial_win_pct_now']:.0f}%" if pd.notna(r.get("aerial_win_pct_now")) else "")
                   + ". Check timing and the service he competes against.")
    if role in ("Winger", "Center forward", "Attacking midfielder") and pd.notna(r.get("succ_dribbles_now")):
        out.append(f"1v1 play: {r['succ_dribbles_now']:.2f} successful dribbles per 90. Check where and against whom.")
    if role in ("Fullback", "Center back", "Central or defensive midfielder") and pd.notna(r.get("def_duel_win_pct_now")):
        out.append(f"Defending: {r['def_duel_win_pct_now']:.0f}% of defensive duels won. Check body shape and decisions "
                   "when isolated.")
    if mins < 1500:
        out.append(f"Sample: {int(mins):,} minutes. Confirm the role he played and why minutes were limited.")
    if r["league"] != USLC:
        out.append(f"Level: built from {r['league']} numbers. Check how his actions hold up against the stronger teams.")
    return out[:4]


GROUP_HEAD = {"Meets requirements": ("Clears every mandatory requirement", "val"),
              "Near matches": ("Misses a requirement by a small margin", "exp"),
              "Exploratory targets": ("Numbers qualify, evidence is weaker", "exp")}
tabs = st.tabs([f"{g} ({n[g]})" for g in G])
SHOW = 25
for tab, g in zip(tabs, G):
    with tab:
        sub = res[res["group"] == g].sort_values(sort_opts[sort_by], ascending=sort_by.startswith("Age"),
                                                  na_position="last")
        if g == "Exploratory targets":
            banner("<b>Exploratory.</b> These players clear or nearly clear the bars on the numbers, but at least one "
                   "mandatory metric is Low confidence, not validated or missing. Use them to decide whom to watch, not "
                   "to rank or decide.", "exp")
        if sub.empty:
            st.markdown("<div class='kv'>Nobody in this group.</div>", unsafe_allow_html=True)
            continue
        for _, r in sub.head(SHOW).iterrows():
            p_ = PL.loc[r["uid"]]
            age = f"{r['age_at_season']:.0f}" if pd.notna(r["age_at_season"]) else "n/a"
            parts = []
            if r["why"]:
                parts.append(("Clears", r["why"]))
            if r["short"]:
                parts.append(("Misses (mandatory)", r["short"]))
            if r["weak"]:
                parts.append(("Weaker evidence", r["weak"]))
            if r["prefs"]:
                parts.append((f"Preferences ({r['n_prefs']} met)", r["prefs"]))
            body = "".join(f"<div><div class='eyebrow'>{esc(h)}</div><ul>{''.join(f'<li>{esc(x)}</li>' for x in xs)}"
                           f"</ul></div>" for h, xs in parts)
            vid = "".join(f"<li>{esc(x)}</li>" for x in video_areas(r))
            tags = pill(GROUP_HEAD[g][0], GROUP_HEAD[g][1])
            if r["uid"] in sl_keys:
                tags += " " + pill("Shortlisted", "rifc")
            if bool(r.get("display_only")):
                tags += " " + pill("Source: partial dataset", "exp")
            club_txt = "Current club verified" if r["uid"] in verified else "Current club unverified"
            last = ("" if p_["last_season"] == r["season"] else
                    f" | last recorded {p_['last_season']} {I.LEAGUE_SHORT.get(p_['last_league'], p_['last_league'])}")
            st.markdown(
                f"<div class='tcard'><div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'>"
                f"<div class='nm'>{esc(p_['display_name'])}</div><div>{tags} &nbsp; {meter(r['reliability'])}</div></div>"
                f"<div class='meta'>Season used: {esc(r['season'])} {esc(r['league'])}, {esc(r['team'])} | "
                f"{esc(r['position_group'])} | age {age} | {int(r['minutes']):,} min{esc(last)} | {esc(club_txt)} | "
                f"{esc(p_['recency'])}</div>"
                f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px'>{body}"
                f"<div><div class='eyebrow'>Video priorities</div><ul>{vid}</ul></div></div></div>",
                unsafe_allow_html=True)
            a, b, c = st.columns([1, 2.2, 1], vertical_alignment="bottom")
            if a.button("Open profile", key=f"tg_open_{g}_{r['uid']}", width="stretch"):
                open_profile(r["uid"], r["rec_key"])
            note = b.text_input("Note", "", key=f"tg_note_{g}_{r['uid']}", label_visibility="collapsed",
                                placeholder="Shortlist note (optional)")
            if c.button("Add to shortlist", key=f"tg_add_{g}_{r['uid']}", width="stretch", type="primary"):
                R.shortlist_add(r["uid"], role, note or f"{g}: {pname}")
                st.toast(f"{p_['display_name']} added to the shortlist.")
        if len(sub) > SHOW:
            st.caption(f"Showing the first {SHOW} of {len(sub)}.")
