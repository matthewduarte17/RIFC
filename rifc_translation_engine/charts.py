"""Plotly figures for the recruitment pages (dark theme from ui.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine.schema import METRICS
from ui import ACCENT, BENCH, BENCH_MID, BORDER, CAND, CAND_SOFT, FAINT, MUTED, PANEL2, RIFC, RIFC_SHADES, TEXT

RIFC_SYMBOLS = ["circle", "square", "triangle-up", "star"]


def _f(v, kind, d=2):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    return f"{v:.1f}%" if kind == "pct" else f"{v:.{d}f}"


def _ord(n):
    n = int(round(n))
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _pct_of(dist, v):
    if dist is None or len(dist) < 10 or v is None or np.isnan(v):
        return None
    d = np.sort(np.asarray(dist))
    lo, hi = np.searchsorted(d, v, "left"), np.searchsorted(d, v, "right")
    return (lo + 0.5 * (hi - lo)) / len(d) * 100

ROW_PX, TITLE_PX, STRIP_PX = 78, 20, 28


def _stack(fig, n, legend_px):
    """Fixed pixel layout for stacked strips: title, strip, tick labels, gap. Returns annotation y per row."""
    P = ROW_PX * n
    ys = []
    for i in range(n):
        top = 1 - i * ROW_PX / P
        dom = [top - (TITLE_PX + STRIP_PX) / P, top - TITLE_PX / P]
        fig.layout[f"yaxis{i + 1 if i else ''}"].domain = dom
        ys.append(top)
    fig.update_layout(height=P + legend_px + 10, margin=dict(l=8, r=8, t=legend_px, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.0 + 4 / P, x=0, font=dict(size=11)))
    return ys


def percentile_strips(rows: list[dict], role_label: str, season: str | None, show_current: bool = True):
    """One strip per metric on its own per 90 scale: Championship P10 to P90 and P25 to P75 bands, median tick,
    the candidate's projection (diamond) and current rate (hollow), RIFC players (observed).

    rows: dicts with metric, kind, dist (array), pred, lo80, hi80, current, pctl, rifc (list of (name, value))."""
    n = len(rows)
    if not n:
        return None
    fig = make_subplots(rows=n, cols=1)
    titles = []
    rifc_names = []
    for r in rows:
        for nm, _ in r.get("rifc", []):
            if nm not in rifc_names:
                rifc_names.append(nm)
    shown = set()
    for i, r in enumerate(rows, start=1):
        d = np.asarray(r["dist"], float)
        kind = r["kind"]
        lab = METRICS[r["metric"]].label
        if len(d) < 10:
            titles.append(f"<b>{lab}</b>   <span style='color:{MUTED}'>fewer than 10 benchmark players</span>")
            continue
        q10, q25, q50, q75, q90 = np.percentile(d, [10, 25, 50, 75, 90])
        vals = [q10, q90] + [x for x in (r.get("pred"), r.get("current")) if x is not None and not np.isnan(x)] \
            + [v for _, v in r.get("rifc", [])]
        lo_x, hi_x = min(vals + [d.min()]), max(vals + [np.percentile(d, 98)])
        pad = (hi_x - lo_x) * 0.06 or 0.05
        bench_txt = (f"Championship {role_label.lower()}s {season or ''} (n={len(d)})<br>P25 {_f(q25, kind)}, "
                     f"median {_f(q50, kind)}, P75 {_f(q75, kind)}")
        # bands as thick lines (hoverable)
        fig.add_trace(go.Scatter(x=[q10, q90], y=[0, 0], mode="lines", line=dict(color=BENCH, width=14),
                                 hovertemplate=f"P10 to P90: {_f(q10, kind)} to {_f(q90, kind)}<extra></extra>",
                                 showlegend=False), row=i, col=1)
        fig.add_trace(go.Scatter(x=[q25, q75], y=[0, 0], mode="lines", line=dict(color=BENCH_MID, width=14),
                                 hovertemplate=bench_txt + "<extra></extra>",
                                 name="Championship P25 to P75", showlegend="band" not in shown,
                                 legendgroup="band"), row=i, col=1)
        shown.add("band")
        fig.add_trace(go.Scatter(x=[q50], y=[0], mode="markers", marker=dict(symbol="line-ns", size=20,
                                 line=dict(color=TEXT, width=2)), name="Championship median",
                                 hovertemplate=f"Median {_f(q50, kind)}<extra></extra>",
                                 showlegend="med" not in shown, legendgroup="med"), row=i, col=1)
        shown.add("med")
        for j, (nm, v) in enumerate(r.get("rifc", [])):
            k = rifc_names.index(nm)
            fig.add_trace(go.Scatter(
                x=[v], y=[0.0], mode="markers", name=f"{nm} (RIFC, observed)", legendgroup=f"r{k}",
                showlegend=f"r{k}" not in shown,
                marker=dict(symbol=RIFC_SYMBOLS[k % 4], size=10, color=RIFC_SHADES[k % 4],
                            line=dict(color="#0B1220", width=1)),
                hovertemplate=f"{nm}, RIFC observed: {_f(v, kind)} ({_ord(_pct_of(d, v) or 0)} pctl)<extra></extra>"),
                row=i, col=1)
            shown.add(f"r{k}")
        cur = r.get("current")
        if show_current and cur is not None and not np.isnan(cur):
            fig.add_trace(go.Scatter(x=[cur], y=[0], mode="markers", name="Candidate now (source league)",
                                     legendgroup="cur", showlegend="cur" not in shown,
                                     marker=dict(symbol="circle-open", size=11, color=MUTED, line=dict(width=2)),
                                     hovertemplate=f"Now, in source league: {_f(cur, kind)}<extra></extra>"), row=i, col=1)
            shown.add("cur")
        p = r.get("pred")
        if p is not None and not np.isnan(p):
            fig.add_trace(go.Scatter(
                x=[p], y=[0], mode="markers", name="Candidate projected", legendgroup="cand",
                showlegend="cand" not in shown,
                marker=dict(symbol="diamond", size=15, color=CAND, line=dict(color="#0B1220", width=1.5)),
                hovertemplate=(f"Projected {_f(p, kind)} per 90<br>80% range {_f(r.get('lo80'), kind)} to "
                               f"{_f(r.get('hi80'), kind)}<br>{_ord(r['pctl'])} percentile<extra></extra>"
                               if r.get("pctl") is not None and not np.isnan(r.get("pctl")) else
                               f"Projected {_f(p, kind)}<extra></extra>")), row=i, col=1)
            shown.add("cand")
        pct_txt = f"{_ord(r['pctl'])} pctl" if r.get("pctl") is not None and not np.isnan(r.get("pctl")) else ""
        fig.update_xaxes(range=[lo_x - pad, hi_x + pad], row=i, col=1, showgrid=False, zeroline=False,
                         ticks="outside", ticklen=3, nticks=6, tickfont=dict(size=10, color=FAINT),
                         ticksuffix="%" if kind == "pct" else "")
        fig.update_yaxes(range=[-1, 1], visible=False, fixedrange=True, row=i, col=1)
        titles.append(f"<b>{lab}</b>   <span style='color:{CAND}'>{pct_txt}</span>")
    ys = _stack(fig, n, 92)
    for y, tx in zip(ys, titles):
        fig.add_annotation(text=tx, xref="paper", yref="paper", x=0, y=y, xanchor="left", yanchor="top",
                           showarrow=False, font=dict(size=12, color=TEXT))
    fig.update_layout(hovermode="closest")
    return fig


def interval_chart(rows: list[dict]):
    """Uncertainty: projected per 90 with 50% (thick) and 80% (thin) ranges, against the current rate."""
    n = len(rows)
    if not n:
        return None
    fig = make_subplots(rows=n, cols=1)
    titles = []
    shown = set()
    for i, r in enumerate(rows, start=1):
        kind = r["kind"]
        lab = METRICS[r["metric"]].label
        xs = [x for x in (r["lo80"], r["hi80"], r.get("current")) if x is not None and not np.isnan(x)]
        lo_x, hi_x = min(xs), max(xs)
        pad = (hi_x - lo_x) * 0.08 or 0.05
        fig.add_trace(go.Scatter(x=[r["lo80"], r["hi80"]], y=[0, 0], mode="lines",
                                 line=dict(color=CAND_SOFT, width=6), name="80% range", legendgroup="80",
                                 showlegend="80" not in shown,
                                 hovertemplate=f"80% range {_f(r['lo80'], kind)} to {_f(r['hi80'], kind)}<extra></extra>"),
                      row=i, col=1)
        shown.add("80")
        if r.get("lo50") is not None and not np.isnan(r.get("lo50")):
            fig.add_trace(go.Scatter(x=[r["lo50"], r["hi50"]], y=[0, 0], mode="lines",
                                     line=dict(color="rgba(242,181,68,.6)", width=12), name="50% range",
                                     legendgroup="50", showlegend="50" not in shown,
                                     hovertemplate=f"50% range {_f(r['lo50'], kind)} to {_f(r['hi50'], kind)}<extra></extra>"),
                          row=i, col=1)
            shown.add("50")
        if r.get("current") is not None and not np.isnan(r.get("current")):
            fig.add_trace(go.Scatter(x=[r["current"]], y=[0], mode="markers", name="Now (source league)",
                                     legendgroup="cur", showlegend="cur" not in shown,
                                     marker=dict(symbol="circle-open", size=11, color=MUTED, line=dict(width=2)),
                                     hovertemplate=f"Now {_f(r['current'], kind)}<extra></extra>"), row=i, col=1)
            shown.add("cur")
        fig.add_trace(go.Scatter(x=[r["pred"]], y=[0], mode="markers", name="Projected", legendgroup="p",
                                 showlegend="p" not in shown,
                                 marker=dict(symbol="diamond", size=14, color=CAND, line=dict(color="#0B1220", width=1.5)),
                                 hovertemplate=f"Projected {_f(r['pred'], kind)}<extra></extra>"), row=i, col=1)
        shown.add("p")
        fig.update_xaxes(range=[max(0, lo_x - pad) if kind != "pct" else lo_x - pad, hi_x + pad], row=i, col=1,
                         showgrid=False, zeroline=False, ticks="outside", ticklen=3, nticks=6,
                         tickfont=dict(size=10, color=FAINT), ticksuffix="%" if kind == "pct" else "")
        fig.update_yaxes(range=[-1, 1], visible=False, fixedrange=True, row=i, col=1)
        hist = (f"   <span style='color:{MUTED}'>past error {_f(r['hist_mae'], kind)} (n={int(r['hist_n'])})</span>"
                if r.get("hist_mae") is not None and not np.isnan(r.get("hist_mae")) else "")
        titles.append(f"<b>{lab}</b>{hist}")
    ys = _stack(fig, n, 56)
    for y, tx in zip(ys, titles):
        fig.add_annotation(text=tx, xref="paper", yref="paper", x=0, y=y, xanchor="left", yanchor="top",
                           showarrow=False, font=dict(size=12, color=TEXT))
    return fig


def waterfall(steps: pd.DataFrame, kind: str):
    """The model's steps from the current rate to the projection, on the per 90 scale."""
    s = steps.copy()
    meas = ["absolute"] + ["relative"] * (len(s) - 2) + ["total"]
    y = [s["value"].iloc[0]] + s["change"].iloc[1:-1].fillna(0).tolist() + [s["value"].iloc[-1]]
    txt = [_f(s["value"].iloc[0], kind)] + [("+" if c >= 0 else "") + _f(c, kind) for c in s["change"].iloc[1:-1].fillna(0)] \
        + [_f(s["value"].iloc[-1], kind)]
    txt = [t.replace("+-", "-") for t in txt]
    fig = go.Figure(go.Waterfall(
        orientation="v", measure=meas, x=[x.replace(" ", "<br>", 1) if len(x) > 12 else x for x in s["step"]], y=y, text=txt, textposition="outside",
        textfont=dict(color=TEXT, size=11),
        connector=dict(line=dict(color=BORDER, width=1)),
        increasing=dict(marker=dict(color="#6E9BF5")), decreasing=dict(marker=dict(color="#7C8BA6")),
        totals=dict(marker=dict(color=CAND)),
        hovertemplate="%{x}: %{text}<extra></extra>"))
    top = max(s["value"].max(), 0) * 1.2 + 0.01
    fig.update_layout(height=290, margin=dict(l=8, r=8, t=16, b=8), showlegend=False,
                      yaxis=dict(title="per 90" if kind == "rate" else "%", range=[0, top] if kind == "rate" else None),
                      xaxis=dict(tickfont=dict(size=11, color=MUTED), tickangle=0))
    return fig


