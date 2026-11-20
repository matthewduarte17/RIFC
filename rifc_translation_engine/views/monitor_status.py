from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import config
from engine.monitor import schedule as SCH
from engine.monitor import store
from engine.monitor.ai import available as ai_available, month_spend
from engine.monitor.sources import load_sources
from engine.monitor.ui_helpers import monitor_ready
from ui import df_show, get_engine, get_identity, setup

setup("Monitor Status")
st.title("Recruitment monitor status")
monitor_ready()
cfg = store.load_config()

st.markdown("### Sources and coverage")
st.caption("Failed or blocked sources are shown, not hidden. A source that shows nothing new is not the same as a source that failed.")
state = store.query("SELECT * FROM rm_source_state")
rows = []
for s in load_sources(cfg):
    x = state[state["source_id"] == s.id]
    rows.append({"Source": s.name, "On": "Yes" if s.enabled else "No",
                 "Last status": (x["last_status"].iloc[0] if len(x) else "not run yet"),
                 "Last success": (str(x["last_success"].iloc[0])[:16] if len(x) and pd.notna(x["last_success"].iloc[0]) else ""),
                 "Failures in a row": int(x["consecutive_failures"].iloc[0]) if len(x) else 0,
                 "Items last scan": int(x["items_last"].iloc[0]) if len(x) else 0,
                 "Problem": (x["last_error"].iloc[0] if len(x) and x["last_error"].iloc[0] else ""),
                 "What was verified": s.verified, "Note": s.note})
df_show(pd.DataFrame(rows))
if not cfg["google_news"]["enabled"]:
    st.info("Google News is off. It would add wide headline coverage, but its robots.txt disallows automated clients and its feed "
            "notice limits use to personal feed readers. To opt in, set google_news.enabled to true in monitor_config.json (in your data folder).")

st.markdown("### Coverage by club")
st.caption("One row per club: what is configured and verified, the last successful page check, failures, and what is missing. This is a list "
           "of what we can see, not a claim that every announcement is caught: a club with no verified page or feed is not covered.")
from engine.monitor import directory as DIR
cov = DIR.coverage_table()
if cov.empty:
    st.info("No club directory yet. Run: python -m engine.monitor directory build")
else:
    lg = st.multiselect("League", sorted(cov["League"].unique()), default=sorted(cov["League"].unique()), key="cov_lg")
    only_gaps = st.checkbox("Only clubs with a failure or something missing", False)
    v = cov[cov["League"].isin(lg)]
    if only_gaps:
        v = v[(v["Missing coverage"] != "none listed") | (v["Failing watches"] > 0) | (v["Problems"] != "")]
    st.dataframe(v, hide_index=True, width="stretch")
    st.download_button("Download coverage (CSV)", v.to_csv(index=False), file_name="rifc_monitor_coverage_by_club.csv", mime="text/csv")
    cur = cov[~cov["Status"].str.startswith("future")]
    st.caption(f"{len(cur)} current clubs listed. League news page verified for {int(cur['Configured and verified'].str.contains('league news').sum())}; "
               f"league roster page readable for {int(cur['Configured and verified'].str.contains('league roster').sum())}; "
               f"club website pages verified for {int(cur['Configured and verified'].str.contains('club news|club roster').sum())}.")

st.markdown("### Page watching (changedetection.io)")
from engine.monitor import watch as WATCH
ws = WATCH.status(cfg)
if not ws["installed"]:
    st.info("Not installed. It is optional and installs only after you agree. It would use about 550 MB, runs only while a scan runs "
            "(not a background service), costs nothing, and needs the Mac awake only during the scan. In a terminal: "
            "python -m engine.monitor watch setup")
else:
    st.write(f"Installed ({ws['executable']}); watching is **{'on' if ws['enabled'] else 'off'}**.")
w = store.query("SELECT watch_id, role, last_status, last_success, consecutive_failures, n_changes, last_error FROM rm_watch ORDER BY watch_id")
if len(w):
    df_show(w)
else:
    st.caption("No pages are being watched yet.")

st.markdown("### Coverage of players")
eng = get_engine()
if eng is not None:
    idn = get_identity(eng)
    cov = idn.records.groupby(["league", "season"]).agg(players=("uid", "nunique")).reset_index()
    df_show(cov)
    st.caption("Announcements are matched only against these league seasons (datasets at 'Full use' or 'Profiles and discovery only'; "
               "excluded datasets are not used). There is no college player data here: college names stay news leads. "
               "Clubs outside these leagues (for example MLS first teams) also stay news leads.")

st.markdown("### Scans")
df_show(store.query("SELECT scan_id, started_at, finished_at, status, trigger FROM rm_scans ORDER BY scan_id DESC LIMIT 15"))
last = store.query("SELECT summary_json FROM rm_scans ORDER BY scan_id DESC LIMIT 1")
if len(last):
    sm = json.loads(last["summary_json"].iloc[0] or "{}")
    if sm.get("warnings"):
        with st.expander(f"{len(sm['warnings'])} warning(s) in the last scan"):
            for w in sm["warnings"]:
                st.write("- " + w)

st.markdown("### Automatic daily scan")
stt = SCH.status()
app_dir = str(Path(__file__).resolve().parents[1])
home = str(config.DATA_DIR.parent)
if not stt["supported"]:
    st.info("Automatic scheduling uses macOS launchd, so it is only available on a Mac. You can still scan from the app or the command line.")
else:
    st.write(f"Installed: **{'Yes' if stt['installed'] else 'No'}**" + (f", daily at {stt.get('time')}" if stt["installed"] else ""))
    if stt["installed"] and stt.get("app_dir") != app_dir:
        st.warning("The schedule points at a different app folder than this one. Press Install to update it.")
    t = st.time_input("Time", value=pd.Timestamp(f"{cfg['schedule']['hour']:02d}:{cfg['schedule']['minute']:02d}").time())
    c1, c2 = st.columns(2)
    if c1.button("Install or update", type="primary"):
        res = SCH.install(app_dir, home, python=sys.executable, hour=t.hour, minute=t.minute)
        if res["ok"]:
            cfg["schedule"] = {"enabled": True, "hour": t.hour, "minute": t.minute}
            store.save_config(cfg)
        st.write(res["message"])
    if c2.button("Remove"):
        cfg["schedule"]["enabled"] = False
        store.save_config(cfg)
        st.write(SCH.remove()["message"])
st.caption("It runs while you are logged in. If the Mac is asleep at the set time, it runs when the Mac wakes. If the Mac is off, it runs "
           "at your next login. The app does not need to be open. A scan also runs when you start the app if the last one is over a day old.")

st.markdown("### AI reading (optional)")
ok, why = ai_available(cfg)
st.write("Off. " + why if not ok else f"On. Spent this month: ${month_spend():.2f} of a ${cfg['ai']['monthly_usd_cap']:.2f} cap.")
st.caption("Nothing here uses AI by default. Fetching, matching, filtering and ranking are ordinary code.")

st.markdown("### Log")
lf = config.DATA_DIR.parent / "logs" / "monitor.log"
if lf.exists():
    st.code("\n".join(lf.read_text().splitlines()[-40:]), language=None)
