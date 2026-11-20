"""Shared Streamlit helpers and the design system (dark navy theme, cards, chart template)."""
from __future__ import annotations

import html as _h

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from engine import db
from engine.api import Engine
from engine.schema import METRICS

# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------
BG = "#0B1220"
PANEL = "#131D31"
PANEL2 = "#1A2640"
BORDER = "#26344F"
TEXT = "#E6EAF2"
MUTED = "#8FA0BA"
FAINT = "#5B6B85"
ACCENT = "#4C8DFF"       # selection, links
CAND = "#F2B544"         # the candidate (projected)
CAND_SOFT = "rgba(242,181,68,.28)"
RIFC = "#2DD4BF"         # Rhode Island FC players
RIFC_SHADES = ["#2DD4BF", "#5EEAD4", "#14B8A6", "#99F6E4"]
BENCH = "rgba(143,160,186,.20)"   # Championship distribution band
BENCH_MID = "rgba(143,160,186,.38)"
LEAGUE_COLORS = {"USL Championship": "#8FA0BA", "USL League One": "#A78BFA", "MLS Next Pro": "#60A5FA"}
CONF_ORDER = ["High", "Medium", "Low", "None"]
CONF_DOTS = {"High": 3, "Medium": 2, "Low": 1, "None": 0}
CONF_HELP = {
    "High": "Validated, and 50+ backtest moves for both this league transition and this position.",
    "Medium": "Validated, and 20+ backtest moves for both this league transition and this position.",
    "Low": "Projected, but not validated or based on few past moves. Treat as rough.",
    "None": "Not enough evidence to project.",
}
STATUS_TEXT = {"validated": "Validated", "unvalidated": "Not validated", "insufficient": "Insufficient data",
               "not available": "Not available"}

# Kept for older pages
NAVY = "#0B2545"
GOLD = CAND
CONF_COLOR = {"High": "#34D399", "Medium": "#FBBF24", "Low": "#F87171", "None": "#6B7A90"}
STATUS_STYLE = {
    "validated": ("Validated", "#2E7D32"),
    "unvalidated": ("Not validated", "#B26A00"),
    "insufficient": ("Insufficient data", "#B00020"),
}

