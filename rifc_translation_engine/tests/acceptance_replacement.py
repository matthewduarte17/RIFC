"""Replacement workflow for a capped age band, on a COPY of your data (set RIFC_TE_HOME).

The corrected Wyscout files do not exist yet, so this test splits the existing ages 20 to 25 file into
20 to 22 and 23 to 25. It checks the mechanics only: no duplicate player seasons, the replaced file kept but
unused, the season's use level carried over, validation run on the merged season, the model untouched.

Run:  RIFC_TE_HOME=/path/to/copy python tests/acceptance_replacement.py /path/to/original/data/raw
"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
assert os.environ.get("RIFC_TE_HOME"), "Run on a copy: set RIFC_TE_HOME"
import pandas as pd  # noqa: E402

from engine import datasets as DS  # noqa: E402
from engine import db  # noqa: E402
from engine.api import Engine  # noqa: E402
from engine.config import DATA_DIR  # noqa: E402
from engine.ingest import ImportOptions, import_file  # noqa: E402
from engine.transfers import build_pairs  # noqa: E402

RAW = Path(sys.argv[1])
res = []


def check(name, ok, detail=""):
    res.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""))


get = lambda: next(d for d in DS.load() if d.league == "MLS Next Pro" and d.season == "2026")
eng = Engine.load()
n_pairs = len(build_pairs(eng.settings)[0])
DS.set_level(get(), "Profiles and discovery only", "test")
d = get()
old = d.imports[d.imports["filename"].str.contains("20-25")]
old_id = int(old["import_id"].iloc[0])
src = next(RAW.glob("*2026_MLSNP_Export_20-25*"))
x = pd.read_excel(src)
uploads = []
for lo, hi in ((20, 22), (23, 25)):
    part = x[(x["Age"] >= lo) & (x["Age"] <= hi)]
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as w:
        part.to_excel(w, index=False, sheet_name=f"Search results ({len(part)})")
    uploads.append((f"2026_MLSNP_Export_{lo}-{hi}.xlsx", buf.getvalue()))
pv = DS.preview_replacement(d, [old_id], uploads)
check("Preview: merged season validated before anything is written", pv["ok"] and not pv["problems"],
      f"files {[(f['file'], f['rows']) for f in pv['files']]}, {pv['old_rows_found']} of {pv['old_rows']} old rows "
      f"found, {pv['overlap_with_kept']} overlap with kept files, merged {pv['merged_players']} players")
before = db.query("SELECT COUNT(*) n FROM player_seasons WHERE league = 'MLS Next Pro' AND season = '2026'")["n"][0]
new_ids = DS.commit_replacement(d, [old_id], uploads, DATA_DIR / "tmp_uploads")
im = db.query(f"SELECT import_id, active, display_only, replaced_by, n_rows FROM imports WHERE import_id IN "
              f"({old_id},{','.join(map(str, new_ids))})")
check("New files stored with the season's level (profiles and discovery only), not Full use",
      (~im[im["import_id"].isin(new_ids)]["active"]).all() and im[im["import_id"].isin(new_ids)]["display_only"].all(),
      f"rows stored {im[im['import_id'].isin(new_ids)]['n_rows'].tolist()}")
check("Replaced file kept but excluded and marked", not im[im["import_id"] == old_id]["active"].iloc[0]
      and str(im[im["import_id"] == old_id]["replaced_by"].iloc[0]) == ",".join(map(str, new_ids)))
d2 = get()
rows = db.query("SELECT name_norm, team, minutes FROM player_seasons WHERE import_id IN (" +
                ",".join(str(int(i)) for i in d2.imports["import_id"]) + ")")
check("No duplicate player seasons in the season's files in use", not rows.duplicated().any() and len(rows) == 1053,
      f"{len(rows)} rows in use (stored in total: {before} before, replaced file kept)")
check("Season files in use exclude the replaced file", old_id not in set(d2.imports["import_id"]),
      ", ".join(d2.imports["filename"]))
check("Merged season revalidated (no file at the export cap in this simulation)",
      [c.level for c in d2.checks if c.name == "Export cap"] == ["pass"], d2.status)
check("Model untouched: not outdated, pairs unchanged", not Engine.load().is_stale()
      and len(build_pairs(eng.settings)[0]) == n_pairs, f"{n_pairs} pairs")
# a later plain import into this season inherits its level
part = x.head(3)
buf = io.BytesIO()
with pd.ExcelWriter(buf) as w:
    part.assign(Player=part["Player"] + " Test").to_excel(w, index=False, sheet_name="Search results (3)")
p = DATA_DIR / "tmp_uploads" / "extra.xlsx"
p.write_bytes(buf.getvalue())
iid, _ = import_file(p, ImportOptions("player_season", "Wyscout", "MLS Next Pro", "2026", age_reference="export",
                                      export_year=2026.85))
r = db.query(f"SELECT active, display_only FROM imports WHERE import_id = {iid}")
check("A plain import into a season that is not in Full use cannot reach the model",
      not r["active"][0] and r["display_only"][0])
print(f"\n{sum(res)} of {len(res)} passed")
