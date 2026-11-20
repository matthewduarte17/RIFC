from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import streamlit as st

from engine import recruit as R
from engine.monitor import brief as B
from engine.monitor import inbox as IB
from engine.monitor import store
from engine.monitor.classify import AVAILABILITY_CLOSING, EVENT_TYPES
from engine.monitor.profile import inference_for
from engine.monitor.ui_helpers import get_ctx, monitor_ready
from ui import discovery_table, esc, get_engine, get_identity, open_profile, setup, stale_warning

setup("Recruitment Inbox")
st.title("Recruitment inbox")
st.caption("Public announcements (roster decisions, options, releases, signings, loans) connected to players already in this "
           "database. Source facts, our records and the system's inferences are kept apart. Your decisions are kept across scans.")

monitor_ready()
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()
idn = get_identity(eng)
try:
    disc = discovery_table(eng)
except Exception:
    disc = None
ctx = get_ctx(eng, idn, disc)

# ---- status line and scan controls ---------------------------------------------------------------------------
h = B.source_health()
c1, c2, c3 = st.columns([3, 1.2, 1.2], vertical_alignment="center")
last = h["last_scan"]
c1.markdown(f"**Last completed scan:** {h['last_success'] or 'never'}" + (f" &nbsp;|&nbsp; last attempt: {last['status']} at {last['at']}" if last else ""))
if c2.button("Scan now", type="primary", help="Reads all sources now. Takes about a minute."):
    from engine.monitor.scan import run_scan
    with st.spinner("Reading sources"):
        s = run_scan(trigger="app", ctx=ctx, echo=False)
    st.success(f"{s.get('status')}: {s['new_items']} new items, {s['new_events']} new events, {s['updated_events']} updated.") \
        if s.get("status") in ("ok", "partial") else st.error(f"Scan {s.get('status')}: {s.get('error') or 'see Monitor Status'}")
if c3.button("Build today's brief"):
    with st.spinner("Writing the brief"):
        B.build_brief(ctx)
    st.switch_page("views/brief.py")
if h["stale"]:
    st.warning(h["stale"] + " Use Scan now, or set up the automatic daily scan on Monitor Status.")
if h["problems"]:
    st.warning("Source problems: " + "; ".join(h["problems"]) + ". Coverage is incomplete until fixed. Details on Monitor Status.")
for w in ctx.warnings:
    st.warning(w)

with st.expander("Read one announcement link"):
    a, b_ = st.columns([4, 1], vertical_alignment="bottom")
    link = a.text_input("Address of an announcement page", key="rm_link", placeholder="https://www.uslchampionship.com/news_article/show/...")
    if b_.button("Read it") and link.strip():
        from engine.monitor.scan import scan_link
        s = scan_link(link.strip(), ctx=ctx, echo=False)
        st.info(f"{s.get('status')}: {s['new_events']} new events from this page." if s.get("status") == "ok"
                else f"Could not read it: {[x['error'] for x in s['sources']]}")

ev = IB.load_events()
if ev.empty:
    st.info("The inbox is empty. The first scan fills it (Scan now). Items older than 45 days are ignored unless you read a link by hand.")
    st.stop()
ev = IB.sort_for_review(IB.enrich(ev, ctx))
ev["announced"] = pd.to_datetime(ev["announced_at"]).dt.strftime("%Y-%m-%d")

