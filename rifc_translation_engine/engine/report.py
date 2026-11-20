"""One page recruitment report (HTML; prints to a single letter page, in colour)."""
from __future__ import annotations

import html
from datetime import date

import numpy as np
import pandas as pd

from . import recruit as R
from .schema import METRICS, POSITION_LABELS

NAVY, GOLD, TEAL = "#0B1220", "#D99A1E", "#0F9F8F"

CSS = f"""
@page {{ size: letter; margin: 0.4in; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ font-family: Inter, -apple-system, 'Helvetica Neue', Arial, sans-serif; color: #1B2335; font-size: 8.6pt;
       margin: 0; background: #fff; }}
.wrap {{ max-width: 7.7in; margin: 0 auto; }}
.hd {{ background: {NAVY}; color: #E6EAF2; border-radius: 8px; padding: 10px 14px; display: flex;
       justify-content: space-between; gap: 12px; }}
.hd h1 {{ font-size: 17pt; margin: 0; font-weight: 700; }}
.hd .role {{ color: #F2B544; font-weight: 600; font-size: 9pt; }}
.hd .meta {{ color: #8FA0BA; font-size: 7.6pt; text-align: right; line-height: 1.5; }}
.facts {{ display: flex; flex-wrap: wrap; gap: 4px 18px; margin-top: 6px; font-size: 8pt; }}
.facts span b {{ display: block; font-size: 6.4pt; letter-spacing: .6px; text-transform: uppercase; color: #8FA0BA;
                font-weight: 600; }}
.lead {{ border-left: 3px solid {GOLD}; background: #F6F7FA; padding: 6px 10px; margin: 8px 0 6px; font-size: 9pt;
         line-height: 1.45; border-radius: 4px; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(1.35in, 1fr)); gap: 6px; }}
.c {{ border: 1px solid #DCE2EA; border-radius: 6px; padding: 5px 7px; }}
.c .l {{ font-size: 6.6pt; text-transform: uppercase; letter-spacing: .5px; color: #5F6B7A; font-weight: 600; }}
.c .b {{ font-size: 14pt; font-weight: 700; line-height: 1.1; }}
.c .b small {{ font-size: 7pt; font-weight: 500; color: #5F6B7A; }}
.c .r {{ font-size: 7.3pt; color: #3A4458; line-height: 1.35; }}
h2 {{ font-size: 7.6pt; text-transform: uppercase; letter-spacing: .7px; color: #5F6B7A; margin: 9px 0 3px;
      font-weight: 700; }}
.grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 2px 16px; }}
ul {{ margin: 0; padding-left: 13px; }} li {{ margin: 1px 0; line-height: 1.35; }}
.dots {{ display: inline-flex; gap: 2px; vertical-align: middle; }}
.dots i {{ width: 6px; height: 6px; border-radius: 50%; background: #D5DBE4; display: inline-block; }}
.dots i.on {{ background: #3B6FD8; }}
.legend {{ font-size: 7pt; color: #5F6B7A; }}
.foot {{ color: #5F6B7A; font-size: 6.9pt; margin-top: 7px; border-top: 1px solid #DCE2EA; padding-top: 4px; line-height: 1.4; }}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _f(v, kind="rate", d=2):
    return R.fmt(v, kind, d)


def _dots(level):
    n = {"High": 3, "Medium": 2, "Low": 1}.get(level, 0)
    return "<span class='dots'>" + "".join(f"<i class='{'on' if i < n else ''}'></i>" for i in range(3)) + \
        f"</span> {_e(level)}"


def _ranges_svg(eng, ev) -> str:
    """Per metric: Championship middle half (band), median (tick), projection with 80% range, current (hollow)."""
    t = ev.table[ev.table["pred"].notna()]
    groups = R.role_groups(ev.role)
    W, LW, VW, RH = 740, 165, 150, 17
    TW = W - LW - VW
    rows = []
    for i, r in enumerate(t.itertuples()):
        d = R.positional_dist(eng, r.metric, groups).to_numpy()
        y = 8 + i * RH
        vals = [r.lo80, r.hi80] + ([r.current] if pd.notna(r.current) else [])
        if len(d) >= 10:
            q25, q50, q75 = np.percentile(d, [25, 50, 75])
            vals += [np.percentile(d, 5), np.percentile(d, 95)]
        lo, hi = min(vals), max(vals)
        pad = (hi - lo) * 0.05 or 0.05
        lo, hi = lo - pad, hi + pad
        X = lambda v: LW + (v - lo) / (hi - lo) * TW
        s = [f"<text x='0' y='{y + 4}' font-size='8' fill='#1B2335'>{_e(r.Metric)}</text>",
             f"<line x1='{LW}' x2='{LW + TW}' y1='{y}' y2='{y}' stroke='#EEF1F5' stroke-width='9'/>"]
        if len(d) >= 10:
            s.append(f"<line x1='{X(q25):.1f}' x2='{X(q75):.1f}' y1='{y}' y2='{y}' stroke='#C9D1DD' stroke-width='9'/>")
            s.append(f"<line x1='{X(q50):.1f}' x2='{X(q50):.1f}' y1='{y - 6}' y2='{y + 6}' stroke='#1B2335' stroke-width='1.2'/>")
        s.append(f"<line x1='{X(r.lo80):.1f}' x2='{X(r.hi80):.1f}' y1='{y}' y2='{y}' stroke='{GOLD}' stroke-width='2'/>")
        if pd.notna(r.current):
            s.append(f"<circle cx='{X(r.current):.1f}' cy='{y}' r='3.2' fill='white' stroke='#5F6B7A' stroke-width='1.3'/>")
        px = X(r.pred)
        s.append(f"<path d='M{px:.1f},{y - 4.5} L{px + 4.5:.1f},{y} L{px:.1f},{y + 4.5} L{px - 4.5:.1f},{y} Z' "
                 f"fill='{GOLD}' stroke='#1B2335' stroke-width='.6'/>")
        pct = f", {R.ordinal(r.pctl_uslc)} pctl" if pd.notna(r.pctl_uslc) else ""
        s.append(f"<text x='{W}' y='{y + 3}' font-size='7.6' text-anchor='end' fill='#1B2335'><tspan font-weight='700'>"
                 f"{_f(r.pred, r.kind)}</tspan> ({_f(r.lo80, r.kind)} to {_f(r.hi80, r.kind)}){pct}</text>")
        rows.append("".join(s))
    h = 8 + len(t) * RH
    return (f"<svg viewBox='0 0 {W} {h}' width='100%' xmlns='http://www.w3.org/2000/svg' "
            f"font-family='Inter, Helvetica, Arial, sans-serif'>{''.join(rows)}</svg>")


def build(eng, ev: R.Evaluation, cmp: pd.DataFrame | None = None, club: dict | None = None) -> str:
    """club: identity.club_status(...) for the player; when given, the header shows the last recorded club and the
    current club verification separately."""
    p, t, s = ev.player, ev.table.set_index("metric"), ev.summary
    row = ev.row if ev.row is not None else {}
    age = f"{p.age:.0f}" if p.age else "n/a"
    contract = row.get("contract_expires") if isinstance(row.get("contract_expires"), str) else "not in data"
    facts = [("Season used", f"{p.season} {p.league}"), ("Club that season", p.team or "n/a"), ("Age", age),
             ("Position", POSITION_LABELS.get(p.position_group, p.position_group)), ("Minutes", f"{int(p.minutes):,}")]
    if row is not None and bool(row.get("display_only")):
        facts.append(("Source data", "Partial dataset (not used by the model)"))
    if club:
        facts.append(("Last recorded club", f"{club['last_club']} ({club['last_season']})"))
        facts.append(("Current club", f"{club['current_club'] or club['status']} (verified {club['verified_on']})"
                      if club["verified"] else "Unverified"))
        facts.append(("Contract", f"{club['contract_expiry']} ({club['contract_source']})" if club["contract_expiry"]
                      else "Not in data"))
    else:
        facts.append(("Contract (Wyscout)", contract))
    cards = []
    for m in R.PRIMARY.get(ev.role, [])[:5]:
        if m not in t.index:
            continue
        r = t.loc[m]
        k = r["kind"]
        if pd.isna(r.get("pred")):
            cards.append(f"<div class='c'><div class='l'>{_e(METRICS[m].label)}</div><div class='b'>n/a</div>"
                         f"<div class='r'>Not projected</div></div>")
            continue
        big = (f"{r['pred_season']:.1f}<small> at {int(ev.minutes):,}</small>" if k == "rate" else _f(r["pred"], k))
        _, diff = R.change_words(r["current"], r["pred"], k)
        pct = R.ordinal(r["pctl_uslc"]) if pd.notna(r.get("pctl_uslc")) else "n/a"
        cards.append(f"<div class='c'><div class='l'>{_e(METRICS[m].label)}</div><div class='b'>{big}</div>"
                     f"<div class='r'><b>{_f(r['pred'], k)}</b> per 90, now {_f(r['current'], k)}<br>{_e(diff)}<br>"
                     f"{pct} pctl | 80% {_f(r['lo80'], k)} to {_f(r['hi80'], k)}<br>{_dots(r['confidence'])}</div></div>")
    li = lambda xs: "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in xs) + "</ul>" if xs else "<p>None identified from the data.</p>"
    comp = R.comparison_summary(cmp, ev) if cmp is not None and not cmp.empty else []
    bench_n = len(R.positional_dist(eng, ev.table["metric"].iloc[0], R.role_groups(ev.role)))
    na = ev.table[ev.table["pred"].isna()]["Metric"].tolist()
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{_e(p.name)} projection</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="hd"><div><div class="role">{_e(ev.role)}</div><h1>{_e(p.name)}</h1>
<div class="facts">{''.join(f"<span><b>{_e(k)}</b>{_e(v)}</span>" for k, v in facts)}</div></div>
<div class="meta">Rhode Island FC recruitment<br>Championship projection<br>{date.today():%d %b %Y}<br>Internal</div></div>
<div class="lead">{_e(R.headline(ev))}</div>
<div class="cards">{''.join(cards)}</div>
<h2>Projected Championship per 90, with 80% range</h2>
<div class="legend">Grey band: middle half of Championship {_e(ev.role.lower())}s (900+ min, n={bench_n}); black tick: median;
gold diamond and line: projection and 80% range; hollow circle: his current rate in {_e(p.league)}.</div>
{_ranges_svg(eng, ev)}
<div class="grid">
<div><h2>Projected strengths</h2>{li(s['strengths'][:4])}</div>
<div><h2>Statistical concerns</h2>{li(s['concerns'][:4])}</div>
<div><h2>Against current RIFC players (observed)</h2>{li(comp) if comp else '<p>No RIFC players in this role.</p>'}</div>
<div><h2>Video scouting priorities</h2>{li(s['video'][:4])}</div>
</div>
<div class="foot">Neutral projection for an average Championship team from the validated translation model, learned from
{int((eng.pairs["pair_type"] == "cross").sum())} moves between MLS NEXT Pro, USL League One and the Championship and tested
on later seasons. Confidence: High or Medium means validated with 50+ or 20+ tested moves for both the transition and the
position. Minutes ({int(ev.minutes):,}) are a scenario, not a prediction. No fit score, availability or fee information.
{('Not projected: ' + _e(', '.join(na)) + '.') if na else ''} Wyscout player data; StatsBomb team data for RIFC context.</div>
</div></body></html>"""