# ---------------------------------------------------------------------------
# Plotly template
# ---------------------------------------------------------------------------
_tpl = go.layout.Template()
_tpl.layout = go.Layout(
    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif", color=TEXT, size=12),
    xaxis=dict(gridcolor="#1C2840", zerolinecolor="#1C2840", linecolor=BORDER, tickcolor=BORDER,
               title=dict(font=dict(color=MUTED, size=11)), tickfont=dict(color=MUTED)),
    yaxis=dict(gridcolor="#1C2840", zerolinecolor="#1C2840", linecolor=BORDER, tickcolor=BORDER,
               title=dict(font=dict(color=MUTED, size=11)), tickfont=dict(color=MUTED)),
    hoverlabel=dict(bgcolor=PANEL2, bordercolor=BORDER, font=dict(color=TEXT, size=12)),
    legend=dict(font=dict(color=MUTED, size=11), bgcolor="rgba(0,0,0,0)"),
    colorway=[ACCENT, CAND, RIFC, "#A78BFA", "#F472B6", "#60A5FA"],
    margin=dict(l=10, r=10, t=30, b=10),
)
pio.templates["rifc"] = _tpl
pio.templates.default = "plotly_dark+rifc"

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
html, body, [class*="css"], .stMarkdown, .stText, button, input, textarea, select {{
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1480px; }}
h1 {{ font-size: 1.65rem !important; font-weight: 700 !important; letter-spacing: .1px; }}
h2 {{ font-size: 1.25rem !important; font-weight: 650 !important; }}
h3 {{ font-size: 1.05rem !important; font-weight: 650 !important; }}
[data-testid="stSidebar"] {{ background: #0E1729; border-right: 1px solid {BORDER}; }}
[data-testid="stExpander"] details {{ border: 1px solid {BORDER}; border-radius: 10px; background: {PANEL}; }}
[data-testid="stMetric"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 10px 14px; }}
div[data-testid="stPopover"] button {{ border-radius: 8px; }}
.small, .muted {{ font-size: .84rem; color: {MUTED}; }}
.eyebrow {{ font-size: .72rem; letter-spacing: .9px; text-transform: uppercase; color: {MUTED}; font-weight: 600; }}
.sec {{ margin: 1.6rem 0 .6rem 0; }}
.sec .t {{ font-size: 1.12rem; font-weight: 650; color: {TEXT}; }}
.sec .s {{ font-size: .86rem; color: {MUTED}; margin-top: 2px; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; color:#0B1220; font-size:.78rem; font-weight:600; }}
/* generic card */
.card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; }}
.card h4 {{ margin: 0 0 8px 0; font-size: .74rem; text-transform: uppercase; letter-spacing: .8px; color: {MUTED}; font-weight: 600; }}
.card ul {{ margin: 0; padding-left: 18px; }}
.card li {{ margin-bottom: 4px; line-height: 1.45; }}
.card p {{ margin: 0 0 6px 0; line-height: 1.5; }}
.lead {{ font-size: 1.02rem; line-height: 1.55; }}
/* player header */
.ph {{ background: linear-gradient(135deg, #15213A 0%, #0F192C 100%); border: 1px solid {BORDER}; border-radius: 14px;
       padding: 18px 22px 16px 22px; margin: 4px 0 14px 0; }}
.ph-top {{ display:flex; flex-wrap:wrap; align-items:baseline; gap: 6px 14px; }}
.ph-name {{ font-size: 1.75rem; font-weight: 700; color: {TEXT}; line-height: 1.15; }}
.ph-role {{ font-size: .95rem; color: {CAND}; font-weight: 600; }}
.ph-sub {{ font-size: .95rem; color: {MUTED}; margin-top: 4px; }}
.ph-grid {{ display:flex; flex-wrap:wrap; gap: 10px 28px; margin-top: 14px; }}
.ph-item {{ min-width: 0; }}
.ph-item .k {{ font-size: .7rem; letter-spacing: .8px; text-transform: uppercase; color: {FAINT}; font-weight: 600; }}
.ph-item .v {{ font-size: .98rem; color: {TEXT}; font-weight: 550; white-space: normal; overflow-wrap: anywhere; }}
.ph-note {{ font-size: .8rem; color: {MUTED}; margin-top: 10px; }}
/* projection cards */
.pc-grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(205px, 1fr)); gap: 12px; margin: 6px 0 4px 0; }}
.pc {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 14px 16px 12px 16px;
       display:flex; flex-direction:column; gap: 6px; min-width: 0; }}
.pc .lab {{ font-size: .74rem; letter-spacing: .8px; text-transform: uppercase; color: {MUTED}; font-weight: 600; }}
.pc .big {{ font-size: 1.9rem; font-weight: 700; color: {TEXT}; line-height: 1.05; }}
.pc .big small {{ font-size: .85rem; color: {MUTED}; font-weight: 500; margin-left: 4px; }}
.pc .rate {{ font-size: .9rem; color: {TEXT}; }}
.pc .rate b {{ color: {CAND}; font-weight: 650; }}
.pc .row {{ display:flex; justify-content:space-between; gap: 8px; font-size: .82rem; color: {MUTED}; }}
.pc .row span:last-child {{ color: {TEXT}; text-align:right; }}
.pc .rng {{ font-size: .8rem; color: {MUTED}; }}
.pc .foot {{ display:flex; justify-content:space-between; align-items:center; border-top: 1px solid {BORDER};
             padding-top: 8px; margin-top: 2px; font-size: .78rem; color: {MUTED}; gap: 6px; flex-wrap: wrap; }}