# ---- filters --------------------------------------------------------------------------------------------------
st.markdown("### Filters")
f1, f2, f3, f4 = st.columns(4)
leagues = sorted({x for x in ev["league"].dropna().unique()})
f_league = f1.multiselect("League", leagues)
f_pos = f2.multiselect("Position", sorted(ev["position_group"].dropna().unique()))
types = [t for t in EVENT_TYPES if t in set(ev["event_type"])]
f_type = f3.multiselect("Announcement type", types, format_func=lambda t: EVENT_TYPES[t])
f_status = f4.multiselect("Review status", IB.STATUSES, default=["New", "Review", "Monitor"])
g1, g2, g3, g4 = st.columns(4)
f_sl = g1.selectbox("Shortlist", ["All", "On shortlist", "Not on shortlist"])
f_fresh = g2.multiselect("Data freshness", ["Current season", "1 season old", "2+ seasons old", "No profile"])
f_match = g3.selectbox("Match", ["All", "Matched to a player", "Match needs review", "News lead (not in database)"])
ages = ev["age_at_season"].dropna()
f_age = g4.slider("Age (at his latest season)", 15, 45, (15, 45)) if len(ages) else (15, 45)
h1, h2 = st.columns([3, 2])
q = h1.text_input("Search name or club")
hide_closing = h2.checkbox("Hide signings, extensions and options exercised for players I do not follow", True,
                           help="These are availability updates. They stay in the data and show for shortlisted players.")

v = ev
if f_league: v = v[v["league"].isin(f_league)]
if f_pos: v = v[v["position_group"].isin(f_pos)]
if f_type: v = v[v["event_type"].isin(f_type)]
if f_status: v = v[v["inbox_status"].isin(f_status)]
if f_sl == "On shortlist": v = v[v["shortlisted"]]
if f_sl == "Not on shortlist": v = v[~v["shortlisted"]]
if f_fresh: v = v[v["freshness"].isin(f_fresh)]
if f_match == "Matched to a player": v = v[v["match_status"] == "matched"]
if f_match == "Match needs review": v = v[v["match_status"] == "review"]
if f_match == "News lead (not in database)": v = v[v["match_status"] == "unmatched"]
if len(ages) and f_age != (15, 45): v = v[v["age_at_season"].between(*f_age)]
if q.strip():
    t = q.strip().lower()
    v = v[v["player_text"].str.lower().str.contains(t, na=False) | v["club_text"].fillna("").str.lower().str.contains(t) |
          v["display_name"].fillna("").str.lower().str.contains(t)]
if hide_closing:
    follow = ctx.followed()
    v = v[~(v["event_type"].isin(AVAILABILITY_CLOSING) & ~v["shortlisted"] & ~v["uid"].isin(follow))]

st.markdown(f"### {len(v)} item(s) <span class='muted'>of {len(ev)}</span>", unsafe_allow_html=True)
if v.empty:
    st.info("Nothing matches these filters.")
    st.stop()

tbl = pd.DataFrame({
    "Priority": v["tier"], "Status": v["inbox_status"],
    "Player": [d if isinstance(d, str) else p for d, p in zip(v["display_name"], v["player_text"])],
    "Announcement": v["type_label"], "Club (source)": v["club_text"], "League": v["league"], "Pos": v["position_group"],
    "Age": v["age_at_season"], "Min": v["minutes"], "Data": v["freshness"],
    "Shortlist": v["shortlisted"].map({True: "Yes", False: ""}),
    "Match": v["match_status"].map({"matched": "Matched", "review": "NEEDS REVIEW", "unmatched": "Lead"}),
    "Announced": v["announced"], "Sources": v["n_sources"], "Updated": v["updated_flag"].map({True: "UPDATED", False: ""}),
}).reset_index(drop=True)
st.caption("Priority is a reading order from visible factors, not a fit rating. Select a row to see the evidence.")
sel = st.dataframe(tbl, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="rm_tbl",
                   column_config={"Age": st.column_config.NumberColumn(format="%.0f"), "Min": st.column_config.NumberColumn(format="%.0f")})
d1, d2 = st.columns(2)
d1.download_button("Download these rows (CSV)", IB.to_csv(v), file_name=f"rifc_recruitment_inbox_{datetime.now():%Y%m%d}.csv", mime="text/csv")
d2.download_button("Download everything (CSV)", IB.to_csv(ev), file_name=f"rifc_recruitment_inbox_all_{datetime.now():%Y%m%d}.csv", mime="text/csv")

