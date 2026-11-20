from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from engine import db
from engine import discovery as D
from engine import identity as I
from engine import recruit as R
from engine.normalize import norm_name
from engine.schema import METRICS, POSITION_LABELS, USLC
from ui import (ACCENT, CAND, CONF_HELP, FAINT, LEAGUE_COLORS, MUTED, RIFC, banner, discovery_table, esc, get_engine,
                get_identity, meter, open_profile, pill, plot, setup, stale_warning)

setup("Recruitment Discovery")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()

st.markdown("<div class='eyebrow'>Recruitment discovery</div>", unsafe_allow_html=True)
st.title("Find players worth a closer look")
st.caption("One point per player, from his own latest eligible season, projected to the Championship with the "
           "validated translation model. Every league is measured against the same Championship benchmark. Nothing "
           "here is a fit score.")

idn = get_identity(eng)
T = discovery_table(eng)
PL = idn.players.set_index("uid")
pl = PL
have = sorted({k.partition("+")[0] for k in eng.models})
ALL_LEAGUES = sorted(T["league"].unique())
DEFAULT_LEAGUES = [x for x in ("USL League One", "MLS Next Pro") if x in ALL_LEAGUES] or ALL_LEAGUES
SS = st.session_state
roles = list(R.ROLES)
seasons_all = sorted(T["season"].unique(), reverse=True)
ages_all = T["age_at_season"].dropna()
AGE_RANGE = (int(ages_all.min()), int(ages_all.max())) if len(ages_all) else (15, 40)
REL_OPTS = ["Validated (High or Medium confidence)", "Include Low confidence", "Everything projected"]
POOL_OPTS = ["Current recruitment pool", "Historical and unverified pool", "All players"]

# ---------------------------------------------------------------------------
# Session state: every control remembers its value for the session, also after visiting other pages
# ---------------------------------------------------------------------------
DEFAULTS = {"disc_role": roles[0], "disc_leagues": DEFAULT_LEAGUES, "disc_basis": "Projected USLC",
            "disc_mode": "Current candidates", "disc_min": 900, "disc_pool": POOL_OPTS[0],
            "disc_cutoff": seasons_all[min(1, len(seasons_all) - 1)], "disc_since": 4, "disc_ver": "All",
            "disc_rel": REL_OPTS[1], "disc_status": "All players", "disc_age": AGE_RANGE, "disc_el_season": [],
            "disc_last_season": [], "disc_clubs": [], "disc_color": "League", "disc_size": True, "disc_rifc": True,
            "disc_hl": "", "disc_find": ""}
for r_ in roles:
    DEFAULTS[f"disc_view_{r_}"] = None
PERSIST = list(DEFAULTS)
for k_, v_ in DEFAULTS.items():
    if k_ not in SS:
        val = SS.get("_p_" + k_, v_)
        if val is not None:
            SS[k_] = val
SS["disc_leagues"] = [x for x in SS.get("disc_leagues", []) if x in ALL_LEAGUES]


def _all_leagues():
    SS["disc_leagues"] = list(ALL_LEAGUES)


def _reset_leagues():
    SS["disc_leagues"] = list(DEFAULT_LEAGUES)


# ---------------------------------------------------------------------------
# Eligibility: why a player is or is not on the chart
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def excluded_rows(_fp):
    x = db.query("SELECT p.name_norm, p.team, p.league, p.season, p.minutes, i.exclusion_reason FROM player_seasons p "
                 "JOIN imports i USING (import_id) WHERE NOT i.active AND NOT COALESCE(i.display_only, FALSE) "
                 "AND i.replaced_by IS NULL")
    return x


def excluded_for(uid) -> list[str]:
    ex = excluded_rows(eng.fingerprint)
    if ex.empty:
        return []
    p_ = PL.loc[uid]
    names = {norm_name(a) for a in p_["aliases"]}
    hit = ex[ex["name_norm"].isin(names)]
    if hit.empty:
        return []
    same_club = hit[hit["team"].isin(p_["clubs"])]
    h = same_club if not same_club.empty else hit
    r = h.sort_values("season").iloc[-1]
    how = "same name and club" if not same_club.empty else "same name only, may be another player"
    return [f"Has a {r['season']} {r['league']} row ({r['team']}, {int(r['minutes'] or 0):,} min) in an excluded "
            f"import, not used anywhere ({how}). Reason: {r['exclusion_reason'] or 'excluded'}"]


def candidate_record(uid, min_min, hist):
    """The record this player would be plotted from: his latest eligible season (or every season in Historical)."""
    r = T[T["uid"] == uid]
    if r.empty:
        return r
    if hist:
        return r[r["minutes"] >= min_min]
    le = I.latest_eligible(idn, min_min)
    return r[r["rec_key"].isin(set(le[le["uid"] == uid]["rec_key"]))]