.pc.na {{ opacity: .7; }}
.meter {{ display:inline-flex; align-items:center; gap:3px; }}
.meter i {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:#2A3855; }}
.meter i.on {{ background: {ACCENT}; }}
.meter b {{ font-weight: 600; color: {TEXT}; margin-left: 5px; font-size: .78rem; }}
.pill {{ display:inline-block; padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 600;
         border: 1px solid {BORDER}; color: {TEXT}; background: {PANEL2}; }}
.pill.val {{ border-color: rgba(76,141,255,.55); color: #BFD5FF; }}
.pill.exp {{ border-color: rgba(242,181,68,.6); color: {CAND}; background: rgba(242,181,68,.08); }}
.pill.rifc {{ border-color: rgba(45,212,191,.6); color: {RIFC}; }}
.banner {{ border-radius: 10px; padding: 10px 14px; font-size: .9rem; margin: 6px 0 12px 0; }}
.banner.exp {{ border: 1px solid rgba(242,181,68,.55); background: rgba(242,181,68,.08); color: #F7D58C; }}
.banner.val {{ border: 1px solid rgba(76,141,255,.5); background: rgba(76,141,255,.08); color: #BFD5FF; }}
.headline {{ background: {PANEL}; border-left: 3px solid {CAND}; border-radius: 8px; padding: 12px 16px;
             font-size: 1.02rem; line-height: 1.55; margin: 10px 0 6px 0; }}
.steps {{ width:100%; border-collapse: collapse; font-size: .88rem; }}
.steps td {{ padding: 6px 8px; border-bottom: 1px solid {BORDER}; vertical-align: top; }}
.steps td.n {{ text-align:right; white-space:nowrap; font-variant-numeric: tabular-nums; }}
.steps tr.fin td {{ font-weight: 650; color: {CAND}; border-bottom: none; }}
.tcard {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 14px 16px; margin-bottom: 10px; }}
.tcard .nm {{ font-size: 1.08rem; font-weight: 650; }}
.tcard .meta {{ font-size: .85rem; color: {MUTED}; margin: 2px 0 8px 0; }}
.tcard ul {{ margin: 4px 0 0 0; padding-left: 18px; font-size: .88rem; }}
.kv {{ font-size: .85rem; color: {MUTED}; }}
.why3 {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 4px 0 14px 0; }}
.wc {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; padding: 12px 16px; min-width: 0; }}
.wc .lab {{ font-size: .72rem; letter-spacing: .8px; text-transform: uppercase; color: {MUTED}; font-weight: 600; }}
.wc .v {{ font-size: 1.7rem; font-weight: 700; color: {TEXT}; margin-top: 4px; line-height: 1.1; }}
.wc .v small {{ font-size: .8rem; color: {MUTED}; font-weight: 500; margin-left: 4px; }}
.wc .sub {{ font-size: .82rem; color: {MUTED}; margin-top: 2px; }}
.wc.hi {{ border-color: rgba(242,181,68,.45); }}
.wc.hi .v {{ color: {CAND}; }}
.whyl {{ list-style: none; padding: 0; margin: 0; }}
.whyl li {{ font-size: .93rem; line-height: 1.5; padding: 8px 0; border-bottom: 1px solid {BORDER}; }}
.whyl li:last-child {{ border-bottom: none; }}
.whyl .tag {{ display:inline-block; font-size: .7rem; text-transform: uppercase; letter-spacing: .6px; font-weight: 700;
             color: {MUTED}; border: 1px solid {BORDER}; border-radius: 6px; padding: 1px 6px; margin-right: 8px; }}
.ivl {{ margin: 18px 4px 6px 4px; }}
.ivl .trk {{ position: relative; height: 10px; background: #1C2840; border-radius: 6px; }}
.ivl .band {{ position: absolute; top: 0; height: 10px; background: rgba(242,181,68,.45); border-radius: 6px; }}
.ivl .mk {{ position: absolute; top: -4px; width: 14px; height: 14px; margin-left: -7px; border-radius: 50%; }}
.ivl .mk.proj {{ background: {CAND}; transform: rotate(45deg); border-radius: 2px; border: 1px solid #0B1220; }}
.ivl .mk.now {{ border: 2px solid {MUTED}; background: {BG}; }}
.ivl .lbl {{ position: relative; height: 18px; margin-top: 6px; font-size: .8rem; color: {MUTED}; }}
.ivl .lbl span {{ position: absolute; transform: translateX(-50%); white-space: nowrap; }}
.rel {{ font-size: .88rem; margin-top: 8px; }}
.kv b {{ color: {TEXT}; font-weight: 600; }}
</style>
"""


def setup(title: str):
    try:
        st.set_page_config(page_title=f"RIFC Recruitment | {title}", layout="wide")
    except Exception:
        pass  # already set by the app's navigation
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        db.init_db()
    except Exception as exc:  # e.g. a database written by a newer DuckDB version
        _offer_rebuild(f"The database could not be opened on this computer ({type(exc).__name__}: {exc}).")


def _offer_rebuild(why: str):
    from engine.rebuild import refit, rebuild
    st.error(why)
    st.markdown("The original files are all in data/raw. Rebuilding reimports them with the same settings, "
                "then refits and backtests (a minute or two). The old database is renamed, not deleted.")
    if st.button("Rebuild from original files", type="primary"):
        box = st.empty()
        with st.spinner("Rebuilding"):
            rebuild(progress=lambda m: box.caption(m))
            st.session_state["engine"] = refit(progress=lambda m: box.caption(m))
        st.success("Rebuilt. Reloading.")
        st.rerun()
    st.stop()


# ---------------------------------------------------------------------------
# Small HTML components
# ---------------------------------------------------------------------------
def esc(x) -> str:
    return _h.escape("" if x is None else str(x))


def meter(level: str) -> str:
    n = CONF_DOTS.get(level, 0)
    dots = "".join(f"<i class='{'on' if i < n else ''}'></i>" for i in range(3))
    return f"<span class='meter' title='{esc(CONF_HELP.get(level, ''))}'>{dots}<b>{esc(level)}</b></span>"


def conf_badge(level: str) -> str:
    return meter(level)


def pill(text: str, kind: str = "") -> str:
    return f"<span class='pill {kind}'>{esc(text)}</span>"


def section(title: str, sub: str = ""):
    st.markdown(f"<div class='sec'><div class='t'>{esc(title)}</div>"
                + (f"<div class='s'>{esc(sub)}</div>" if sub else "") + "</div>", unsafe_allow_html=True)


def card(title: str, items: list[str] | str, empty: str = "None identified from the data."):
    if isinstance(items, str):
        body = f"<p class='lead'>{esc(items) or empty}</p>"
    else:
        body = "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in items) + "</ul>" if items else f"<p>{empty}</p>"
    st.markdown(f"<div class='card'><h4>{esc(title)}</h4>{body}</div>", unsafe_allow_html=True)


def banner(text: str, kind: str = "val"):
    st.markdown(f"<div class='banner {kind}'>{text}</div>", unsafe_allow_html=True)


def plot(fig, key=None, **kw):
    """Plotly chart in the app theme (our template, not Streamlit's)."""
    cfg = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"]}
    cfg.update(kw.pop("config", {}))
    return st.plotly_chart(fig, theme=None, width="stretch", config=cfg, key=key, **kw)


# ---------------------------------------------------------------------------
# Player picker
# ---------------------------------------------------------------------------
def pick_player(eng, key_name: str = "player_key", default_key: str | None = None, label="Player"):
    """Fast search across every player season. Returns the selected row (or None)."""
    from engine import recruit as R
    t = R.player_table(eng)
    c0, c1 = st.columns([5, 1], vertical_alignment="bottom")
    with c1.popover("Filters", width="stretch"):
        lg = st.multiselect("League", sorted(t["league"].unique()), key=f"{key_name}_lg")
        ss = st.multiselect("Season", sorted(t["season"].unique(), reverse=True), key=f"{key_name}_ss")
        pg = st.multiselect("Position", ["ST", "W", "AM", "CM", "DM", "FB", "CB"], key=f"{key_name}_pg")
        mn = st.number_input("Minimum minutes", 0, 4000, 450, 90, key=f"{key_name}_mn")
    if lg:
        t = t[t["league"].isin(lg)]
    if ss:
        t = t[t["season"].isin(ss)]
    if pg:
        t = t[t["position_group"].isin(pg)]
    cur = st.session_state.get(key_name) or default_key
    t = t[(t["minutes"] >= mn) | (t["key"] == cur)]
    if t.empty:
        c0.info("No players match the filters.")
        return None
    keys = t["key"].tolist()
    labels = dict(zip(t["key"], t["label"]))
    idx = keys.index(cur) if cur in keys else 0
    k = c0.selectbox(label, keys, index=idx, format_func=lambda x: labels[x], key=f"{key_name}_box",
                     help="Type part of a name to search.")
    st.session_state[key_name] = k
    return t[t["key"] == k].iloc[0]


def badge(status: str) -> str:
    label, color = STATUS_STYLE.get(status, (status, "#555"))
    return f'<span class="badge" style="background:{color};color:white">{label}</span>'


def get_engine(reload: bool = False) -> Engine | None:
    if reload or "engine" not in st.session_state:
        try:
            st.session_state["engine"] = Engine.load()
        except Exception:
            # Saved model files written by different library versions: refit from the database instead.
            from engine.rebuild import refit
            box = st.empty()
            with st.spinner("Saved model could not be read on this computer. Refitting from your data (about a minute)"):
                st.session_state["engine"] = refit(progress=lambda m: box.caption(m))
            box.empty()
    return st.session_state["engine"]


def get_identity(eng: Engine):
    """Player identity layer (one stable ID per player); cached per data, model run and manual decisions."""
    from engine import identity as I
    k = I.cache_file(eng)
    if st.session_state.get("_idn_key") != k:
        box = st.empty()
        with st.spinner("Building player profiles from every season (first time after new data, under a minute)"):
            st.session_state["_idn"] = I.load_or_build(eng, progress=lambda m: box.caption(m))
        box.empty()
        st.session_state["_idn_key"] = k
    return st.session_state["_idn"]


def discovery_table(eng: Engine) -> pd.DataFrame:
    """Neutral Championship projections for every player season record (cached per model run and identity build)."""
    from engine import discovery as D
    from engine import identity as I
    idn = get_identity(eng)
    tag = "_" + I.cache_file(eng).split("identity_")[-1].split(".")[0]
    k = ("disc", eng.run_id, eng.backtest_id, eng.fingerprint, tag)
    if st.session_state.get("_disc_key") != k:
        with st.spinner("Projecting every player season in the database (first time only, a few seconds)"):
            T = D.load_or_build(eng, records=idn.records, tag=tag)
            pl = idn.players.set_index("uid")
            T["display_name"] = T["uid"].map(pl["display_name"])
            st.session_state["_disc"] = T
        st.session_state["_disc_key"] = k
    return st.session_state["_disc"]


def open_profile(uid: str, rec_key: str | None = None):
    """Jump to the unified player profile, optionally on one season record."""
    st.session_state["player_uid"] = uid
    st.session_state["player_rec"] = rec_key
    st.switch_page("views/player_projection.py")


def player_search(eng: Engine, key: str = "ps", default_uid: str | None = None, label: str = "Search player",
                  placeholder: str = "Name, club or league"):
    """One result per player. Typing narrows the list to real matches only (exact and close names first, then
    alternate names, clubs and leagues). Returns the chosen player ID."""
    from engine import identity as I
    idn = get_identity(eng)
    pl = idn.players.set_index("uid")
    cur = st.session_state.get("player_uid") or default_uid
    if cur not in pl.index:
        cur = None
    c0, c1 = st.columns([1.3, 2.7], vertical_alignment="bottom")
    q = c0.text_input(label, key=f"{key}_q", placeholder=placeholder)
    if q.strip():
        res = I.search(idn, q)
        if res.empty:
            c1.markdown(f"<div class='kv' style='padding-bottom:10px'>No player, club or league matches "
                        f"<b>{esc(q)}</b>.</div>", unsafe_allow_html=True)
            return cur
        opts = res["uid"].tolist()
    else:
        opts = [cur] if cur else []
        if not opts:
            c1.markdown("<div class='kv' style='padding-bottom:10px'>Type to search.</div>", unsafe_allow_html=True)
            return None
    idx = opts.index(cur) if cur in opts else 0
    n = f"{len(opts)} match{'es' if len(opts) != 1 else ''}" if q.strip() else "Player"
    uid = c1.selectbox(n, opts, index=idx, format_func=lambda u: I.label(pl.loc[u].to_dict() | {"uid": u}),
                       key=f"{key}_pick_{abs(hash(q.strip().lower())) % 10**8}")
    if uid != st.session_state.get("player_uid"):
        st.session_state["player_uid"] = uid
        st.session_state["player_rec"] = None
    return uid


def stale_warning(eng: Engine | None):
    if eng is None:
        st.info("No translation model fitted yet. Import data, then fit the model on the Translation Model page.")
        return
    if eng.is_stale():
        st.warning("Data has changed since the model was fitted. Refit on the Translation Model page, "
                   "then rerun the backtest.")


def fmt(v, kind="rate", d=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return f"{v:.1f}%" if kind == "pct" else f"{v:.{d}f}"


def metric_label(m: str) -> str:
    return METRICS[m].label if m in METRICS else m


def db_counts() -> dict:
    out = {}
    for t in ("imports", "player_seasons", "transfers_ref"):
        out[t] = int(db.query(f"SELECT COUNT(*) AS n FROM {t}")["n"].iloc[0])
    return out


def df_show(df: pd.DataFrame, **kw):
    st.dataframe(df, hide_index=True, width="stretch", **kw)
