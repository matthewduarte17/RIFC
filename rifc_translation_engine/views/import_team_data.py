from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import db
from engine import teamstats as T
from engine.normalize import norm_team
from ui import df_show, setup

setup("Import Team Data")
st.title("Import team data (StatsBomb)")
st.caption("Upload the category exports of ONE competition season together (results and xG, shooting, set pieces, "
           "passing, pressing, OBV). They are merged into one record per club after checks. Originals are kept.")

c1, c2, c3 = st.columns([1.2, 1, 1.6])
competition = c1.selectbox("Competition", ["USL Championship"],
                           help="StatsBomb team data is only used for Championship context.")
season = c2.text_input("Season (required)", "", placeholder="e.g. 2025")
phase = c3.radio("Competition phase (required)", list(T.PHASES), index=None, format_func=lambda k: T.PHASES[k])
files = st.file_uploader("StatsBomb team CSV files", type=["csv"], accept_multiple_files=True)

ps = db.load_table("player_seasons", active_only=True)
if files:
    data = [(f.name, f.getvalue()) for f in files]
    # Evidence for the season from the club list (never from the file name)
    if not ps.empty:
        df0 = T.read_csv_bytes(data[0][1])
        clubs = {norm_team(x) for x in df0["Team Name"] if str(x).lower() not in T.LEAGUE_AVG_NAMES}
        hits = []
        for s, g in ps[ps["league"] == competition].groupby("season"):
            if {norm_team(x) for x in g["team"].dropna()} == clubs:
                hits.append(s)
        st.caption("Club list check against your player data: " + (f"these clubs match the {', '.join(hits)} season exactly."
                   if hits else "no player data season has exactly these clubs.") + " The season you enter is what is stored.")
    if not season or phase is None:
        st.info("Enter the season and choose the competition phase to run the checks.")
        st.stop()
    known = {norm_team(x): x for x in ps[(ps["league"] == competition) & (ps["season"] == season)]["team"].dropna().unique()}
    b = T.build_batch(data, competition, season.strip(), phase, known)
    st.markdown("### Files")
    df_show(pd.DataFrame([{"file": v[0], "category": T.FILE_LABELS[k], "rows": len(v[2])} for k, v in b.files.items()]))
    st.markdown("### Checks")
    chk = pd.DataFrame([c.__dict__ for c in b.checks])
    df_show(chk)
    dup = T.existing_batch(competition, season.strip(), phase)
    if not dup.empty:
        st.error(f"{competition} {season} ({T.PHASES[phase]}) is already imported (batch {int(dup['batch_id'].iloc[0])}). "
                 "Exclude it below first to replace it.")
    blocked = any(c.level == "error" for c in b.checks) or not dup.empty
    if b.merged is not None and not b.merged.empty:
        with st.expander("Preview (merged, full precision)"):
            df_show(b.merged.head(60))
    notes = st.text_input("Notes")
    if st.button("Import", type="primary", disabled=blocked):
        bid = T.commit_batch(b, notes)
        st.success(f"Imported as batch {bid}. Rerun the team context test on its page.")

st.markdown("### Imported team data")
bt = T.batches()
if bt.empty:
    st.caption("None yet.")
else:
    df_show(bt[["batch_id", "competition", "season", "phase", "n_teams", "active", "imported_at", "notes"]])
    a, b2 = st.columns(2)
    pick = a.selectbox("Batch", bt["batch_id"].tolist())
    if b2.button("Exclude or restore batch"):
        cur = bool(bt.set_index("batch_id").loc[pick, "active"])
        T.set_batch_active(pick, not cur)
        st.rerun()
st.caption("Future seasons: export the same six StatsBomb team views, upload them together, set the season and phase. "
           "Do not mix regular season only and combined exports for the same season.")