rows = sel.selection.rows if sel and sel.selection else []
r = None
if rows:
    r = v.iloc[rows[0]]
else:                                   # a link such as ...?event=12 opens one item directly
    try:
        want = int(st.query_params.get("event", ""))
        hit = ev[ev["event_id"] == want]
        r = hit.iloc[0] if len(hit) else None
    except (TypeError, ValueError):
        r = None
if r is None:
    st.stop()
eid = int(r["event_id"])
card = r["card"] if isinstance(r["card"], dict) and r["card"].get("available") else None
srcs = r["sources"] or []

st.divider()
st.markdown(f"## {(card or {}).get('name') or r['player_text']} <span class='muted'>— {r['type_label']}</span>", unsafe_allow_html=True)
if r["updated_flag"]:
    st.warning(f"Updated since you last looked: {r['update_note']}")
if r["prior_decision"]:
    st.info(r["prior_decision"])

col_a, col_b, col_c = st.columns(3)
with col_a:
    st.markdown("#### The source confirms")
    st.markdown(f"**Type:** {r['type_label']}  \n**Club named:** {r['club_text'] or 'not read'}  \n**Player as written:** {esc(r['player_text'])}  \n"
                f"**Announced:** {r['announced']}  \n**Reported by:** {r['n_sources']} source(s)")
    for s in srcs:
        st.markdown(f"- [{s['title'][:90]}]({s['url']}) ({s['source']}, published {s['published'] or 'date not given'}, found {s['discovered']})")
        st.caption(f"“{s['quote']}”" + ("" if s["body_status"] in ("ok", "not needed") else f" — article text: {s['body_status']}"))
    st.caption(f"Name read by pattern ({r['extraction']}): check the wording above.")
with col_b:
    st.markdown("#### Our database records")
    if card:
        rec = card.get("record") or {}
        st.markdown(f"**{card['name']}** — {card.get('position') or 'position not recorded'}  \n"
                    f"**Latest record:** {rec.get('league','')} {rec.get('season','')}, {rec.get('team','')}, "
                    f"{int(rec['minutes']):,} min" + (f", age {rec['age_at_season']:.0f} that season" if rec.get("age_at_season") else "") if rec else "No season record.")
        beh = card.get("seasons_since_last_record")
        st.markdown(f"**Data as of:** season {card['last_season']} (his league's latest in the data: {card.get('league_latest_season')}). "
                    + ("This is current-season data." if beh == 0 else f"His last record is **{beh} season(s) older** than his league's latest; the news may postdate it." if beh else ""))
        if rec.get("partial_dataset"):
            st.caption("His latest season comes from a partial dataset (profiles and discovery only).")
        cs = card["club_status"]
        st.markdown(f"**Current club:** {cs['status']}" + (f" ({cs['current_club']}, verified {cs['verified_on']})" if cs.get("verified") else " — only a manual verification sets this"))
        if card.get("contract_expiry"):
            st.markdown(f"**Contract expiry in our data:** {card['contract_expiry']} ({card['contract_source']})")
        sl = card.get("shortlist")
        if sl:
            added = sl["added_at"]
            st.markdown(f"**Shortlist:** Yes, {sl['role']}" + (f" — {sl['note']}" if sl["note"] else "") + f" (added {added})")
        else:
            st.markdown("**Shortlist:** not on the shortlist")
        prj = card["projection"]
        if prj.get("rows"):
            pt = pd.DataFrame([{"Metric": x["metric"], "Now (per 90)": x["now"], "Projected Championship": x["proj"],
                                "80% range": (f"{x['lo80']:.2f} to {x['hi80']:.2f}" if x["lo80"] is not None else ""),
                                "Confidence": x["conf"], "Status": x["status"]} for x in prj["rows"]])
            st.dataframe(pt, hide_index=True, width="stretch", column_config={
                "Now (per 90)": st.column_config.NumberColumn(format="%.2f"), "Projected Championship": st.column_config.NumberColumn(format="%.2f")})
        for lim in prj.get("limits", []):
            st.caption("Limit: " + lim)
        if card["identity_flags"]:
            st.caption("Identity flags: " + "; ".join(card["identity_flags"]))
    else:
        st.info("Not in our database. This is a news lead: no statistical profile is shown or invented.")
        if r["match_status"] == "review":
            st.caption("Possible players are listed under 'The system infers'.")
