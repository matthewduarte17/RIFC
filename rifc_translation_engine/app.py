"""RIFC Recruitment: player translation and team context.

Run:  streamlit run app.py   (or double click start.command)
"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="RIFC Recruitment", layout="wide")

recruitment = [
    st.Page("views/discovery.py", title="Recruitment Discovery", icon=":material/scatter_plot:", default=True),
    st.Page("views/inbox.py", title="Recruitment Inbox", icon=":material/inbox:"),
    st.Page("views/brief.py", title="Daily Brief", icon=":material/summarize:"),
    st.Page("views/targets.py", title="Potential Targets", icon=":material/target:"),
    st.Page("views/player_projection.py", title="Player Profile", icon=":material/person_search:"),
    st.Page("views/compare_rifc.py", title="Compare with RIFC", icon=":material/compare_arrows:"),
    st.Page("views/shortlist.py", title="Recruitment Shortlist", icon=":material/format_list_bulleted:"),
    st.Page("views/rifc_team_profile.py", title="RIFC Team Profile", icon=":material/shield:"),
    st.Page("views/team_benchmarks.py", title="Championship Team Benchmarks", icon=":material/leaderboard:"),
    st.Page("views/reliability.py", title="Model Reliability", icon=":material/verified:"),
]
admin = [
    st.Page("views/home.py", title="Data and Model Status", icon=":material/dashboard:"),
    st.Page("views/monitor_status.py", title="Monitor Status", icon=":material/radar:"),
    st.Page("views/import_players.py", title="Import Player Data", icon=":material/upload_file:"),
    st.Page("views/import_team_data.py", title="Import Team Data", icon=":material/upload:"),
    st.Page("views/data_library.py", title="Data Library", icon=":material/folder_open:"),
    st.Page("views/translation_model.py", title="Translation Model", icon=":material/model_training:"),
    st.Page("views/backtest.py", title="Backtest", icon=":material/history:"),
    st.Page("views/validation_audit.py", title="Validation Audit", icon=":material/fact_check:"),
    st.Page("views/identity_review.py", title="Identity Review", icon=":material/badge:"),
    st.Page("views/team_context_test.py", title="Team Context Test", icon=":material/science:"),
]
st.navigation({"Recruitment": recruitment, "Admin": admin}).run()