def why_not(uid, st_) -> list[str]:
    """Every reason the player is not plotted under the current settings (empty list: he is plotted)."""
    p_ = PL.loc[uid]
    out = []
    if p_["is_rifc_now"]:
        return ["Current RIFC player: shown only as a reference star" + ("" if st_["show_rifc"] else
                                                                           " (RIFC players toggle is off)") + "."]
    recs = T[T["uid"] == uid]
    if recs.empty:
        return ["Goalkeeper or no position in the data: discovery covers outfield players only."] + excluded_for(uid)
    cand = candidate_record(uid, st_["min_min"], st_["hist"])
    if cand.empty:
        best = recs.sort_values("season_start").iloc[-1]
        out.append(f"Minimum minutes: his latest season ({best['season']} {best['league']}) has {int(best['minutes']):,}"
                   f" minutes; the chart needs {st_['min_min']:,}.")
        return out + excluded_for(uid)
    r = cand.sort_values("season_start").iloc[-1]
    if r["position_group"] not in st_["groups"]:
        out.append(f"Position: his {r['season']} season is as a {POSITION_LABELS.get(r['position_group'], r['position_group']).lower()}; "
                   f"the chart shows {st_['role'].lower()}s.")
    if r["league"] not in st_["leagues"]:
        out.append(f"Source league excluded: his {r['season']} season is in {r['league']}.")
    if not st_["hist"]:
        cur = bool(p_["recent"]) and str(p_["last_season"]) >= str(st_["cutoff"])
        if st_["pool"] == POOL_OPTS[0] and not cur:
            out.append(f"Pool: not in the current recruitment pool ({p_['recency']}, last recorded {p_['last_season']}).")
        if st_["pool"] == POOL_OPTS[1] and cur:
            out.append("Pool: he is in the current pool, but the historical pool is selected.")
    if p_["seasons_since"] > st_["since"]:
        out.append(f"Seasons since last appearance: {int(p_['seasons_since'])} (filter {st_['since']}).")
    if st_["el_seasons"] and r["season"] not in st_["el_seasons"]:
        out.append(f"Season outside the selected range: {r['season']}.")
    if st_["last_seasons"] and p_["last_season"] not in st_["last_seasons"]:
        out.append(f"Latest recorded season {p_['last_season']} is outside the selected range.")
    if st_["clubs"] and r["team"] not in st_["clubs"]:
        out.append(f"Club filter: his season is at {r['team']}.")
    if pd.notna(r["age_at_season"]) and not (st_["age"][0] <= r["age_at_season"] <= st_["age"][1]):
        out.append(f"Age {r['age_at_season']:.0f} is outside the age filter.")
    if st_["status"] == "Shortlisted only" and uid not in sl_keys:
        out.append("Recruitment status: not shortlisted.")
    if st_["status"] == "Not shortlisted" and uid in sl_keys:
        out.append("Recruitment status: already shortlisted.")
    if st_["ver"] != "All" and ((uid in verified) != (st_["ver"] == "Verified")):
        out.append(f"Current club verification filter: {st_['ver'].lower()} only.")
    col_ = "proj" if st_["proj"] else "now"
    for m, ax in ((st_["mx"], "X"), (st_["my"], "Y")):
        if pd.isna(r[f"{m}_now"]):
            out.append(f"Missing {ax} axis metric: {METRICS[m].label} is not in his {r['season']} data.")
        elif st_["proj"] and pd.isna(r[f"{m}_proj"]):
            out.append(f"Unavailable projected metric: {METRICS[m].label} cannot be projected from {r['league']} for "
                       f"his position (not enough past moves). Switch Numbers to Current league to see his own value.")
    if st_["proj"] and not out:
        need = {REL_OPTS[0]: 2, REL_OPTS[1]: 1, REL_OPTS[2]: 0}[st_["rel"]]
        rel_ = min(D.CONF_RANK[r[f"{st_['mx']}_conf"]], D.CONF_RANK[r[f"{st_['my']}_conf"]])
        if rel_ < need:
            out.append(f"Model reliability restriction: {r[f'{st_['mx']}_conf']} and {r[f'{st_['my']}_conf']} confidence "
                       f"on these metrics; the filter asks for {st_['rel'].lower()}.")
    if not out:
        for m, ax, mn in ((st_["mx"], "X", st_["xmin"]), (st_["my"], "Y", st_["ymin"])):
            if r[f"{m}_{col_}"] < mn:
                out.append(f"Below the {ax} axis threshold: {r[f'{m}_{col_}']:.2f} under {mn:.2f}.")
    if out:
        out += excluded_for(uid)
    if out and p_["identity_level"] in ("Uncertain", "Probable"):
        out.append(f"Identity is {p_['identity_level'].lower()}: {'; '.join(p_['flags'])}. Check Identity Review.")
    return out


