from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import db
from engine.schema import METRICS
from ui import df_show, get_engine, setup

setup("Data Library")
st.title("Data library")

from engine import datasets as DS
from ui import banner, esc, pill, section

imports = db.query("SELECT import_id, active, display_only, filename, dataset_type, provider, file_league, file_season, "
                   "n_rows, imported_at, exclusion_reason, scope_note, notes FROM imports ORDER BY import_id")
if imports.empty:
    st.info("Nothing imported yet.")
    st.stop()

st.caption("One row per league and season. Every file of a league season moves together. Nothing is ever deleted "
           "here: excluded files stay in the database and in data/raw.")
with st.expander("What each use level controls"):
    m = pd.DataFrame({lv: ["Yes" if u in DS.LEVEL_USES[lv] else "" for u in DS.USES] for lv in DS.LEVELS},
                     index=DS.USES).reset_index().rename(columns={"index": "Used for"})
    st.dataframe(m, hide_index=True, width="stretch")
    st.caption("Profiles and discovery only lets you see a season's players with projections from the current model "
               "without letting that data train or test the model. Full use changes the model's data, so the fitted "
               "model is outdated until it is refitted.")

eng_ = get_engine()
dss = DS.load()
LV_KIND = {"Full use": "val", "Profiles and discovery only": "rifc", "Excluded": "exp", "Mixed": "exp"}
ST_KIND = {"Valid": "val", "Valid with warnings": "", "Invalid": "exp"}
for d in dss:
    g = d.imports
    n_players = int(db.query("SELECT COUNT(*) n FROM player_seasons WHERE import_id IN (" +
                             ",".join(map(str, g["import_id"])) + ")")["n"].iloc[0])
    last = pd.to_datetime(g["imported_at"]).max()
    reason = (g["exclusion_reason"].dropna().iloc[0] if g["exclusion_reason"].notna().any() else "")
    short = ("Excluded: " + (d.headline or reason)) if d.level == "Excluded" else \
        ("Shown in profiles and discovery only. " + (d.headline or "")) if d.level.startswith("Profiles") else ""
    st.markdown(
        f"<div class='tcard'><div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'>"
        f"<div class='nm'>{esc(d.league)} {esc(d.season)}</div><div>{pill(d.level, LV_KIND.get(d.level, ''))} "
        f"{pill(d.status, ST_KIND.get(d.status, ''))}</div></div>"
        f"<div class='meta'>{len(g)} file{'s' if len(g) != 1 else ''} | {n_players:,} player rows | last import "
        f"{last:%d %b %Y}</div>" + (f"<div class='kv'>{esc(short)}</div>" if short else "") + "</div>",
        unsafe_allow_html=True)
    with st.expander(f"Details and controls: {d.league} {d.season}"):
        icon = {"pass": "Pass", "warning": "Warning", "fail": "Fail"}
        st.dataframe(pd.DataFrame([{"Check": c.name, "Result": icon[c.level], "Detail": c.detail} for c in d.checks]),
                     hide_index=True, width="stretch", column_config={"Detail": st.column_config.TextColumn(width="large")})
        st.dataframe(g[["import_id", "filename", "n_rows", "imported_at", "exclusion_reason", "scope_note"]].rename(
            columns={"import_id": "Import", "filename": "File", "n_rows": "Rows", "imported_at": "Imported",
                     "exclusion_reason": "Reason for exclusion", "scope_note": "Note"}), hide_index=True, width="stretch")
        if d.replaced is not None and not d.replaced.empty:
            st.caption("Replaced files (kept, not used): " + "; ".join(
                f"#{r.import_id} {r.filename} (replaced by {r.replaced_by})" for r in d.replaced.itertuples()))
        st.markdown("<div class='eyebrow' style='margin-top:10px'>Replace files</div>", unsafe_allow_html=True)
        st.caption("For corrected exports, for example a capped age band re-exported in smaller pieces. The new files "
                   "are checked against the files that stay, never against the ones they replace, so no player season "
                   "is stored twice or lost. They get this season's current use level; Full use comes only after the "
                   "merged season passes validation.")
        r1, r2 = st.columns([1.4, 2.6])
        rep = r1.multiselect("Files to replace", d.imports["import_id"].tolist(), key=f"rep_{d.league}_{d.season}",
                             format_func=lambda i: d.imports.set_index("import_id").loc[i, "filename"])
        ups = r2.file_uploader("Corrected Wyscout exports", type=["xlsx", "xls", "csv"], accept_multiple_files=True,
                               key=f"up_{d.league}_{d.season}")
        if ups:
            files_ = [(u.name, u.getvalue()) for u in ups]
            pv = DS.preview_replacement(d, [int(i) for i in rep], files_)
            st.dataframe(pd.DataFrame(pv["files"]), hide_index=True, width="stretch")
            if "rows_added" in pv:
                st.markdown(f"<div class='kv'>Rows of the replaced files found again: <b>{pv['old_rows_found']}</b> of "
                            f"{pv['old_rows']}. New rows added: <b>{pv['rows_added']}</b>. Overlap with files that stay "
                            f"(skipped): {pv['overlap_with_kept']}. Season after the change: <b>{pv['merged_players']:,}"
                            f"</b> player rows.</div>", unsafe_allow_html=True)
            for pr in pv["problems"]:
                banner(esc(pr), "exp")
            okr = st.checkbox("Import these files and mark the selected files as replaced",
                              key=f"okrep_{d.league}_{d.season}", disabled=not pv["ok"] or not rep)
            if st.button("Import and replace", key=f"dorep_{d.league}_{d.season}", disabled=not okr):
                from engine.config import DATA_DIR
                ids_ = DS.commit_replacement(d, [int(i) for i in rep], files_, DATA_DIR / "uploads_tmp")
                for k_ in ("engine", "_idn_key", "_disc_key"):
                    st.session_state.pop(k_, None)
                st.toast(f"Imported as {', '.join(map(str, ids_))}. Review the checks before choosing Full use.")
                st.rerun()
        st.markdown("<div class='eyebrow' style='margin-top:10px'>Use level</div>", unsafe_allow_html=True)
        options = [lv for lv in DS.LEVELS if lv != d.level]
        a, b = st.columns([1.4, 2.6], vertical_alignment="bottom")
        target = a.selectbox("Change use level to", options, key=f"lvl_{d.league}_{d.season}")
        note = b.text_input("Reason (saved with the change)", key=f"note_{d.league}_{d.season}")
        override = ""
        blocked = False
        if target == "Full use" and d.status == "Invalid":
            banner("<b>This dataset failed validation.</b> Full use would let it train and test the model. Only do "
                   "this with a written override; it is saved with the files.", "exp")
            override = st.text_input("Override reason (required)", key=f"ovr_{d.league}_{d.season}")
            blocked = not override.strip()
        if target == "Profiles and discovery only" and d.status == "Invalid":
            st.caption("Allowed: these players' rows are shown, labelled with the issue, and never reach the model. "
                       "Players missing from the files stay missing.")
        if DS.affects_model(d.level, target):
            banner("This change alters the model's training data. The fitted model and its backtest become outdated "
                   "until you refit on the Translation Model page (a backup is made first).", "exp")
        ok = st.checkbox("I have read the checks and want to make this change", key=f"ok_{d.league}_{d.season}")
        if st.button(f"Apply: {target}", key=f"apply_{d.league}_{d.season}", type="primary",
                     disabled=not ok or blocked):
            try:
                msg = DS.set_level(d, target, note, override)
                st.session_state.pop("engine", None)
                st.session_state.pop("_idn_key", None)
                st.session_state.pop("_disc_key", None)
                st.toast(msg)
                st.rerun()
            except ValueError as e:
                st.error(str(e))

