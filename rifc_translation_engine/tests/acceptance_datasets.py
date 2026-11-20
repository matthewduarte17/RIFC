"""Dataset use levels, on a COPY of your data (set RIFC_TE_HOME to a folder holding a copy of data/).

Run:  RIFC_TE_HOME=/path/to/copy python tests/acceptance_datasets.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
assert os.environ.get("RIFC_TE_HOME"), "Run on a copy: set RIFC_TE_HOME"

from engine import datasets as DS  # noqa: E402
from engine import discovery as D  # noqa: E402
from engine import identity as I  # noqa: E402
from engine.api import Engine  # noqa: E402
from engine.transfers import build_pairs  # noqa: E402

res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))


eng = Engine.load()
base_pairs, _, _ = build_pairs(eng.settings)
n0 = (len(base_pairs), int((base_pairs["pair_type"] == "cross").sum()))
idn0 = I.build(eng)
get = lambda: next(d for d in DS.load() if d.league == "MLS Next Pro" and d.season == "2026")
d = get()
check("MLS NEXT Pro 2026 starts excluded and invalid", d.level == "Excluded" and d.status == "Invalid", d.headline)
check("Excluded: no 2026 MLS NEXT Pro records in profiles or discovery",
      not ((idn0.records["league"] == "MLS Next Pro") & (idn0.records["season"] == "2026")).any())

DS.set_level(d, "Profiles and discovery only", "test")
check("Profiles and discovery only does not make the model outdated", not eng.is_stale())
p1, _, _ = build_pairs(eng.settings)
check("Training pairs unchanged (no contamination)", (len(p1), int((p1["pair_type"] == "cross").sum())) == n0,
      f"{n0[0]} pairs, {n0[1]} cross league")
idn1 = I.build(eng)
r26 = idn1.records[(idn1.records["league"] == "MLS Next Pro") & (idn1.records["season"] == "2026")]
check("2026 MLS NEXT Pro players appear in profiles and discovery, labelled display only",
      len(r26) > 900 and r26["display_only"].all(), f"{len(r26)} season records")
check("Active rows are still linked exactly as the model links them", idn1.audit["same_links_as_model"])
T = D.project_all(eng, 2200, idn1.records)
t26 = T[(T["league"] == "MLS Next Pro") & (T["season"] == "2026")]
check("Projections exist for them from the unchanged model", t26["xg_proj"].notna().sum() > 500,
      f"{int(t26['xg_proj'].notna().sum())} with an xG projection")
pl = idn1.players.set_index("uid")
partial = pl[pl["recency"].str.startswith("Not in the partial")]
check("MLS NEXT Pro 2025 players missing from the partial 2026 export are labelled, not called stale",
      len(partial) > 0 and partial["recent"].all(), f"{len(partial)} players: {partial['recency'].iloc[0]}")
try:
    DS.set_level(get(), "Full use")
    check("Full use of an invalid dataset is refused without an override", False)
except ValueError:
    check("Full use of an invalid dataset is refused without an override", True)
DS.set_level(get(), "Full use", override="test override")
check("Full use (with override) makes the model outdated", Engine.load().is_stale())
p2, _, _ = build_pairs(eng.settings)
check("Full use changes the training pairs (so a refit is needed)", len(p2) != n0[0],
      f"{n0[0]} to {len(p2)} pairs")
DS.set_level(get(), "Excluded", "test back")
check("Back to Excluded: model current again, pairs restored", not Engine.load().is_stale()
      and len(build_pairs(eng.settings)[0]) == n0[0])
print(f"\n{sum(res)} of {len(res)} passed")
