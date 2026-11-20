from __future__ import annotations

from datetime import date

import streamlit as st

from engine import config
from engine.monitor import brief as B
from engine.monitor import store
from engine.monitor.ui_helpers import get_ctx, monitor_ready
from ui import discovery_table, get_engine, get_identity, setup, stale_warning

setup("Daily Brief")
st.title("Daily brief")
st.caption("Only what is new or changed since the previous brief, ordered by what needs a decision. Source facts, our records and our "
           "reading are labelled separately. A quiet day gives a short brief.")
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

hist = store.query("SELECT brief_date, generated_at FROM rm_briefs ORDER BY brief_date DESC LIMIT 30")
c1, c2 = st.columns([1, 3], vertical_alignment="bottom")
if c1.button("Build / refresh today's brief", type="primary"):
    with st.spinner("Writing the brief"):
        B.build_brief(ctx)
    st.rerun()
if hist.empty:
    st.info("No brief has been written yet. Press the button, or let the daily scan write one.")
    st.stop()
dates = [str(d)[:10] for d in hist["brief_date"]]
pick = c2.selectbox("Brief", dates, format_func=lambda d: d + ("  (today)" if d == str(date.today()) else ""))
row = store.query("SELECT markdown, generated_at FROM rm_briefs WHERE brief_date = ?", [pick])
md = row["markdown"].iloc[0]
st.caption(f"Written {str(row['generated_at'].iloc[0])[:16]}. Also saved as a file in {config.DATA_DIR / 'briefs'}.")
st.markdown(md)
st.download_button("Download this brief (Markdown)", md, file_name=f"rifc_brief_{pick}.md", mime="text/markdown")
try:
    st.page_link("views/inbox.py", label="Open the Recruitment Inbox", icon=":material/inbox:")
except Exception:
    st.caption("Open the Recruitment Inbox from the menu.")