def show_on_chart(uid):
    """Change only the filters that hide this player (never the model). Called before the page reruns."""
    p_ = PL.loc[uid]
    recs = T[T["uid"] == uid]
    if recs.empty:
        return
    keys_ = [k_ for k_ in SS.keys() if str(k_).startswith("disc_") and str(k_) not in ("disc_find", "disc_hl")
             and not str(k_).startswith(("disc_find_", "disc_hl_", "disc_chart", "disc_open"))]
    before = {k_: SS[k_] for k_ in keys_}
    r = recs.sort_values(["season_start", "minutes"], ascending=[False, False]).iloc[0]
    role_ = R.DEFAULT_ROLE.get(r["position_group"], SS["disc_role"])
    if r["position_group"] not in R.role_groups(SS["disc_role"]):
        SS["disc_role"] = role_
    if r["league"] not in SS["disc_leagues"]:
        SS["disc_leagues"] = SS["disc_leagues"] + [r["league"]]
    if r["minutes"] < SS["disc_min"]:
        SS["disc_min"] = int(r["minutes"] // 90 * 90)
    SS["disc_mode"] = "Current candidates"
    if not (bool(p_["recent"]) and str(p_["last_season"]) >= str(SS["disc_cutoff"])):
        SS["disc_pool"] = POOL_OPTS[2]
    SS["disc_since"] = 4
    for k_ in ("disc_el_season", "disc_last_season", "disc_clubs"):
        SS[k_] = []
    SS["disc_status"], SS["disc_ver"], SS["disc_age"] = "All players", "All", AGE_RANGE
    SS["disc_rel"] = REL_OPTS[2]
    for k_ in list(SS.keys()):
        if str(k_).startswith(("disc_xmin_", "disc_ymin_")):
            SS[k_] = 0.0
    SS["disc_pinned"] = list(set(SS.get("disc_pinned", [])) | {uid})
    names = {"disc_role": "position", "disc_leagues": "source leagues", "disc_min": "minimum minutes",
             "disc_mode": "mode", "disc_pool": "pool", "disc_since": "seasons since last appearance",
             "disc_rel": "reliability", "disc_el_season": "season shown", "disc_last_season": "latest season",
             "disc_clubs": "club", "disc_status": "recruitment status", "disc_ver": "verification", "disc_age": "age"}
    changes = []
    for k_, lab_ in names.items():
        if k_ in before and before[k_] != SS.get(k_):
            b_, a_ = before[k_], SS.get(k_)
            fmt_ = lambda x: ", ".join(map(str, x)) if isinstance(x, (list, tuple)) else str(x)
            changes.append(f"{lab_}: {fmt_(b_) or 'none'} to {fmt_(a_) or 'none'}")
    SS["_restore"] = {"uid": uid, "before": before, "changes": changes}


def restore_filters():
    rs = SS.pop("_restore", None)
    if rs:
        for k_, v_ in rs["before"].items():
            SS[k_] = v_


R.shortlist_migrate(idn)
sl = R.shortlist()
sl_keys = set(sl["key"]) if not sl.empty else set()
verified = I.verified_uids()

# ---------------------------------------------------------------------------
# Global player search: every player in the database, whatever the chart shows
# ---------------------------------------------------------------------------
gs1, gs2, gs3, gs4 = st.columns([1.1, 3.0, 0.8, 0.9], vertical_alignment="bottom")
find = gs1.text_input("Find any player", key="disc_find", placeholder="Name, surname, club or league",
                      help="Searches every player in the database, independent of the chart and its filters. Accents "
                           "and initials do not matter.")
found_uid = None
if find.strip():
    res = I.search(idn, find)
    if res.empty:
        gs2.markdown(f"<div class='kv' style='padding-bottom:10px'><b>{esc(find)}</b> is not in the imported data "
                     "(no player, club or league matches).</div>", unsafe_allow_html=True)
    else:
        lab_ = {r_.uid: f"{r_.display_name} | {r_.last_club} | {POSITION_LABELS.get(r_.last_position, r_.last_position or '')}"
                        f" | {r_.last_league} {r_.last_season}" for r_ in res.itertuples()}
        found_uid = gs2.selectbox(f"{len(res)} match{'es' if len(res) != 1 else ''}", list(lab_),
                                  format_func=lambda u: lab_[u], key=f"disc_find_pick_{abs(hash(find.lower())) % 10**8}")
        if gs3.button("Open profile", key="disc_find_open", width="stretch"):
            open_profile(found_uid)
        gs4.button("Show on chart", key="disc_find_show", width="stretch", on_click=show_on_chart, args=(found_uid,),
                   help="Adjusts only the filters that hide him: position, league, minimum minutes, pool, reliability "
                        "and other filters. The model is never changed.")

# ---------------------------------------------------------------------------
# Top controls: position, source leagues, view
# ---------------------------------------------------------------------------
c1, c2, c3 = st.columns([1, 2.3, 1.5], vertical_alignment="bottom")
role = c1.selectbox("Position", roles, key="disc_role")
with c2:
    leagues = st.pills("Source leagues", ALL_LEAGUES, selection_mode="multi", key="disc_leagues",
                       help="Changes which players are shown: the league of each player's latest eligible season (or, "
                            "in Historical seasons, of each season record).") or []
pre = D.presets(eng, role)
pre_lab = [f"{METRICS[y].label} vs {METRICS[x].label}" for x, y in pre] + ["Custom axes"]
if SS.get(f"disc_view_{role}") not in pre_lab:
    SS[f"disc_view_{role}"] = pre_lab[0]
choice = c3.selectbox("View", pre_lab, key=f"disc_view_{role}",
                      help="Presets for this position, shown only where both metrics exist in the data.")
b1, b2, b3, b4 = st.columns([0.8, 0.8, 1.9, 2.1], vertical_alignment="center")
b1.button("All leagues", on_click=_all_leagues, width="stretch")
b2.button("Reset leagues", on_click=_reset_leagues, width="stretch",
          help="Back to the default recruitment view: " + " and ".join(DEFAULT_LEAGUES) + ".")
basis = b3.segmented_control("Numbers", ["Projected USLC", "Current league"], key="disc_basis",
                             label_visibility="collapsed",
                             help="Projected: what the model expects in the Championship (per 90). Current: what he "
                                  "produced in his own league, untranslated.") or "Projected USLC"
mode = b4.segmented_control("Mode", ["Current candidates", "Historical seasons"], key="disc_mode",
                            label_visibility="collapsed",
                            help="Current candidates: one point per player. Historical seasons: every season record, "
                                 "for looking back; not a candidate list.") or "Current candidates"
proj = basis.startswith("Projected")
hist_mode = mode.startswith("Historical")
role_metrics = [m for m in dict.fromkeys(R.PRIMARY[role] + R.ROLES[role]["metrics"]) if m in have]
pool_m = role_metrics + [m for m in have if m not in role_metrics]
if choice == "Custom axes" or not pre:
    for k_, i_ in (("disc_x", 0), ("disc_y", min(1, len(pool_m) - 1))):
        if SS.get(k_) not in pool_m:
            SS[k_] = pool_m[i_]
    a, b = st.columns(2)
    mx = a.selectbox("Horizontal axis", pool_m, format_func=lambda m: METRICS[m].label, key="disc_x")
    my = b.selectbox("Vertical axis", pool_m, format_func=lambda m: METRICS[m].label, key="disc_y")
else:
    mx, my = pre[pre_lab.index(choice)]
groups = R.role_groups(role)
if not leagues:
    st.info("No source league selected. Pick at least one league, or press All leagues.")

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
latest_overall = idn.latest_season
for k_ in (f"disc_xmin_{mx}", f"disc_ymin_{my}"):
    if k_ not in SS:
        SS[k_] = 0.0
with st.expander("More filters", expanded=False):
    f1, f2, f3, f4 = st.columns(4)
    min_min = f1.number_input("Minimum minutes (eligible season)", 0, 4000, step=90, key="disc_min",
                              help="A season counts as eligible with at least this many minutes. Each player is shown "
                                   "from his own latest eligible season.")
    pool = f1.selectbox("Pool", POOL_OPTS, key="disc_pool", disabled=hist_mode,
                        help="Current: his last record is in the latest imported season of his league, and not older "
                             "than the cutoff. Historical and unverified: everyone else (older data, or missing from "
                             "his league's later data).")
    cutoff = f2.selectbox("Current pool: last recorded season from", seasons_all, key="disc_cutoff", disabled=hist_mode)
    max_since = f2.slider("Seasons since last appearance (at most)", 0, 4, key="disc_since")
    ver = f3.selectbox("Current club verification", ["All", "Verified", "Unverified"], key="disc_ver")
    rel = f3.selectbox("Reliability", REL_OPTS, key="disc_rel", disabled=not proj,
                       help=" ".join(f"{k}: {v}" for k, v in CONF_HELP.items()))
    status = f4.selectbox("Recruitment status", ["All players", "Shortlisted only", "Not shortlisted"], key="disc_status")
    age_rng = f4.slider("Age (in the season shown)", AGE_RANGE[0], AGE_RANGE[1], key="disc_age")
    g1, g2, g3, g4 = st.columns(4)
    el_seasons = g1.multiselect("Season shown", seasons_all, key="disc_el_season",
                                help="Season of the record shown (latest eligible in Current candidates).")
    last_seasons = g2.multiselect("Latest recorded season", seasons_all, key="disc_last_season",
                                  help="His most recent season in the data, eligible or not.")
    xmin = g3.number_input(f"{METRICS[mx].label}: at least", step=0.05, key=f"disc_xmin_{mx}")
    ymin = g4.number_input(f"{METRICS[my].label}: at least", step=0.05, key=f"disc_ymin_{my}")
    clubs = st.multiselect("Club (season shown)", sorted(T["team"].dropna().unique()), key="disc_clubs")

o1, o2, o3, o4 = st.columns([1.3, 1, 1, 2.2], vertical_alignment="bottom")
color_by = o1.selectbox("Colour by", ["League", "Reliability", "Recruitment status"], key="disc_color")
size_by = o2.toggle("Size by minutes", key="disc_size")
show_rifc = o3.toggle("RIFC players", key="disc_rifc", help="Reference only: not candidates, not counted.")
search = o4.text_input("Highlight on chart", key="disc_hl", placeholder="Names, separated by commas",
                       help="Labels these players if they are plotted, and explains why if they are not.")
STATE = {"role": role, "groups": groups, "leagues": leagues, "min_min": int(min_min), "hist": hist_mode, "pool": pool,
         "cutoff": cutoff, "since": max_since, "el_seasons": el_seasons, "last_seasons": last_seasons, "clubs": clubs,
         "age": age_rng, "status": status, "ver": ver, "proj": proj, "mx": mx, "my": my, "rel": rel, "xmin": xmin,
         "ymin": ymin, "show_rifc": show_rifc}

# ---------------------------------------------------------------------------
# Candidate set
# ---------------------------------------------------------------------------
col = "proj" if proj else "now"
rifc_uids = set(pl.index[pl["is_rifc_now"]])
if hist_mode:
    v = T[T["position_group"].isin(groups) & (T["minutes"] >= min_min) & T["league"].isin(leagues)].copy()
    le = I.latest_eligible(idn, min_min)
    v["is_latest"] = v["rec_key"].isin(set(le["rec_key"]))
else:
    le = I.latest_eligible(idn, min_min)
    v = T[T["rec_key"].isin(set(le["rec_key"]))].copy()
    v = v[v["position_group"].isin(groups) & v["league"].isin(leagues)]   # league of the latest eligible season only
    v["is_latest"] = True
v = v[~v["uid"].isin(rifc_uids)]
v["recency"] = v["uid"].map(pl["recency"])
v["last_season"] = v["uid"].map(pl["last_season"])
v["seasons_since"] = v["uid"].map(pl["seasons_since"])
in_current = v["uid"].map(pl["recent"]).astype(bool)
in_current &= v["last_season"].astype(str) >= str(cutoff)
if not hist_mode and pool == POOL_OPTS[0]:
    v = v[in_current]
elif not hist_mode and pool == POOL_OPTS[1]:
    v = v[~in_current]
v = v[v["seasons_since"] <= max_since]
if ver != "All":
    v = v[v["uid"].isin(verified) == (ver == "Verified")]
if el_seasons:
    v = v[v["season"].isin(el_seasons)]
if last_seasons:
    v = v[v["last_season"].isin(last_seasons)]
if clubs:
    v = v[v["team"].isin(clubs)]
v = v[v["age_at_season"].isna() | v["age_at_season"].between(*age_rng)]
if status == "Shortlisted only":
    v = v[v["uid"].isin(sl_keys)]
elif status == "Not shortlisted":
    v = v[~v["uid"].isin(sl_keys)]
n_before = len(v)
v = v[v[f"{mx}_{col}"].notna() & v[f"{my}_{col}"].notna()]
n_noproj = n_before - len(v)
v["rel"] = [min(D.CONF_RANK[a], D.CONF_RANK[b]) for a, b in zip(v[f"{mx}_conf"], v[f"{my}_conf"])]
v["rel_lab"] = v["rel"].map({3: "High", 2: "Medium", 1: "Low", 0: "None"})
if proj:
    need = {REL_OPTS[0]: 2, REL_OPTS[1]: 1, REL_OPTS[2]: 0}[rel]
    v = v[v["rel"] >= need]
v = v[(v[f"{mx}_{col}"] >= xmin) & (v[f"{my}_{col}"] >= ymin)]

le_all = I.latest_eligible(idn, 450)
rifc = T[T["rec_key"].isin(set(le_all["rec_key"])) & T["uid"].isin(rifc_uids) & T["position_group"].isin(groups)]
rifc = rifc[rifc[f"{mx}_now"].notna() & rifc[f"{my}_now"].notna()]

# filters changed by "Show on chart": say what changed, offer to undo
rs_ = SS.get("_restore")
if rs_ and rs_["changes"]:
    a_, b_ = st.columns([4, 1], vertical_alignment="center")
    a_.markdown(f"<div class='kv'>To show <b>{esc(PL.loc[rs_['uid'], 'display_name'])}</b>, these filters changed: "
                f"{esc('; '.join(rs_['changes']))}.</div>", unsafe_allow_html=True)
    b_.button("Restore filters", on_click=restore_filters, width="stretch")

# found player: on the chart or not, and why
if found_uid:
    rs = why_not(found_uid, STATE)
    nm = PL.loc[found_uid, "display_name"]
    if not rs:
        st.markdown(f"<div class='banner val'><b>{esc(nm)}</b> is on the chart and labelled.</div>",
                    unsafe_allow_html=True)
        SS["disc_pinned"] = list(set(SS.get("disc_pinned", [])) | {found_uid})
    else:
        st.markdown(f"<div class='banner exp'><b>{esc(nm)}</b> is in the database but not on this chart:<ul style='margin:6px 0 0 0'>"
                    + "".join(f"<li>{esc(x)}</li>" for x in rs) + "</ul></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Live breakdown
# ---------------------------------------------------------------------------
counts = v.groupby("league")["uid"].nunique() if not hist_mode else v.groupby("league").size()
chips = "".join(f"<span class='pill' style='margin-right:6px;border-color:{LEAGUE_COLORS.get(lg, ACCENT)}'>"
                f"{esc(lg)}: <b>{int(counts.get(lg, 0))}</b></span>" for lg in leagues)
unit = "season records" if hist_mode else "players"
tot = int(v["uid"].nunique()) if not hist_mode else len(v)
extra = (f" &nbsp; <span class='muted'>{n_noproj:,} left out: one of the two metrics could not be projected or is "
         "not in the data.</span>" if n_noproj else "")
st.markdown(f"<div style='margin:6px 0 4px 0'>{chips}<span class='kv'>&nbsp; <b>{tot:,}</b> {unit}"
            + "</span>"
            + (f" <span class='muted'>&nbsp;+ {len(rifc)} RIFC reference</span>" if show_rifc and len(rifc) else "")
            + extra + "</div>", unsafe_allow_html=True)
if hist_mode:
    banner("<b>Historical seasons.</b> Every season record with enough minutes, including old ones. Filled points are "
           "each player's latest eligible season; hollow points are earlier seasons. All points of one player open the "
           "same profile. This is not a list of current candidates.", "exp")
mlsnp_note = [lg for lg in leagues if idn.league_latest.get(lg) and str(idn.league_latest[lg]) != str(latest_overall)]
if mlsnp_note:
    st.caption(" ".join(f"{lg}: latest imported season is {idn.league_latest[lg]}, so those players' {latest_overall} "
                        "clubs are unknown." for lg in mlsnp_note))

# ---------------------------------------------------------------------------
# Chart
# ---------------------------------------------------------------------------
kx, ky = METRICS[mx].kind, METRICS[my].kind
LS = I.LEAGUE_SHORT


def fv(x, k):
    return "n/a" if pd.isna(x) else (f"{x:.1f}%" if k == "pct" else f"{x:.2f}")


def hover(d: pd.DataFrame, observed: bool) -> list[str]:
    out = []
    for r in d.itertuples():
        g = r._asdict()
        age = f"{g['age_at_season']:.0f}" if pd.notna(g["age_at_season"]) else "n/a"
        c_ = "now" if (observed or not proj) else "proj"
        tail = "observed" if (observed or not proj) else f"{g['rel_lab']} reliability" if "rel_lab" in g else ""
        out.append(f"<b>{esc(g.get('display_name') or g['player_name'])}</b><br>{esc(g['team'])} | {g['season']} "
                   f"{esc(g['league'])}<br>Age {age} | {int(g['minutes']):,} min<br>{METRICS[mx].label}: "
                   f"{fv(g[f'{mx}_{c_}'], kx)} | {METRICS[my].label}: {fv(g[f'{my}_{c_}'], ky)}<br>{tail}"
                   + ("" if g.get("is_latest", True) else "<br>earlier season")
                   + ("<br>From a partial dataset (profiles and discovery only)" if g.get("display_only") else ""))
    return out


def sizes(d):
    if not size_by:
        return 9
    return np.clip(5 + 11 * np.sqrt(d["minutes"].clip(0, 3400) / 3400), 6, 16)


q = [s_.strip() for s_ in search.split(",") if s_.strip()]
pinned = set(SS.get("disc_pinned", []))
hl_found = {}                                 # query -> matching player IDs in the whole database
for s_ in q:
    res_ = I.search(idn, s_, limit=8)
    top_ = res_[res_["display_name"].map(I.fold).str.contains(I.fold(s_), regex=False)
                | res_["aliases"].map(lambda a: any(I.fold(s_) in I.fold(x) for x in a))]
    hl_found[s_] = (top_ if not top_.empty else res_)["uid"].tolist()
hl_uids = {u for us in hl_found.values() for u in us}
lab_mask = v["uid"].isin(pinned | hl_uids)

fig = go.Figure()
if color_by == "League":
    groups_c = [(lg, v[v["league"] == lg], LEAGUE_COLORS.get(lg, ACCENT)) for lg in sorted(v["league"].unique())]
elif color_by == "Reliability":
    cmap = {"High": "#4C8DFF", "Medium": "#8FB4FF", "Low": "#5B6B85", "None": "#3A465C"}
    groups_c = [(f"{k} reliability", v[v["rel_lab"] == k], c) for k, c in cmap.items() if (v["rel_lab"] == k).any()]
else:
    groups_c = [("Shortlisted", v[v["uid"].isin(sl_keys)], CAND), ("Not shortlisted", v[~v["uid"].isin(sl_keys)],
                                                                    "#7C8BA6")]
for name, d, c in groups_c:
    for latest_flag in ([True, False] if hist_mode else [True]):
        dd = d[d["is_latest"] == latest_flag]
        if dd.empty:
            continue
        fig.add_trace(go.Scatter(
            x=dd[f"{mx}_{col}"], y=dd[f"{my}_{col}"], mode="markers",
            name=f"{name} ({len(dd)})" + ("" if latest_flag else ", earlier seasons"),
            legendgroup=name,
            marker=dict(size=sizes(dd), color=c if latest_flag else "rgba(0,0,0,0)", opacity=0.8 if latest_flag else 0.6,
                        line=dict(color="#0B1220" if latest_flag else c, width=0.6 if latest_flag else 1.4)),
            selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=0.18)),
            customdata=dd[["uid", "rec_key"]].to_numpy(), hovertext=hover(dd, False), hoverinfo="text"))
