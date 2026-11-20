from __future__ import annotations

import pandas as pd
import streamlit as st

from engine import audit, db
from ui import df_show, get_engine, setup, stale_warning

setup("Validation Audit")
st.title("Validation audit")
st.caption("Read only. Checks leakage, accuracy by transition, position and metric, and player identities. "
           "Nothing here changes the model.")

eng = get_engine()
stale_warning(eng)
if eng is None or not eng.backtest_preds:
    st.info("Fit the model and run the backtest first.")
    st.stop()

ex = db.query("SELECT import_id, filename, file_league, file_season, exclusion_reason FROM imports WHERE NOT active")
if not ex.empty:
    st.warning(f"{len(ex)} import(s) excluded from the model: " + "; ".join(
        f"#{r.import_id} {r.file_league} {r.file_season}" for r in ex.itertuples()) + ". Reason: "
        + str(ex["exclusion_reason"].iloc[0]))

TRUST_HELP = (f"Trust: Adequate = {audit.ADEQUATE}+ backtest moves, Limited = {audit.LIMITED} to "
              f"{audit.ADEQUATE - 1}, Too few to trust = under {audit.LIMITED}. Verdict: Reliable = beats carrying "
              "the current number over, within 5% of the best simple baseline, and 80% ranges cover 70% to 90%.")


def colour(v):
    if not isinstance(v, str):
        return ""
    if v.startswith("Reliable") or v == "Adequate" or v == "pass":
        return "background-color: rgba(46,125,50,0.18)"
    if v.startswith("Promising") or v == "Limited":
        return "background-color: rgba(227,178,60,0.25)"
    if v.startswith("Not reliable") or v.startswith("Too few") or v.startswith("Unknown") or v == "FAIL":
        return "background-color: rgba(176,0,32,0.15)"
    return ""


def show(df, fmt_cols=None):
    sty = df.style.map(colour).format({c: "{:.3f}" for c in (fmt_cols or [])} | {
        c: "{:+.0f}%" for c in df.columns if c.endswith("%")} | {"80% coverage": "{:.0%}"} if "80% coverage" in df
        else {}, na_rep="")
    st.dataframe(sty, hide_index=True, width="stretch")


tab1, tab2, tab3, tab4, tab5 = st.tabs(["Leakage", "By transition", "By position", "Transition x position",
                                        "Identity"])

with tab1:
    st.markdown("**Checks run on every metric and every test season.**")
    with st.spinner("Running leakage checks and the player disjoint rerun"):
        if "leak" not in st.session_state or st.session_state.get("leak_run") != eng.backtest_id:
            st.session_state["leak"] = audit.leakage_audit(eng)
            st.session_state["leak_run"] = eng.backtest_id
        checks, comp, pert = st.session_state["leak"]
    summ = checks.groupby(["check", "result"]).size().rename("checks").reset_index()
    show(summ)
    st.markdown("**Future perturbation test.** Every row from the first test season on is scrambled; predictions "
                "for that season must not move at all.")
    show(pert)
    st.markdown("**Player disjoint rerun.** Every earlier move by a test player is removed from training. If the "
                "model leaned on knowing the player, error would rise.")
    show(comp.rename(columns={"change": "MAE change %"}), ["MAE standard", "MAE player disjoint"])
    dup = audit.duplicate_moves(eng)
    st.markdown(f"**Duplicate moves** (same move under two identities): {len(dup)}")
    if not dup.empty:
        df_show(dup)
    with st.expander("Every check"):
        df_show(checks)

with tab2:
    st.caption(TRUST_HELP)
    show(audit.transition_coverage(eng))
    T = audit.breakdown(eng, ["transition"])
    tr = st.multiselect("Transitions", sorted(T["transition"].unique()), default=sorted(T["transition"].unique()))
    cols = ["metric", "transition", "n", "trust", "MAE model", "MAE carry over", "MAE best simple",
            "Model vs carry over %", "Model vs best simple %", "80% coverage", "verdict"]
    show(T[T["transition"].isin(tr)][cols], ["MAE model", "MAE carry over", "MAE best simple"])
    st.download_button("Download CSV", T.to_csv(index=False), "audit_by_transition.csv")

with tab3:
    st.caption(TRUST_HELP)
    P = audit.breakdown(eng, ["position_group"])
    st.markdown("**Verdict matrix**")
    mat = P.pivot(index="metric", columns="position_group", values="verdict")
    ns = P.groupby("position_group")["n"].first()
    mat.columns = [f"{c} (n={ns[c]})" for c in mat.columns]
    st.dataframe(mat.style.map(colour), width="stretch")
    with st.expander("Full table"):
        show(P[["metric", "position_group", "n", "trust", "MAE model", "MAE carry over", "MAE best simple",
                "Model vs carry over %", "Model vs best simple %", "80% coverage", "verdict"]],
             ["MAE model", "MAE carry over", "MAE best simple"])
    O = audit.breakdown(eng, [])
    st.markdown("**All positions and transitions pooled**")
    show(O[["metric", "model reads", "n", "Model vs carry over %", "Model vs best simple %", "80% coverage",
            "P(model beats carry over)", "verdict"]])
    st.download_button("Download CSV", P.to_csv(index=False), "audit_by_position.csv")

with tab4:
    st.caption("Most cells here are small. Read the trust column before the numbers.")
    X = audit.breakdown(eng, ["transition", "position_group"])
    c1, c2 = st.columns(2)
    t = c1.selectbox("Transition", sorted(X["transition"].unique()),
                     index=sorted(X["transition"].unique()).index("MLSNP to USLC")
                     if "MLSNP to USLC" in set(X["transition"]) else 0)
    m = c2.selectbox("Metric", list(dict.fromkeys(X["metric"])))
    show(X[(X["transition"] == t) & (X["metric"] == m)][["position_group", "n", "trust", "MAE model",
                                                         "MAE carry over", "MAE best simple", "80% coverage",
                                                         "verdict"]], ["MAE model", "MAE carry over",
                                                                       "MAE best simple"])
    cnt = X.groupby(["transition", "position_group"])["n"].first().unstack().fillna(0).astype(int)
    st.markdown("**Backtest moves per cell (same for every metric)**")
    st.dataframe(cnt, width="stretch")
    st.download_button("Download CSV", X.to_csv(index=False), "audit_by_transition_and_position.csv")

with tab5:
    I = audit.identity_audit(eng)
    sev = st.multiselect("Severity", ["check", "low impact", "handled", "info"], default=["check", "low impact"])
    df_show(I[I["severity"].isin(sev)].drop(columns=[c for c in ("pid", "cross_moves") if c in I]))
    st.caption("'Likely two players merged' uses evidence beyond the name: more minutes across two clubs in one "
               "season than one player can play, or recorded heights more than 3 cm apart. Wyscout ages are often "
               "off by one between files for the same player, so an age difference alone is not treated as a merge.")
    name = st.text_input("Look up a player", "Pérez")
    if name:
        df_show(audit.player_detail(eng, name))
    st.download_button("Download CSV", I.to_csv(index=False), "identity_audit.csv")
