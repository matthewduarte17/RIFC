from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from engine import db
from engine.ingest import (BUILDERS, ImportOptions, auto_map, commit, frame_to_mapping, mapping_to_frame,
                           read_file, sha256, target_options, validate)
from engine.schema import DATASET_TYPES, PROVIDERS, USLC
from ui import df_show, setup

setup("Import")
st.title("Import data")
st.caption("Originals are copied untouched to data/raw and every source row is stored as JSON. "
           "Nothing in your existing scouting files is changed.")

c1, c2 = st.columns([1, 1])
dtype = c1.selectbox("Dataset type", list(DATASET_TYPES), format_func=lambda k: DATASET_TYPES[k][0])
provider = c2.selectbox("Data provider", PROVIDERS,
                        help="Metrics are only compared within the same provider unless the definition is "
                             "standard (goals, assists, shots).")

with st.expander("Age reference (for linking players across files)", expanded=provider == "Wyscout"):
    st.markdown("Wyscout and some other platforms export a player's **current** age even for past seasons. "
                "Pick how the age column in these files was measured.")
    age_ref = st.radio("Age column is", ["As of the season", "As of the export date"], horizontal=True,
                       index=1 if provider == "Wyscout" else 0,
                       help="Wyscout exports show today's age for every season, so export date is the default.")
    export_date = st.date_input("Export date", value=pd.Timestamp.today()) if age_ref.endswith("date") else None

files = st.file_uploader("CSV or XLSX files", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
if not files:
    st.info("Tip: export one file per league per season (for example 'USL League One 2024.xlsx'). "
            "Set league and season for each file below if the export has no such columns.")
    st.stop()

known_leagues = db.query("SELECT DISTINCT league FROM player_seasons UNION SELECT DISTINCT league FROM team_seasons")
known_leagues = sorted(x for x in known_leagues["league"].dropna().tolist())
default_leagues = sorted(set(known_leagues) | {USLC, "USL League One", "MLS Next Pro", "MLS"})


def guess_season(name):
    m = re.search(r"(20\d{2})\s*[-_/]\s*(\d{2,4})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)[-2:]}"
    m = re.search(r"(20\d{2})", name)
    return m.group(1) if m else ""


def guess_league(name):
    n = name.lower().replace("_", " ")
    for lg in sorted(default_leagues, key=len, reverse=True):
        if lg.lower() in n:
            return lg
    if any(k in n for k in ("league one", "league 1", "usl1", "usl l1", "usl1 ")):
        return "USL League One"
    if "championship" in n or "uslc" in n:
        return USLC
    return ""


# ---- Per file settings ------------------------------------------------------
loaded = []
for f in files:
    data = f.getvalue()
    sheets = read_file(data, f.name)
    with st.expander(f"{f.name}", expanded=len(files) == 1):
        a, b, c = st.columns([1, 1, 1])
        sheet = a.selectbox("Sheet", list(sheets), key=f"sheet_{f.name}")
        raw = sheets[sheet]
        league = b.text_input("League for the whole file (if no league column)", guess_league(f.name),
                              key=f"lg_{f.name}")
        season = c.text_input("Season for the whole file (if no season column)", guess_season(f.name),
                              key=f"ss_{f.name}")
        st.caption(f"{len(raw):,} rows, {raw.shape[1]} columns")
        df_show(raw.head(5))
    loaded.append({"file": f, "data": data, "sheet": sheet, "raw": raw, "league": league.strip() or None,
                   "season": season.strip() or None})

# ---- Column mapping (one editor per distinct column layout) ------------------
st.subheader("Column mapping")
st.caption("Auto detected. Check anything marked fuzzy. Unit: total = season count, p90 = per 90, pct = 0 to 100.")
layouts = {}
for item in loaded:
    sig = tuple(item["raw"].columns)
    layouts.setdefault(sig, []).append(item)

options = target_options(dtype)
mappings = {}
for i, (sig, items) in enumerate(layouts.items()):
    names = ", ".join(x["file"].name for x in items)
    st.markdown(f"**Layout {i + 1}** ({len(items)} file{'s' if len(items) > 1 else ''}): {names}")
    maps = auto_map(list(sig), dtype)
    frame = mapping_to_frame(maps, items[0]["raw"])
    edited = st.data_editor(
        frame, hide_index=True, width="stretch", key=f"map_{dtype}_{i}_{hash(sig)}",
        column_config={
            "maps to": st.column_config.SelectboxColumn(options=options, required=True),
            "unit": st.column_config.SelectboxColumn(options=["value", "total", "p90", "pct"], required=True),
            "source column": st.column_config.TextColumn(disabled=True),
            "example": st.column_config.TextColumn(disabled=True),
            "detection": st.column_config.TextColumn(disabled=True),
        })
    mappings[sig] = frame_to_mapping(edited)

# ---- Validate ----------------------------------------------------------------
st.subheader("Validation")
results = []
for item in loaded:
    opt = ImportOptions(dataset_type=dtype, provider=provider, file_league=item["league"],
                        file_season=item["season"],
                        age_reference="season" if age_ref.startswith("As of the season") else "export",
                        export_year=(export_date.year + (export_date.timetuple().tm_yday / 365.25))
                        if export_date else None)
    maps = mappings[tuple(item["raw"].columns)]
    res = BUILDERS[dtype](item["raw"], maps, opt)
    issues = validate(res, opt, sha256(item["data"]))
    results.append((item, opt, maps, res, issues))
    n_err = sum(i.level == "error" for i in issues)
    n_warn = sum(i.level == "warning" for i in issues)
    icon = "Blocked" if n_err else ("Warnings" if n_warn else "Ready")
    with st.expander(f"{icon}: {item['file'].name} ({n_err} errors, {n_warn} warnings)", expanded=bool(n_err)):
        if issues:
            df_show(pd.DataFrame([{"level": i.level, "check": i.check, "message": i.message, "rows": i.n}
                                  for i in issues]))
        if dtype == "player_season":
            prev = res.frame[["player_name", "position_raw", "position_group", "team", "league", "season",
                              "minutes", "age_at_season", "goals_p90", "assists_p90", "xg_p90"]].head(10)
            st.caption("Normalised preview")
            df_show(prev.round(3))

notes = st.text_input("Notes stored with this import (optional)")
ready = [r for r in results if not any(i.level == "error" for i in r[4])]
if st.button(f"Import {len(ready)} file(s)", type="primary", disabled=not ready):
    done = []
    for item, opt, maps, res, issues in ready:
        opt.notes = notes
        iid = commit(item["data"], item["file"].name, item["sheet"], item["raw"], res, maps, opt, issues)
        done.append(f"#{iid} {item['file'].name}")
    st.success("Imported: " + "; ".join(done) + ". Refit the model on the Translation Model page.")
    st.session_state.pop("engine", None)
