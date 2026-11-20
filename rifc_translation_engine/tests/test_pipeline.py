"""End to end code check on SYNTHETIC fixtures in a temporary folder.

Verifies: import and mapping, per 90 normalisation, identity linking, pair
building, that planted league effects are recovered with the right sign, that
the backtest runs without look ahead, and the readiness gate. These results
say nothing about real world accuracy.

Run:  python -m pytest -q tests   (or: python tests/test_pipeline.py)
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rifc_te_test_"))
os.environ["RIFC_TE_HOME"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from engine import db  # noqa: E402
from engine.api import Engine, PlayerInput, Scenario  # noqa: E402
from engine.config import ModelSettings  # noqa: E402
from engine.ingest import ImportOptions, auto_map, import_file  # noqa: E402
from tests.synthetic import generate  # noqa: E402

DB = TMP / "test.duckdb"


def _load():
    files = generate(TMP / "fixtures")
    for f, lg, s in files:
        iid, issues = import_file(f, ImportOptions("player_season", "Wyscout", lg, s), db_path=DB,
                                  raw_dir=TMP)
        assert not [i for i in issues if i.level == "error"], issues
    return files


def test_mapping():
    maps = auto_map(["Player", "Team within selected timeframe", "Position", "Age", "Minutes played",
                     "Goals", "xG per 90", "Accurate passes, %", "Gls.1"], "player_season")
    got = {m.source: (m.target, m.unit) for m in maps}
    assert got["Player"][0] == "player_name"
    assert got["Minutes played"][0] == "minutes"
    assert got["xG per 90"] == ("xg", "p90")
    assert got["Accurate passes, %"] == ("pass_pct", "pct")
    assert got["Gls.1"] == ("goals", "p90")
    # Wyscout: season team beats current club; % columns never fuzzy match counts
    w = {m.source: (m.target, m.unit) for m in auto_map(
        ["Player", "Team", "Team within selected timeframe", "Aerial duels won, %", "Successful dribbles, %",
         "Aerial duels per 90", "Minutes played"], "player_season")}
    assert w["Team within selected timeframe"][0] == "team"
    assert w["Team"][0] == "current_team"
    assert w["Aerial duels won, %"] == ("aerial_win_pct", "pct")   # success rate, never a count
    assert w["Successful dribbles, %"][0] == "(ignore)"


def test_exact_keys_blanks():
    import pandas as pd
    from engine.ingest import exact_keys
    df = pd.DataFrame({"name_norm": ["a b", "c d"], "team": ["X", "X"], "league": "L", "season": "2026",
                       "provider": "W", "age": [float("nan")] * 2, "minutes": [900, 900], "matches": [10, 10],
                       "goals_total": [0, 0]})
    k = exact_keys(df)
    assert k.notna().all() and not k.duplicated().any()


def test_pipeline():
    _load()
    ps = db.load_table("player_seasons", DB)
    assert len(ps) > 3000
    r = ps.dropna(subset=["goals_total"]).iloc[0]
    assert abs(r["goals_p90"] - r["goals_total"] / r["minutes"] * 90) < 1e-9

    eng = Engine.fit(ModelSettings(), db_path=DB)
    pairs = eng.pairs
    assert (pairs["pair_type"] == "cross").sum() > 100
    # identity: each synthetic player should map to a single pid
    names_per_pid = eng.agg.groupby("pid")["player_name"].nunique()
    assert names_per_pid.max() == 1

    # Same player projected into each league: the gap should follow the planted effects
    import pandas as pd
    mm = eng.models["goals"]
    probe = pd.DataFrame([{"src_minutes": 2000, "dst_minutes": 2000, "position_group": "ST",
                           "src_league": "Test League A", "dst_league": L, "age_dst": 26,
                           "src_team_idx": 0, "dst_team_idx": 0, "dst_start_share": 0.85,
                           "src_goals": 0.45} for L in ("Test League A", "Test League B", "Test League C")])
    mu = mm.predict_t(probe)[0]
    b_minus_a, c_minus_a = mu[1] - mu[0], mu[2] - mu[0]
    print("recovered effects (planted +0.25, -0.20):", round(b_minus_a, 3), round(c_minus_a, 3))
    assert 0.08 < b_minus_a < 0.4 and -0.35 < c_minus_a < -0.05

    summary = eng.run_backtest(db_path=DB)
    g = summary.set_index("metric").loc["goals"]
    print(summary[["metric", "n_test", "mae_model", "mae_persistence", "mae_pair_ratio", "coverage80"]])
    assert g["n_test"] > 50
    assert g["mae_model"] < g["mae_persistence"]
    assert 0.6 < g["coverage80"] < 0.95

    p = PlayerInput("Test striker", "ST", "Test League A", "2025", 2500, age=24,
                    per90={"goals": 20 / 2500 * 90, "assists": 4 / 2500 * 90})
    out = eng.project(p, "Test League C", Scenario(minutes=2000))
    row = out.set_index("metric").loc["goals"]
    assert row["status"] in ("validated", "unvalidated")
    assert row["lo80"] <= row["pred"] <= row["hi80"]
    # Unknown league must be refused, not guessed
    out2 = eng.project(p, "Some Unseen League", Scenario())
    assert (out2["status"] == "insufficient").all()

    drv = eng.drivers(p, "goals", "Test League C", Scenario(minutes=2000))
    assert len(drv) >= 3

    eng2 = Engine.load(DB)
    assert eng2 is not None and eng2.validation


if __name__ == "__main__":
    test_mapping()
    test_pipeline()
    print("ok")