with col_c:
    st.markdown("#### The system infers")
    st.markdown(f"**Review priority: {r['tier']}** (reading order, not a fit rating)")
    for f in r["factors"]:
        st.markdown(f"- {f}")
    for t in inference_for(r["event_type"], bool(r["shortlisted"])):
        st.markdown(f"- _{t}_")
    st.markdown(f"**Match:** {r['match_status']}" + (f", {r['match_grade']}" if r["match_grade"] else ""))
    for m in json.loads(r["match_reasons"] or "[]"):
        st.caption(m)

# ---- match answer -------------------------------------------------------------------------------------------
cands = json.loads(r["candidates_json"] or "[]")
if r["match_status"] == "review":
    st.markdown("#### Which player is this?")
    st.caption("Names alone never link a player. Choose one only if you are sure, or say none of them.")
    opts = {c["uid"]: f"{c['name']} — {c['last_club']}, {c['last_league']} {c['last_season']} ({c['name_level']} name, club: {c['club_level']})" for c in cands}
    pick = st.radio("Candidates", list(opts), format_func=lambda u: opts[u], key=f"cand_{eid}") if opts else None
    for c in cands:
        if c["uid"] == pick:
            for t in c["reasons"] + c["flags"]:
                st.caption(t)
    k1, k2 = st.columns(2)
    if pick and k1.button("Yes, this is the same player", key=f"conf_{eid}"):
        IB.confirm_match(eid, pick)
        st.rerun()
    if k2.button("None of these (keep as a news lead)", key=f"rej_{eid}"):
        IB.reject_match(eid)
        st.rerun()
elif r["match_status"] == "matched" and r["match_grade"] != "Confirmed by you":
    if st.button("This is not that player", key=f"notthis_{eid}"):
        IB.reject_match(eid, r["uid"])
        st.rerun()

# ---- your decision --------------------------------------------------------------------------------------------
st.markdown("#### Your decision")
n1, n2 = st.columns([1.2, 3])
status = n1.radio("Status", IB.STATUSES, index=IB.STATUSES.index(r["inbox_status"]), horizontal=True, key=f"st_{eid}")
note = n2.text_input("Note", r["user_note"] or "", key=f"note_{eid}")
b1, b2, b3, b4 = st.columns(4)
if b1.button("Save decision", type="primary", key=f"save_{eid}"):
    IB.set_status(eid, status, note)
    st.rerun()
if card and b2.button("Open player profile", key=f"prof_{eid}"):
    open_profile(r["uid"])
if card and not card.get("shortlist"):
    with b3.popover("Add to shortlist"):
        roles = list(R.ROLES)
        role = st.selectbox("Role", roles, index=roles.index(R.DEFAULT_ROLE.get(card["position_group"], roles[0])), key=f"role_{eid}")
        sn = st.text_input("Shortlist note", f"Added from inbox: {r['type_label']} ({r['announced']})", key=f"sn_{eid}")
        if st.button("Add", key=f"add_{eid}"):
            IB.add_to_shortlist(ctx, r["uid"], role, sn)
            st.rerun()
elif card:
    b3.caption("On the shortlist (change it on Recruitment Shortlist)")
if r["updated_flag"] and b4.button("Mark update as seen", key=f"seen_{eid}"):
    IB.acknowledge_update(eid)
    st.rerun()
