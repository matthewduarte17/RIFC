from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import teamcontext as TC
from ui import card, df_show, get_engine, setup, stale_warning

setup("Team Context Test")
st.title("Team context test")
st.caption("Does knowing the destination club's StatsBomb profile improve player projections on past moves? "
           "The neutral model is never changed by this page.")

eng = get_engine()
stale_warning(eng)
if eng is None:
    st.stop()
st.markdown(TC.__doc__.split("Rules fixed before the test was run (6 Nov 2026):", 1)[1].split("The neutral model")[0])

saved = TC.load_saved()
if saved and (saved.get("fingerprint") != eng.fingerprint or saved.get("team_fingerprint") != TC.team_fingerprint()):
    st.warning("Player or team data changed since this test ran. Run it again.")
if st.button("Run the test", type="primary"):
    with st.spinner("Refitting the neutral model season by season"):
        saved = TC.run_and_save(eng)
if not saved:
    st.info("Not run yet.")
    st.stop()

res = pd.DataFrame(saved["results"])
d = saved["details"]
eff = saved.get("effects", {})
card("Result", [f"Trained on moves into {', '.join(map(str, d.get('train_years', [])))}, tested on moves into "
                f"{d.get('test_year')}; {len(res)} metrics tested.",
                f"Adopted: {', '.join(eff) or 'none'}." + (" Treat as provisional: with this many metrics tested, one "
                                                            "could pass by chance; it needs another season to confirm."
                                                            if eff else ""),
                "Everything else: the RIFC projection equals the neutral Championship projection, and RIFC's team "
                "numbers are shown as context only."])
cols = ["label", "model reads", "team feature", "train moves", "test moves", "MAE neutral", "MAE team adjusted",
        "MAE positional baseline", "error change %", "P(adjusted better)", "decision", "why", "test moves by transition"]
df_show(res[[c for c in cols if c in res]].round(3).rename(columns={"label": "Metric"}))
st.caption("MAE: average absolute miss per 90. Positional baseline: Championship positional average from earlier seasons. "
           "Error change below zero means the team adjusted version was more accurate.")