def scenario_chart(series: list[dict], minutes: float):
    """Season totals across minutes: projected line and 80% band per metric (band recomputed at every minutes
    value), marker at the chosen minutes. series: dicts with label and curve (minutes, pred, lo80, hi80)."""
    fig = go.Figure()
    colors = [(CAND, "242,181,68"), (ACCENT, "76,141,255"), (RIFC, "45,212,191"), ("#A78BFA", "167,139,250")]
    for k, s in enumerate(series):
        c, rgba = colors[k % 4]
        cv = s["curve"]
        g = cv["minutes"].to_numpy()
        fig.add_trace(go.Scatter(x=np.r_[g, g[::-1]], y=np.r_[cv["hi80"], cv["lo80"][::-1]], fill="toself",
                                 fillcolor=f"rgba({rgba},.12)", line=dict(width=0), hoverinfo="skip",
                                 showlegend=False, legendgroup=s["label"]))
        fig.add_trace(go.Scatter(x=g, y=cv["pred"], mode="lines", line=dict(color=c, width=2.5), name=s["label"],
                                 legendgroup=s["label"], customdata=np.c_[cv["lo80"], cv["hi80"]],
                                 hovertemplate="%{y:.1f} (80% range %{customdata[0]:.1f} to %{customdata[1]:.1f})"
                                               "<extra>" + s["label"] + "</extra>"))
        i = int(np.argmin(np.abs(g - minutes)))
        fig.add_trace(go.Scatter(x=[g[i]], y=[cv["pred"].iloc[i]], mode="markers+text",
                                 text=[f"{cv['pred'].iloc[i]:.1f}"], textposition="top left",
                                 textfont=dict(color=c, size=12),
                                 marker=dict(color=c, size=9, line=dict(color="#0B1220", width=1)),
                                 showlegend=False, hoverinfo="skip"))
    fig.add_vline(x=minutes, line=dict(color=FAINT, dash="dot", width=1))
    fig.update_layout(height=300, margin=dict(l=8, r=8, t=10, b=8), hovermode="x unified",
                      xaxis=dict(title="Championship minutes", tickformat=",", range=[450, 3150]),
                      yaxis=dict(title="Season total", rangemode="tozero"),
                      legend=dict(orientation="h", yanchor="top", y=-0.22, x=0))
    return fig