if show_rifc and not rifc.empty:
    fig.add_trace(go.Scatter(
        x=rifc[f"{mx}_now"], y=rifc[f"{my}_now"], mode="markers+text", name="RIFC reference (observed, not a candidate)",
        text=rifc["display_name"].fillna(rifc["player_name"]), textposition="top center",
        textfont=dict(color=RIFC, size=11),
        marker=dict(size=13, color=RIFC, symbol="star", line=dict(color="#0B1220", width=1)),
        customdata=rifc[["uid", "rec_key"]].to_numpy(), hovertext=hover(rifc, True), hoverinfo="text"))
hl = v[lab_mask]
if not hl.empty:
    if hist_mode:   # connect each labelled player's seasons
        for u, g in hl.sort_values("season_start").groupby("uid"):
            if len(g) > 1:
                fig.add_trace(go.Scatter(x=g[f"{mx}_{col}"], y=g[f"{my}_{col}"], mode="lines", showlegend=False,
                                         line=dict(color=CAND, width=1, dash="dot"), hoverinfo="skip"))
    fig.add_trace(go.Scatter(
        x=hl[f"{mx}_{col}"], y=hl[f"{my}_{col}"], mode="markers+text", name="Labelled players",
        text=hl["display_name"].fillna(hl["player_name"]) + (" " + hl["season"].astype(str) if hist_mode else ""),
        textposition="top center", textfont=dict(color=CAND, size=12),
        marker=dict(size=18, color="rgba(0,0,0,0)", line=dict(color=CAND, width=2.5)),
        customdata=hl[["uid", "rec_key"]].to_numpy(), hovertext=hover(hl, False), hoverinfo="text"))
