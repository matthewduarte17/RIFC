"""Safe upgrade, end to end, on simulated Macs in a temporary folder. Your real data is only read.

Run:  python tests/acceptance_upgrade.py /path/to/rifc_translation_engine.zip [/path/to/old_backup_folder]

The old backup folder (default backups/pre_identity_20261107_180406) stands in for an installation from an
earlier ZIP: database, manifests and identity rules as they were then, with the shortlist in its old format.
Scenarios
  1  first start of the new version next to an old installation (data inside the old app folder)
  2  later upgrade of the data home after a season of recruitment work (shortlist, notes, verifications,
     identity decisions and rules, use level changes, a removed file, your own import, a StatsBomb batch off)
  3  same version again: nothing changes
  4  an older ZIP: nothing changes
  5  a check fails (your own file's original is missing): nothing changes, the merged copy is kept to inspect
  6  clean start with no installation anywhere
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ZIP = Path(sys.argv[1]).resolve()
OLD = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "backups" / "pre_identity_20261107_180406"
W = Path(tempfile.mkdtemp(prefix="rifc_upgrade_test_"))
PY = sys.executable
res = []


def check(name, ok, detail=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail else ""), flush=True)


def tree_hash(folder: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(folder.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(folder)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def unzip(dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(dest)
    return dest / "rifc_translation_engine"


def launch(bundle: Path, home: Path, fake_home: Path, extra=()):
    env = dict(os.environ, HOME=str(fake_home))
    env.pop("RIFC_TE_HOME", None)
    p = subprocess.run([PY, "-m", "engine.upgrade", "--bundle", str(bundle), "--home", str(home), *extra],
                       cwd=str(bundle), env=env, capture_output=True, text=True, input="\n")
    return p.returncode, p.stdout + p.stderr


def run_in(home: Path, bundle: Path, code: str) -> str:
    env = dict(os.environ, RIFC_TE_HOME=str(home), PYTHONPATH=str(bundle))
    p = subprocess.run([PY, "-c", code], cwd=str(bundle), env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-3000:])
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""


STATE = """
import json, duckdb, shutil, tempfile, os
from pathlib import Path
d = Path(os.environ['RIFC_TE_HOME']) / 'data'
t = Path(tempfile.mkdtemp()) / 'c.duckdb'; shutil.copy2(d / 'engine.duckdb', t)
c = duckdb.connect(str(t), read_only=True)
q = lambda s: c.execute(s).fetchall()
lv = lambda a, x: 'Full use' if a else ('Profiles and discovery only' if x else 'Excluded')
out = {
 'mlsnp26': sorted({lv(a, x) for a, x in q("SELECT active, COALESCE(display_only, FALSE) FROM imports WHERE file_league='MLS Next Pro' AND file_season='2026'")}),
 'n_mlsnp26': q("SELECT COUNT(*) FROM imports WHERE file_league='MLS Next Pro' AND file_season='2026'")[0][0],
 'shortlist': [list(map(str, r)) for r in q("SELECT key, role, note FROM shortlist ORDER BY key")],
 'verif': [list(map(str, r)) for r in q("SELECT uid, status, current_club, verified_on, source, note FROM club_verifications")],
 'version': q("SELECT value FROM app_meta WHERE key='data_version'")[0][0],
 'levels': {f: lv(a, x) for f, a, x in q("SELECT filename, active, COALESCE(display_only, FALSE) FROM imports")},
 'reasons': {f: r for f, r in q("SELECT filename, exclusion_reason FROM imports")},
 'rows': {f: n for f, n in q("SELECT i.filename, COUNT(p.row_id) FROM imports i LEFT JOIN player_seasons p USING (import_id) GROUP BY 1")},
 'batches': [list(map(str, r)) for r in q("SELECT season, phase, active FROM team_stat_batches ORDER BY season, phase")],
 'decisions': json.loads((d / 'identity_decisions.json').read_text()).get('decisions', []) if (d / 'identity_decisions.json').exists() else [],
 'rules': [r['player'] for r in json.loads((d / 'identity_overrides.json').read_text())['do_not_link']],
}
print(json.dumps(out, default=str))
"""


def state(home: Path, bundle: Path) -> dict:
    return json.loads(run_in(home, bundle, STATE))


# ---------------------------------------------------------------------------
print(f"Work folder: {W}")
FH = W / "mac"                                       # the simulated home folder
DL = FH / "Downloads"
old_app = DL / "rifc_translation_engine"             # the installation from an earlier ZIP
(old_app / "data" / "models").mkdir(parents=True)
(old_app / "start.command").write_text("#!/bin/bash\n")
for f in ("engine.duckdb", "identity_overrides.json", "import_manifest.json", "team_import_manifest.json"):
    if (OLD / f).exists():
        shutil.copy2(OLD / f, old_app / "data" / f)
for f in (OLD / "models").glob("*"):
    shutil.copy2(f, old_app / "data" / "models" / f.name)
shutil.copytree(ROOT / "data" / "raw", old_app / "data" / "raw")
old_hash = tree_hash(old_app)
bundle = unzip(DL / "new")                           # the new ZIP, unzipped next to the old folder
bundle_hash = tree_hash(bundle / "data")
REL = json.loads((bundle / "data" / "RELEASE.json").read_text())
HOME = FH / "RIFC Translation Engine"

print("\nScenario 1: first start next to an old installation")
before = state_dir = None
rc, out = launch(bundle, HOME, FH, ["--yes"])
print("\n".join("   | " + x for x in out.strip().splitlines()))
check("1 finds the old installation", "Found an existing installation" in out and str(old_app) in out)
check("1 upgrade succeeds", rc == 0 and (HOME / "data" / "engine.duckdb").exists(), f"exit {rc}")
check("1 old installation untouched", tree_hash(old_app) == old_hash)
check("1 ZIP data untouched", tree_hash(bundle / "data") == bundle_hash)
s1 = state(HOME, bundle)
check("1 MLS NEXT Pro 2026: 3 files, Profiles and discovery only",
      s1["n_mlsnp26"] == 3 and s1["mlsnp26"] == ["Profiles and discovery only"], s1["mlsnp26"])
check("1 shortlist kept (old format keys, notes)", len(s1["shortlist"]) == 2, s1["shortlist"])
check("1 data version recorded", s1["version"] == REL["version"], s1["version"])
bk = sorted((HOME / "backups").glob("pre_upgrade_*"))
check("1 backup of the old data with a report", bk and (bk[-1] / "data" / "engine.duckdb").exists()
      and (bk[-1] / "upgrade_report.txt").exists())
mig = run_in(HOME, bundle, """
from engine.api import Engine
from engine import identity as I, recruit as R
eng = Engine.load(); idn = I.build(eng); n = R.shortlist_migrate(idn); sl = R.shortlist()
names = [idn.players.set_index('uid').loc[k, 'display_name'] for k in sl['key'] if k in set(idn.players['uid'])]
print(f"{n}|{len(sl)}|{', '.join(sorted(names))}|{eng.is_stale()}")""").split("|")
check("1 app moves the old shortlist keys to player IDs", mig[1] == "2" and len(mig[2].split(", ")) == 2,
      f"{mig[0]} moved, players: {mig[2]}")
check("1 model ready without a refit", mig[3] == "False")

print("\nScenario 2: data home upgrade after recruitment work")
work = run_in(HOME, bundle, """
import json, pandas as pd
from pathlib import Path
from engine import db, identity as I, recruit as R, teamstats as T
from engine.config import DATA_DIR, RAW_DIR
from engine.api import Engine
from engine.ingest import ImportOptions, import_file
from engine.upgrade import set_meta
eng = Engine.load(); idn = I.build(eng)
p = idn.players.sort_values('uid')
u1, u2, u3 = p['uid'].iloc[10], p['uid'].iloc[20], p['uid'].iloc[30]
R.shortlist_add(u3, 'Winger', 'Watch v Hartford, strong 1v1. Café test, 3 key passes.')
I.add_verification(u1, 'At a club (verified)', 'Test FC', '2026-11-01', 'Club website', '2027', None, 'checked by phone')
I.save_decision('confirm', [u2], 'same player, checked birth date')
rules = json.loads((DATA_DIR / 'identity_overrides.json').read_text())
rules['do_not_link'].append({'player': 'TEST RULE', 'reason': 'test', 'groups': [[{'team': 'A', 'league': 'B', 'season': '2024'}], [{'team': 'C', 'league': 'B', 'season': '2025'}]]})
(DATA_DIR / 'identity_overrides.json').write_text(json.dumps(rules, indent=1))
im = db.query('SELECT * FROM imports ORDER BY import_id')
u1_23 = im[(im.file_league == 'USL League One') & (im.file_season == '2023')]['import_id'].tolist()
db.set_import_active(u1_23, False, 'Test: excluded by the scout')
gone = im[(im.file_league == 'USL Championship') & (im.file_season == '2023')]['filename'].iloc[-1]
db.delete_import(int(im[im.filename == gone]['import_id'].iloc[0]))
# your own import: a SYNTHETIC test file (names prefixed TEST), never shipped
from engine.upgrade import _rel_raw
src = Path(RAW_DIR) / _rel_raw(im[(im.file_league == 'MLS Next Pro') & (im.file_season == '2025')]['stored_path'].iloc[0])
raw = pd.read_excel(src).head(25)
raw['Player'] = 'TEST ' + raw['Player'].astype(str)
f = Path(DATA_DIR).parent / 'my_scouting_test_file.csv'; raw.to_csv(f, index=False)
iid, _ = import_file(f, ImportOptions('player_season', 'Wyscout', 'MLS Next Pro', '2025', notes='my own file'))
b = T.batches(); T.set_batch_active(int(b['batch_id'].iloc[0]), False)
set_meta('data_version', '2026.11.10')
print(json.dumps({'u': [u1, u2, u3], 'gone': gone, 'own': f'{iid:05d}_my_scouting_test_file.csv', 'own_rows': int(db.query(f'SELECT COUNT(*) n FROM player_seasons WHERE import_id={iid}')['n'][0])}))
""")
work = json.loads(work)
s2b = state(HOME, bundle)
live_hash = tree_hash(HOME / "data")
rc, out = launch(bundle, HOME, FH)
print("\n".join("   | " + x for x in out.strip().splitlines()))
check("2 upgrade succeeds", rc == 0, f"exit {rc}")
s2 = state(HOME, bundle)
check("2 shortlist identical (keys, roles, notes)", s2["shortlist"] == s2b["shortlist"], f"{len(s2['shortlist'])} entries")
check("2 club verification kept", s2["verif"] == s2b["verif"] and len(s2["verif"]) == 1, s2["verif"])
check("2 identity decision kept", s2["decisions"] == s2b["decisions"] and len(s2["decisions"]) == 1)
check("2 your identity rule kept", "TEST RULE" in s2["rules"], s2["rules"])
u1f = [f for f, lv in s2b["levels"].items() if "USL_League_1" in f and "2023" in f]
check("2 your use level change kept (USL League One 2023 Excluded, with reason)",
      all(s2["levels"][f] == "Excluded" and s2["reasons"][f] == "Test: excluded by the scout" for f in u1f), u1f)
check("2 removed file stays removed", work["gone"] not in s2["levels"], work["gone"])
own = [f for f in s2["levels"] if f.endswith("my_scouting_test_file.csv")]
check("2 your own import kept with its rows and file name", own == ["my_scouting_test_file.csv"] and s2["rows"][own[0]] == work["own_rows"],
      f"{work['own_rows']} rows")
check("2 StatsBomb batch you switched off stays off", s2["batches"] == s2b["batches"], s2["batches"])
check("2 MLS NEXT Pro 2026 still Profiles and discovery only", s2["mlsnp26"] == ["Profiles and discovery only"])
bk2 = sorted((HOME / "backups").glob("pre_upgrade_*"))[-1]
check("2 previous data moved to the backup unchanged", tree_hash(bk2 / "data") == live_hash, bk2.name)
check("2 version moved forward", s2["version"] == REL["version"])
check("2 report in the data folder", (HOME / "data" / "last_upgrade_report.txt").exists())
app = run_in(HOME, bundle, """
from engine.api import Engine
from engine import identity as I, discovery as D, recruit as R
eng = Engine.load(); idn = I.build(eng)
r = idn.records
m = r[(r.league == 'MLS Next Pro') & (r.season == '2026')]
sl = R.shortlist()
print(f"{len(m)}|{bool(m['display_only'].all())}|{all(k in set(idn.players['uid']) for k in sl['key'])}|{eng.is_stale()}")""").split("|")
check("2 app: 2026 MLS NEXT Pro players in profiles, marked partial", int(app[0]) > 0 and app[1] == "True",
      f"{app[0]} player records")
check("2 app: every shortlist entry opens a profile", app[2] == "True")
check("2 app: model flagged outdated, because your training files differ", app[3] == "True")

print("\nScenario 3: same version again")
h = tree_hash(HOME / "data")
rc, out = launch(bundle, HOME, FH)
check("3 nothing to do, nothing changed", rc == 0 and out.strip() == "" and tree_hash(HOME / "data") == h, out.strip())

print("\nScenario 4: an older ZIP")
older = unzip(W / "older")
r = json.loads((older / "data" / "RELEASE.json").read_text()); r["version"] = "2026.11.1"
(older / "data" / "RELEASE.json").write_text(json.dumps(r))
rc, out = launch(older, HOME, FH)
check("4 refuses, nothing changed", rc == 2 and tree_hash(HOME / "data") == h, out.strip())

print("\nScenario 5: a check fails")
own_raw = next((HOME / "data" / "raw").glob("*my_scouting_test_file.csv"))
own_raw.rename(W / "moved_away.csv")
newer = unzip(W / "newer")
r = json.loads((newer / "data" / "RELEASE.json").read_text()); r["version"] = "2026.12.1"
(newer / "data" / "RELEASE.json").write_text(json.dumps(r))
h = tree_hash(HOME / "data")
rc, out = launch(newer, HOME, FH)
print("\n".join("   | " + x for x in out.strip().splitlines() if "FAIL" in x or "MISSING" in x or "untouched" in x))
check("5 stops, data untouched", rc == 1 and tree_hash(HOME / "data") == h, f"exit {rc}")
check("5 merged copy kept for inspection", any((HOME / "backups").glob("failed_upgrade_*")))
(W / "moved_away.csv").rename(own_raw)

print("\nScenario 6: clean start, no installation anywhere")
FH6 = W / "mac6"
(FH6 / "Downloads").mkdir(parents=True)
b6 = unzip(FH6 / "Downloads")
rc, out = launch(b6, FH6 / "RIFC Translation Engine", FH6)
s6 = state(FH6 / "RIFC Translation Engine", b6)
check("6 sets up the data folder from the release", rc == 0 and s6["version"] == REL["version"], out.strip())
check("6 MLS NEXT Pro 2026 Profiles and discovery only", s6["mlsnp26"] == ["Profiles and discovery only"])

print(f"\n{sum(res)} of {len(res)} checks passed")
sys.exit(0 if all(res) else 1)