if eng_ is not None and eng_.is_stale():
    section("Model is outdated", "The data used for training changed since the last fit.")
    st.markdown("Refitting retrains every metric model, reruns the backtest and the team context test (a few "
                "minutes). The current database and fitted models are copied to the backups folder first.")
    if st.checkbox("Back up and refit now", key="refit_ok"):
        if st.button("Confirm refit", type="primary"):
            from engine.rebuild import refit
            path = DS.backup("pre_refit")
            box = st.empty()
            with st.spinner("Refitting"):
                st.session_state["engine"] = refit(progress=lambda m: box.caption(m))
            st.success(f"Refitted. Backup: {path}")

with st.expander("All imports (files)"):
    df_show(imports)

with st.expander("Remove an import"):
    st.caption("Removes the rows that came from one file. The original file copy in data/raw is kept.")
    iid = st.selectbox("Import", imports["import_id"],
                       format_func=lambda i: f"#{i} {imports.set_index('import_id').loc[i, 'filename']}")
    if st.checkbox("I understand this removes these rows from the engine database"):
        if st.button("Remove import", type="secondary"):
            db.delete_import(int(iid))
            st.session_state.pop("engine", None)
            st.success(f"Import #{iid} removed. Refit the model.")
            st.rerun()

ps = db.load_table("player_seasons", active_only=True)
if not ps.empty:
    st.subheader("Coverage: player rows by league and season (active imports)")
    cov = ps.pivot_table(index="league", columns="season", values="row_id", aggfunc="count", fill_value=0)
    df_show(cov.reset_index())

    st.subheader("Metric coverage by league (share of rows with a value)")
    rows = []
    for (lg, prov), g in ps.groupby(["league", "provider"]):
        r = {"league": lg, "provider": prov, "rows": len(g)}
        for m, meta in METRICS.items():
            r[meta.label] = round(g[f"{m}_p90"].notna().mean() * 100)
        rows.append(r)
    cov_m = pd.DataFrame(rows)
    keep = [c for c in cov_m.columns if c in ("league", "provider", "rows") or cov_m[c].sum() > 0]
    df_show(cov_m[keep])

    st.subheader("Position labels")
    pg = ps.groupby(["position_raw"], dropna=False)["position_group"].first().reset_index()
    pg["rows"] = ps.groupby(["position_raw"], dropna=False).size().values
    df_show(pg.sort_values("rows", ascending=False))
    st.caption("Positions with no group are excluded from position models. Tell me the label and the group "
               "and I can add it to engine/schema.py.")

