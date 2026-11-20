"""Click through every page headlessly on a SYNTHETIC temp database.

Run:  python tests/smoke_ui.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rifc_te_ui_"))
os.environ["RIFC_TE_HOME"] = str(TMP)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from engine import db  # noqa: E402
from engine.api import Engine  # noqa: E402
from engine.config import ModelSettings  # noqa: E402
from engine.ingest import ImportOptions, import_file  # noqa: E402
from tests.synthetic import generate  # noqa: E402

files = generate(TMP / "fixtures")
for f, lg, s in files:
    import_file(f, ImportOptions("player_season", "Wyscout", lg, s))
ps = db.load_table("player_seasons")
rng = np.random.default_rng(1)
teams = ps.groupby(["team", "league", "season"]).agg(goals=("goals_total", "sum")).reset_index()
teams["matches"] = 30
teams["penalties"] = rng.integers(2, 9, len(teams))
teams.loc[teams["team"] == teams["team"].iloc[0], "team"] = "Rhode Island FC"  # synthetic stand in
ps_team = teams["team"].iloc[0]
f = TMP / "teams.csv"
teams.rename(columns={"team": "Team", "league": "League", "season": "Season", "goals": "Goals",
                      "matches": "Matches", "penalties": "Penalties awarded"}).to_csv(f, index=False)
_, issues = import_file(f, ImportOptions("team_season", "Other"))
print("team import issues:", [i.message for i in issues if i.level != "info"])

eng = Engine.fit(ModelSettings())
eng.run_backtest()
print("context used:", {m: mm.design.use_context for m, mm in eng.models.items()})

import glob
pages = ["app.py"] + sorted(glob.glob(str(ROOT / "views" / "*.py")))
for page in pages:
    at = AppTest.from_file(str(ROOT / page), default_timeout=300)
    at.run()
    errs = [e.value for e in at.exception]
    print(f"{Path(page).name}: {'OK' if not errs else errs}")
    assert not errs, (page, errs)
