from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import identity as I
from ui import banner, card, esc, get_engine, get_identity, open_profile, pill, section, setup, stale_warning

setup("Identity Review")
eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()
idn = get_identity(eng)
A = idn.audit
st.markdown("<div class='eyebrow'>Identity review</div>", unsafe_allow_html=True)
st.title("One player, one profile")
st.caption("How season rows were joined into players, what is uncertain, and your manual decisions. Profiles use the "
           "corrections here straight away; the translation model only changes when a rule is applied and the model "
           "is refitted.")

lv = A.get("levels", {})
cards = [("Player profiles", f"{A['player_ids']:,}", f"from {A['season_rows']:,} season rows"),
         ("Season entries folded", f"{A['season_entries_folded']:,}",
          f"{A['search_entries_before']:,} season entries became {A['players_outfield']:,} outfield players"),
         ("Verified", f"{lv.get('Verified', 0) + lv.get('Confirmed by scout', 0):,}",
          "multi season, all identity evidence agrees"),
         ("Single season", f"{lv.get('Single season', 0):,}", "one record, nothing to join"),
         ("Uncertain or probable", f"{lv.get('Uncertain', 0) + lv.get('Probable', 0):,}", "listed below for review")]
st.markdown("<div class='pc-grid'>" + "".join(
    f"<div class='pc'><div class='lab'>{esc(a)}</div><div class='big'>{esc(b)}</div><div class='rng'>{esc(c)}</div></div>"
    for a, b, c in cards) + "</div>", unsafe_allow_html=True)
card("Audit", [
    f"Links are the model's own ({'identical' if A['same_links_as_model'] else 'DIFFERENT, rebuild needed'}): "
    f"{A['model_identities']:,} model identities; {A['duplicate_profile_rows_ignored']} duplicate Wyscout profile rows "
    "ignored (same season stats under two profiles).",
    f"{A['clash_identities']} model identities hold two clubs in the same league season. One export lists a player "
    f"once, so these are different people: split into {A['clash_identities'] + A['clash_extra_profiles']} profiles. "
    f"They touch {A.get('pairs_touching_split_identities', 0)} of the model's season pairs "
    f"({A.get('cross_pairs_touching_split_identities', 0)} cross league).",
    f"{A['multi_league_same_season']} players played in two competitions in one season (loans and MLS NEXT Pro "
    "call ups); shown as separate records, never averaged.",
    f"{A['same_name_separate']} pairs of profiles share a name and are kept apart; {A['same_name_close_age']} of them "
    "have similar ages and no single deciding field, listed below.",
    f"Model season pairs: {A.get('model_cross_pairs', 0):,} cross league, {A.get('model_within_pairs', 0):,} same league. "
    "The identity layer does not change them."])

section("Needs review", "Uncertain: two clubs in one league season, or 1,500+ minutes in two leagues in one season. "
        "Probable: minor disagreements (height, missing birth year, name spelling).")
rv = idn.review
if rv.empty:
    st.markdown("<div class='kv'>Nothing to review.</div>", unsafe_allow_html=True)
for r in rv.itertuples():
    p_ = idn.players.set_index("uid").loc[r.uid]
    st.markdown(f"<div class='tcard'><div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'>"
                f"<div class='nm'>{esc(r.player)}</div><div>{pill(r.level, 'exp')} <span class='muted'>{esc(r.uid)}"
                f"</span></div></div><div class='meta'>{esc(r.seasons)}</div><div class='kv'>{esc(r.flags)}</div></div>",
                unsafe_allow_html=True)
    a, b, c = st.columns([1, 1, 2.2])
    if a.button("Open profile", key=f"ir_open_{r.uid}", width="stretch"):
        open_profile(r.uid)
    if b.button("Confirm identity", key=f"ir_conf_{r.uid}", width="stretch",
                help="Records that you checked this player and the records belong together."):
        I.save_decision("confirm", [r.uid], "Checked on Identity Review")
        st.rerun()
    if r.flags.startswith("Split"):
        if c.button("Apply this split to the model (then refit)", key=f"ir_split_{r.uid}", width="stretch"):
            ok = I.apply_split_to_model(idn, int(p_["model_pid"]), "Two clubs in one league season; split from the "
                                        "Identity Review page.")
            st.toast("Rule written. Refit on the Translation Model page to apply it." if ok else "Rule already exists.")

section("Same name, kept as separate players")
sim = idn.similar
if not sim.empty:
    close = sim[sim["needs_review"]]
    if not close.empty:
        banner(f"{len(close)} pair(s) share a name and a similar age with no field that clearly separates them. They "
               "stay separate unless you link them.", "exp")
        for r in close.itertuples():
            a, b = st.columns([3, 1], vertical_alignment="center")
            a.markdown(f"<div class='kv'><b>{esc(r.name)}</b>: {esc(r.a)} <b>vs</b> {esc(r.b)}</div>",
                       unsafe_allow_html=True)
            if b.button("Same player: link profiles", key=f"ir_link_{r.uid_a}_{r.uid_b}", width="stretch"):
                I.save_decision("link", [r.uid_a, r.uid_b], "Linked on Identity Review (profiles only)")
                st.rerun()
    st.dataframe(sim.rename(columns={"name": "Name", "a": "Player A", "b": "Player B",
                                     "kept_apart_because": "Kept apart because"})[["Name", "Player A", "Player B",
                                                                                  "Kept apart because"]],
                 hide_index=True, width="stretch")

section("Decisions", "Profile level decisions, newest last. Removing one restores the automatic result.")
dec = I._decisions()
if not dec:
    st.markdown("<div class='kv'>No manual decisions yet.</div>", unsafe_allow_html=True)
for i, d in enumerate(dec):
    a, b = st.columns([4, 1], vertical_alignment="center")
    a.markdown(f"<div class='kv'><b>{esc(d['action'])}</b> {esc(', '.join(d['uids']))}: {esc(d.get('note', ''))} "
               f"({esc(d['at'])})</div>", unsafe_allow_html=True)
    if b.button("Undo", key=f"ir_undo_{i}"):
        I.remove_decision(i)
        st.rerun()
rules = I.model_rules()
st.caption(f"Model rules in data/identity_overrides.json: {len(rules)} ("
           + "; ".join(r.get("player", "") for r in rules) + "). They change the model only after a refit.")
