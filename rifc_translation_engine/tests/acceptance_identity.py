"""Identity and discovery acceptance checks on YOUR real database (not synthetic).

Run:  python tests/acceptance_identity.py
Prints PASS or FAIL per check. Reads only; the verification check writes to a temporary copy of the database.
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from engine import discovery as D  # noqa: E402
from engine import identity as I  # noqa: E402
from engine.api import Engine  # noqa: E402
from engine.config import DB_PATH  # noqa: E402

eng = Engine.load()
idn = I.load_or_build(eng)
PL = idn.players.set_index("uid")
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))


def uid_of(name, team=None, season=None):
    r = idn.records[(idn.records["player_name"] == name) & ((idn.records["team"] == team) if team else True)
                    & ((idn.records["season"] == season) if season else True)]
    return r["uid"].unique().tolist()


# S. Marou
res = I.search(idn, "Marou")
check("Search 'Marou' returns one player", len(res) == 1, ", ".join(res["display_name"]))
u = res["uid"].iloc[0]
rec = I.records_of(idn, u)
check("Profile holds his 2025 and 2026 USL League One seasons as separate records",
      set(zip(rec["league"], rec["season"])) >= {("USL League One", "2025"), ("USL League One", "2026")},
      "; ".join(f"{r.season} {r.team} {int(r.minutes)} min" for r in rec.itertuples()))
le = I.latest_eligible(idn, 450).set_index("uid")
check("Latest eligible season is the default", le.loc[u, "season"] == "2026", le.loc[u, "season"])
T = D.project_all(eng, 2200, idn.records)
cur = T[T["rec_key"].isin(set(I.latest_eligible(idn, 900)["rec_key"]))]
check("Default discovery view shows him once", int((cur["uid"] == u).sum()) == 1)
check("Historical mode has both seasons", int((T["uid"] == u).sum()) == 2)
check("Last recorded club is not treated as verified", not I.club_status(idn, u)["verified"],
      I.club_status(idn, u)["status"])

# changed clubs
u = uid_of("Arturo Ordóñez")
clubs = PL.loc[u[0], "clubs"] if len(u) == 1 else []
check("Player who changed clubs: one profile, every club", len(u) == 1 and len(clubs) >= 3, " to ".join(clubs))

# changed leagues
u = uid_of("R. Carmichael")
check("Player who changed leagues: one profile across MLS NEXT Pro and League One",
      len(u) == 1 and set(PL.loc[u[0], "leagues"]) >= {"MLS Next Pro", "USL League One"}, ", ".join(PL.loc[u[0], "leagues"]))

# two competitions in one season
m = idn.records.groupby(["uid", "season"])["league"].nunique()
ex = m[m > 1].index[0]
r2 = idn.records[(idn.records["uid"] == ex[0]) & (idn.records["season"] == ex[1])]
check("Two competitions in one season stay separate records", len(r2) == 2,
      f"{PL.loc[ex[0], 'display_name']} {ex[1]}: " + ", ".join(f"{r.league} {r.team} {int(r.minutes)}" for r in r2.itertuples()))

# similar names, different players
g = uid_of("J. Garcia")
check("J. Garcia (Monterey Bay and Pittsburgh in the same seasons) split into separate players", len(g) >= 2,
      "; ".join(f"{PL.loc[x, 'display_name']} {', '.join(PL.loc[x, 'clubs'])}" for x in g))
p = [x for x in PL.index if PL.loc[x, "display_name"] == "J. Pérez"]
check("J. Pérez stays two players (existing rule)", len(p) >= 2,
      "; ".join(", ".join(PL.loc[x, "clubs"]) for x in p))
w = [x for x in PL.index if PL.loc[x, "display_name"] == "O. Wright"]
check("O. Wright stays one player (earlier fix)", len(w) == 1, ", ".join(PL.loc[w[0], "clubs"]) if w else "")

# most recent league season excluded (MLS NEXT Pro 2026)
x = PL[(PL["last_league"] == "MLS Next Pro") & (PL["last_season"] == "2025")].sort_values("total_minutes").index[-1]
check("Player missing from the excluded or partial MLS NEXT Pro 2026 data is labelled, not shown as current in 2026",
      PL.loc[x, "recency"].endswith("not imported") or PL.loc[x, "recency"].startswith("Not in the partial"),
      f"{PL.loc[x, 'display_name']}: {PL.loc[x, 'recency']}")
u = uid_of("J. Barea", "Real Monarchs", "2025")
check("J. Barea: MLS NEXT Pro 2025 and League One 2026 in one profile",
      len(u) == 1 and set(PL.loc[u[0], "leagues"]) == {"MLS Next Pro", "USL League One"}, ", ".join(PL.loc[u[0], "clubs"]))

# no 2026 record
old = PL[(PL["last_season"] == "2024")]
x = old.index[0]
check("Player with no 2026 record is in the historical pool, not shown as current",
      not PL.loc[x, "recency"] == "In the latest data", f"{PL.loc[x, 'display_name']}: {PL.loc[x, 'recency']}")

# current club unknown, then manual verification on a temporary copy
tmp = Path(tempfile.mkdtemp()) / "copy.duckdb"
shutil.copy(DB_PATH, tmp)
u = res["uid"].iloc[0]
before = I.club_status(idn, u, path=tmp)
I.add_verification(u, "At a club (verified)", "Test Club", date.today(), "Acceptance test", "2027-11-30", date.today(),
                   "test", path=tmp)
after = I.club_status(idn, u, path=tmp)
check("Unknown current club shows as unverified; a manual entry verifies it without touching the last recorded club",
      (not before["verified"]) and after["verified"] and after["last_club"] == before["last_club"],
      f"{before['status']} -> {after['current_club']} ({after['source']}); last recorded still {after['last_club']}")

# stable IDs and model untouched
again = I.build(eng)
check("Player IDs are stable across rebuilds",
      dict(zip(idn.players["cluster"], idn.players["uid"])) == dict(zip(again.players["cluster"], again.players["uid"])))
check("Identity layer starts from the model's links", idn.audit["same_links_as_model"])
print(f"\n{sum(results)} of {len(results)} passed")
