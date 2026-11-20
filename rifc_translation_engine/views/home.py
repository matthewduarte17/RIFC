from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import db
from engine import teamcontext as TC
from engine import teamstats as T
from ui import card, df_show, get_engine, setup, stale_warning

setup("Overview")
st.title("Data and model status")
eng = get_engine()
stale_warning(eng)

from engine import datasets as DS  # noqa: E402
st.markdown("### Player data (Wyscout)")
rows = []
for d in DS.load():
    rows.append({"League": d.league, "Season": d.season, "Files": len(d.imports),
                 "Rows": int(d.imports["n_rows"].sum()), "Use": d.level, "Validation": d.status,
                 "Note": ("Excluded: " + d.headline) if d.level == "Excluded" and d.headline else d.headline})
df_show(pd.DataFrame(rows))
try:
    st.page_link("views/data_library.py", label="Manage datasets in the Data Library", icon=":material/folder_open:")
except Exception:
    st.caption("Manage datasets on the Data Library page.")
st.markdown("### Team data (StatsBomb)")
bt = T.batches()
df_show(bt[["season", "phase", "n_teams", "active", "notes"]] if not bt.empty else bt)
if eng is not None:
    c = eng.pairs[eng.pairs["pair_type"] == "cross"]
    saved = TC.load_saved()
    card("Model", [f"{len(c)} cross league moves, {(eng.pairs['pair_type'] == 'within').sum()} same league season pairs.",
                   f"{len(eng.models)} metric models fitted; backtest on "
                   f"{len(next(iter(eng.backtest_preds.values()))) if eng.backtest_preds else 0} moves per metric.",
                   "Team context adopted for: " + (", ".join(saved.get("effects", {})) or "none")
                   if saved else "Team context test not run."])