bench = D.benchmarks(eng, groups)
for m, axis in ((mx, "x"), (my, "y")):
    b = bench.get(m)
    if b is not None and len(b) >= 10:
        med = float(np.median(b))
        kw = dict(line=dict(color=FAINT, dash="dot", width=1))
        if axis == "x":
            fig.add_vline(x=med, annotation_text="Championship median", annotation_position="top",
                          annotation_font=dict(color=MUTED, size=10), **kw)
        else:
            fig.add_hline(y=med, annotation_text="Championship median", annotation_position="top left",
                          annotation_font=dict(color=MUTED, size=10), **kw)
suffix = "projected Championship per 90" if proj else "per 90 in own league"
fig.update_layout(
    height=640, margin=dict(l=8, r=8, t=30, b=8), hovermode="closest", dragmode="zoom",
    xaxis=dict(title=f"{METRICS[mx].label}, {suffix}" if kx == "rate" else f"{METRICS[mx].label}",
               ticksuffix="%" if kx == "pct" else ""),
    yaxis=dict(title=f"{METRICS[my].label}, {suffix}" if ky == "rate" else f"{METRICS[my].label}",
               ticksuffix="%" if ky == "pct" else ""),
    legend=dict(orientation="h", yanchor="top", y=-0.1, x=0), clickmode="event+select")