st.subheader("Validation log")
log = db.query("SELECT v.import_id, i.filename, v.level, v.check_name, v.message, v.n_affected "
               "FROM validation_log v JOIN imports i USING (import_id) ORDER BY v.import_id")
lvl = st.multiselect("Levels", ["error", "warning", "info"], default=["warning", "error"])
df_show(log[log["level"].isin(lvl)])

eng = get_engine()
if eng is not None and eng.review is not None and not eng.review.empty:
    st.subheader("Identity review")
    st.caption("Clusters that look like two different players merged under one identity. Add a birth date or a "
               "transfer list row to separate or confirm them.")
    df_show(eng.review)

st.subheader("Team data (StatsBomb)")
from engine import teamstats as _T  # noqa: E402
_b = _T.batches()
if _b.empty:
    st.caption("No StatsBomb team data imported.")
else:
    df_show(_b[["batch_id", "season", "phase", "n_teams", "active", "imported_at", "notes"]])
    _chk = []
    for r in _b.itertuples():
        import json as _json
        for c in _json.loads(r.checks_json):
            _chk.append({"season": r.season, **c})
    df_show(pd.DataFrame(_chk))
    st.caption("Manage team batches on the Import Team Data page.")

st.subheader("Where your data is kept")
from engine import upgrade as _U  # noqa: E402
from engine.config import DATA_DIR as _DD  # noqa: E402
_ver = _U.get_meta("data_version", "not recorded")
_from = _U.get_meta("upgraded_from")
_at = _U.get_meta("upgraded_at")
st.markdown(f"Data folder: `{_DD}`  \nData version: **{_ver}**" +
            (f"  \nLast upgrade: {_at[:16].replace('T', ' ')} from version {_from}" if _at else ""))
st.caption("This folder sits outside the app folder, so installing a new ZIP never replaces it. When you start a new "
           "version, the launcher backs this folder up to backups/pre_upgrade_<date>, carries your shortlist, notes, "
           "club verifications, identity decisions, use levels and your own imports forward, checks every item, and "
           "only then switches over.")
_rep = _DD / "last_upgrade_report.txt"
if _rep.exists():
    with st.expander("Last upgrade report"):
        st.code(_rep.read_text(), language=None)