def compare_bars(rows: list[dict], kind_by_metric: dict, cand_name: str):
    """Small multiples: one panel per metric, horizontal bars for the candidate (projected, with 80% whisker),
    his current rate, and each RIFC player (observed), with the Championship P25 to P75 band and median."""
    metrics = list(dict.fromkeys(r["metric"] for r in rows))
    n = len(metrics)
    if not n:
        return None
    cols = 2 if n > 1 else 1
    nr = int(np.ceil(n / cols))
    fig = make_subplots(rows=nr, cols=cols, subplot_titles=[METRICS[m].label for m in metrics],
                        horizontal_spacing=0.14, vertical_spacing=0.32 / nr if nr > 1 else 0.1)
    shown = set()
    for i, m in enumerate(metrics):
        rr, cc = i // cols + 1, i % cols + 1
        g = [r for r in rows if r["metric"] == m]
        kind = kind_by_metric[m]
        labels = [r["label"] for r in g]
        vals = [r["value"] for r in g]
        colors = [r["color"] for r in g]
        err_plus = [(r["hi"] - r["value"]) if r.get("hi") is not None else 0 for r in g]
        err_minus = [(r["value"] - r["lo"]) if r.get("lo") is not None else 0 for r in g]
        hover = [r["hover"] for r in g]
        fig.add_trace(go.Bar(x=vals, y=labels, orientation="h", marker=dict(color=colors), showlegend=False,
                             error_x=dict(type="data", symmetric=False, array=err_plus, arrayminus=err_minus,
                                          color=TEXT, thickness=1.4, width=4),
                             text=[_f(v, kind) for v in vals], textposition="inside", insidetextanchor="start",
                             textfont=dict(color="#0B1220", size=11),
                             hovertext=hover, hoverinfo="text"), row=rr, col=cc)
        bench = g[0].get("bench")
        if bench is not None:
            q25, q50, q75 = bench
            fig.add_vrect(x0=q25, x1=q75, fillcolor="rgba(143,160,186,.13)", line_width=0, layer="below",
                          row=rr, col=cc, exclude_empty_subplots=False)
            fig.add_vline(x=q50, line=dict(color=MUTED, dash="dot", width=1.2), row=rr, col=cc,
                          exclude_empty_subplots=False)
        fig.update_yaxes(autorange="reversed", tickfont=dict(size=11, color=TEXT), row=rr, col=cc)
        fig.update_xaxes(showgrid=True, gridcolor="#1C2840", rangemode="tozero", tickfont=dict(size=10, color=FAINT),
                         ticksuffix="%" if kind == "pct" else "", row=rr, col=cc)
    per = 34 * max(len([r for r in rows if r["metric"] == metrics[0]]), 3) + 70
    fig.update_layout(height=nr * per + 30, margin=dict(l=8, r=8, t=36, b=8), bargap=0.32)
    for a in fig.layout.annotations:
        if a.text in [METRICS[m].label for m in metrics]:
            a.font = dict(size=12, color=TEXT)
    return fig