event = plot(fig, key=f"disc_chart_{role}_{mx}_{my}_{basis}_{mode}", on_select="rerun",
             selection_mode=("points", "box", "lasso"),
             config={"modeBarButtonsToRemove": ["autoScale2d", "toggleSpikelines"]})
st.caption("Click a player, or drag a box or lasso around several, to see the detail below. "
           + ("RIFC stars are their observed Championship numbers, shown for reference; everyone else is projected. "
              if proj else "Untranslated numbers: a League One rate is not a Championship rate. ")
           + "Dotted lines: median of Championship players in this position (latest season, 900+ minutes), the same "
             "benchmark for every league.")

if pinned:
    def _clear_pins():
        SS["disc_pinned"] = []
    st.button(f"Clear {len(pinned)} pinned label{'s' if len(pinned) != 1 else ''}", on_click=_clear_pins)

# ---------------------------------------------------------------------------
# Highlight on chart: explain players who are not plotted
# ---------------------------------------------------------------------------
if q:
    plotted = set(v["uid"]) | (set(rifc["uid"]) if show_rifc else set())
    for s_, us in hl_found.items():
        if not us:
            st.markdown(f"<div class='kv'>Highlight: <b>{esc(s_)}</b> is not in the imported data.</div>",
                        unsafe_allow_html=True)
            continue
        for u in us[:4]:
            nm = PL.loc[u, "display_name"]
            if u in plotted:
                continue
            rs = why_not(u, STATE)
            a_, b_ = st.columns([4, 1], vertical_alignment="center")
            a_.markdown(f"<div class='kv'><b>{esc(nm)}</b> ({esc(PL.loc[u, 'last_club'])}, {esc(PL.loc[u, 'last_league'])} "
                        f"{esc(PL.loc[u, 'last_season'])}) is not plotted: {esc(' '.join(rs) or 'filtered out')}</div>",
                        unsafe_allow_html=True)
            b_.button("Show on chart", key=f"disc_hl_show_{u}", on_click=show_on_chart, args=(u,), width="stretch")
        if len(us) > 4:
            st.caption(f"'{s_}' matches {len(us)} players; refine the name to explain the rest.")

