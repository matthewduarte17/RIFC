"""Scores the monitor's reader and matcher against hand-labelled real announcements (tests/fixtures/validation_sample.csv).

The labels were written by reading the announcements, before this script was run. This runs the same reading and matching
code a scan uses (scan.read_mentions and Matcher.match) on the live announcement pages and compares.

Run on a computer that can reach the league sites:
    RIFC_TE_HOME=<your data home> python tools/validate_monitor.py [--out report.md] [--csv details.csv]
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.monitor.classify import headline_type
from engine.monitor.http import Http, Robots
from engine.monitor.match import Matcher, name_compat, surname_key
from engine.monitor.profile import load_context
from engine.monitor.scan import read_mentions
from engine.monitor.sources import fetch_article

SAMPLE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "validation_sample.csv"
# truth move -> the pipeline event types that count as correct
OK_TYPES = {"signing": {"signing"}, "extension": {"extension"}, "loan_in": {"loan_in"}, "loan_recall": {"loan_return"}, "transfer_out": {"departure"},
            "option_exercised": {"option_exercised"}, "option_declined": {"option_declined"}, "under_contract": {"under_contract"},
            "out_of_contract": {"out_of_contract"}, "retirement": {"retirement"}, "released": {"released"}, "loan_return": {"loan_return"}, "no_move": set()}
# hand-written: the club where the right database player was recorded, when the name alone has twins
EXPECT_CLUB = {"Javier Garcia": "NY Cosmos", "Nikolai Pierre": "Rhode Island", "Juan David Torres": "Phoenix Rising", "Colton Swan": "Charleston Battery",
               "Jorge Garcia": "Pittsburgh Riverhounds", "Joel Garcia Jr.": "Monterey Bay FC", "Marlon Vargas": "New Mexico United", "Sam Gomez": "Monterey Bay FC"}
LEAGUE = {"dev_transactions": "USL Championship", "holdout_transactions": "USL League One", "holdout_roster_lists": "USL Championship",
          "holdout2_transactions": "USL Championship", "holdout2_controls": "USL Championship", "holdout2_roster_lists": "USL Championship",
          "untouched_transactions": "USL League One"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--csv")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(SAMPLE)))
    ctx = load_context(with_projections=False)
    if not ctx.ready:
        sys.exit("The model or identity layer is not available: " + "; ".join(ctx.warnings))
    matcher = Matcher(ctx.roster, ctx.catalog)
    http = Http(timeout=25, retries=1)
    robots = Robots(http)
    pages = {}
    detail = []
    for r in rows:
        key = (r["source_url"], r["headline"])
        if key not in pages:
            title, body = r["headline"], ""
            htype = headline_type(title)
            if htype and htype != "performance":
                st, t2, body, _ = fetch_article(r["source_url"], http, robots)
                if not st.startswith("ok"):
                    body = ""
            mentions, club = read_mentions(title, body, htype, LEAGUE[r["set"]], None, ctx, matcher)
            res = [(m, matcher.match(m.name, m.club_text, LEAGUE[r["set"]], m.position, m.extra_clubs, m.article_clubs)) for m in mentions]
            pages[key] = (htype, club, res)
        htype, club, res = pages[key]
        hit = next(((m, mr) for m, mr in res if name_compat(r["player"], m.name) or name_compat(m.name, r["player"]) or
                    (surname_key(m.name) == surname_key(r["player"]) and m.name[:1] == r["player"][:1])), None)
        out = {**r, "headline_type": htype or "", "announcing_club_read": club or "", "found": bool(hit), "read_type": hit[0].event_type if hit else "",
               "type_ok": False, "match_status": hit[1].status if hit else "", "match_grade": (hit[1].grade or "") if hit else "", "link": "", "link_ok": ""}
        if hit:
            m, mr = hit
            out["type_ok"] = m.event_type in OK_TYPES.get(r["truth_type"], set())
            if mr.status == "matched":
                pl = ctx.idn.players.set_index("uid").loc[mr.uid]
                exp = EXPECT_CLUB.get(r["player"])
                hist = {c.lower() for c in pl["clubs"]}
                out["link"] = f"{pl['display_name']} | {pl['last_club']} {pl['last_season']}"
                if r["db_truth"] == "no":
                    out["link_ok"] = "WRONG PERSON"
                elif r["db_truth"] == "unknown":
                    out["link_ok"] = "unverified"
                elif exp:
                    out["link_ok"] = "ok" if exp.lower() in hist else "WRONG PERSON"
                else:
                    out["link_ok"] = "ok" if (name_compat(r["player"], pl["display_name"]) or any(name_compat(r["player"], x) for x in pl["aliases"])) else "WRONG PERSON"
        detail.append(out)
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(detail[0]))
            w.writeheader()
            w.writerows(detail)
    lines = report(detail)
    print("\n".join(lines))
    if a.out:
        Path(a.out).write_text("\n".join(lines))


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def report(detail):
    L = []
    controls = [x for x in detail if x["truth_type"] == "no_move"]
    L += [f"## negative controls (headlines about awards and previews: no move should be reported)", f"- false alarms: {len([x for x in controls if x['found']])} of {len(controls)}", ""]
    for sett in ("dev_transactions", "holdout_transactions", "holdout_roster_lists", "holdout2_transactions", "holdout2_roster_lists", "untouched_transactions", "FRESH (holdout2 only)", "ALL"):
        d = [x for x in detail if x["truth_type"] != "no_move" and (sett == "ALL" or x["set"] == sett or (sett.startswith("FRESH") and x["set"].startswith("holdout2")))]
        if not d:
            continue
        found = [x for x in d if x["found"]]
        typ = [x for x in found if x["type_ok"]]
        indb = [x for x in d if x["db_truth"] in ("yes", "likely")]   # "unknown" labels are left out of every count
        linked = [x for x in indb if x["match_status"] == "matched"]
        review = [x for x in indb if x["match_status"] == "review"]
        nodb = [x for x in d if x["db_truth"] == "no"]
        wrong = [x for x in d if x["link_ok"] == "WRONG PERSON"]
        L += [f"## {sett}", f"- player statements: {len(d)}",
              f"- read (a player mention was extracted): {pct(len(found), len(d))}",
              f"- read with the right type: {pct(len(typ), len(d))}; read with the WRONG type: {pct(len(found) - len(typ), len(d))}; not read at all: {pct(len(d) - len(found), len(d))}",
              f"- players who ARE in our database: {len(indb)}; linked automatically: {pct(len(linked), len(indb))}; queued for review: {pct(len(review), len(indb))}; "
              f"left unmatched: {pct(len(indb) - len(linked) - len(review) - len([x for x in indb if not x['found']]), len(indb))}; not read: {len([x for x in indb if not x['found']])}",
              f"- automatic links to the WRONG person: {len(wrong)}; players NOT in our database that were linked to someone: {len([x for x in nodb if x['match_status'] == 'matched'])} of {len(nodb)}", ""]
    L += ["## Type confusion (truth -> what was read)"]
    c = Counter((x["truth_type"], x["read_type"] or "(not read)") for x in detail if x["read_type"] == "" or not x["type_ok"])
    for (t, r), n in sorted(c.items(), key=lambda kv: -kv[1]):
        L.append(f"- {t} -> {r}: {n}")
    L += ["", "## Every miss or error"]
    for x in detail:
        problems = []
        if x["truth_type"] == "no_move":
            if x["found"]:
                problems.append(f"FALSE ALARM: read as {x['read_type']}")
        elif not x["found"]:
            problems.append("NOT READ")
        elif not x["type_ok"]:
            problems.append(f"type {x['truth_type']} read as {x['read_type']}")
        if x["truth_type"] == "no_move":
            pass
        if x["link_ok"] == "WRONG PERSON":
            problems.append(f"WRONG LINK -> {x['link']}")
        if x["db_truth"] in ("yes", "likely") and x["found"] and x["match_status"] == "unmatched":
            problems.append("in database but left unmatched")
        if problems:
            L.append(f"- [{x['set']}] {x['player']} ({x['club']}, truth {x['truth_type']}): " + "; ".join(problems) + f" | {x['headline'][:70]}")
    return L


if __name__ == "__main__":
    main()