# ---------------------------------------------------------------------------
# Selection detail panel
# ---------------------------------------------------------------------------
pts = (event.selection.points if event and hasattr(event, "selection") else []) or []
sel_recs = list(dict.fromkeys(p["customdata"][1] for p in pts if p.get("customdata")))
if sel_recs:
    sel = T.set_index("rec_key").loc[[k for k in sel_recs if k in set(T["rec_key"])]].reset_index()
    sel = sel.drop_duplicates("uid") if not hist_mode else sel
    st.markdown(f"<div class='sec'><div class='t'>Selected ({sel['uid'].nunique()} player"
                f"{'s' if sel['uid'].nunique() != 1 else ''})</div></div>", unsafe_allow_html=True)
    a, b, c, d = st.columns([1.2, 1.2, 1.8, 1], vertical_alignment="bottom")
    if a.button("Label selected on the chart", width="stretch"):
        SS["disc_pinned"] = list(pinned | set(sel["uid"]))
        st.rerun()
    if b.button("Clear labels", width="stretch", disabled=not pinned):
        SS["disc_pinned"] = []
        st.rerun()
    note = c.text_input("Shortlist note", "", key="disc_note", label_visibility="collapsed",
                        placeholder="Shortlist note for the selected players (optional)")
    if d.button(f"Shortlist {sel['uid'].nunique()}", type="primary", width="stretch"):
        for u in sel["uid"].unique():
            R.shortlist_add(u, role, note)
        st.toast(f"Added {sel['uid'].nunique()} to the shortlist.")
        st.rerun()
    for i, r in enumerate(sel.head(12).itertuples()):
        g = r._asdict()
        p_ = pl.loc[g["uid"]]
        cs_txt = "Current club verified" if g["uid"] in verified else "Current club unverified"
        lines = []
        for m in (mx, my):
            k = METRICS[m].kind
            if pd.notna(g[f"{m}_proj"]):
                lines.append(f"<li>{METRICS[m].label}: <b>{fv(g[f'{m}_proj'], k)}</b> projected (80% range "
                             f"{fv(g[f'{m}_lo80'], k)} to {fv(g[f'{m}_hi80'], k)}), now {fv(g[f'{m}_now'], k)} in "
                             f"{esc(g['league'])}; {g[f'{m}_conf']} confidence</li>")
            else:
                lines.append(f"<li>{METRICS[m].label}: now {fv(g[f'{m}_now'], k)}; not projected</li>")
        latest_note = ("" if p_["last_season"] == g["season"] else
                       f" Last recorded: {p_['last_season']} {LS.get(p_['last_league'], p_['last_league'])} "
                       f"({esc(p_['last_club'])}).")
        tags = pill("Shortlisted", "rifc") if g["uid"] in sl_keys else ""
        if g.get("display_only"):
            tags += " " + pill("Source: partial dataset", "exp")
        st.markdown(
            f"<div class='tcard'><div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'>"
            f"<div class='nm'>{esc(p_['display_name'])}</div><div>{tags} {pill(cs_txt)}</div></div>"
            f"<div class='meta'>Season shown: {g['season']} {esc(g['league'])}, {esc(g['team'])} | "
            f"{int(g['minutes']):,} min | age {g['age_at_season']:.0f} | {esc(p_['recency'])}.{latest_note}</div>"
            f"<ul>{''.join(lines)}</ul></div>", unsafe_allow_html=True)
        if st.button("Open profile", key=f"disc_open_{g['rec_key']}"):
            open_profile(g["uid"], g["rec_key"])
    if len(sel) > 12:
        st.caption(f"Showing 12 of {len(sel)}. Narrow the selection to see the rest.")
else:
    st.markdown("<div class='kv'>No players selected.</div>", unsafe_allow_html=True)

# remember every control for the session
for k_ in PERSIST + [f"disc_xmin_{mx}", f"disc_ymin_{my}", "disc_x", "disc_y"]:
    if k_ in SS:
        SS["_p_" + k_] = SS[k_]
