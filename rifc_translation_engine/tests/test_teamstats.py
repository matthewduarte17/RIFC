"""StatsBomb team importer checks on SYNTHETIC CSVs (fake clubs, made up numbers)."""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rifc_te_team_"))
os.environ["RIFC_TE_HOME"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from engine import teamstats as T  # noqa: E402

DB = TMP / "t.duckdb"
CLUBS = ["Alpha FC", "Beta SC", "Gamma"]


def _csv(cols: dict, games=(30, 31, 32), avg=True) -> bytes:
    df = pd.DataFrame({"Team Name": CLUBS, "Games": list(games), **cols, "Home Fixtures": "x", "Away Fixtures": "y"})
    if avg:
        df.loc[len(df)] = ["League Average", sum(games) / 3] + [pd.Series(v).mean() for v in cols.values()] + ["", ""]
    return df.to_csv(index=False).encode()


def _files(games=(30, 31, 32)):
    res = _csv({"Goal Difference": [1, 0, -1], "Goals": [40, 35, 30], "Non Penalty xG": [33.3, 30.1, 28.0],
                "Open Play xG": [25.0, 22.0, 21.0], "Penalty Goals Conceded": [1, 2, 3]}, games)
    sh = _csv({"Non Penalty Shots": [400, 350, 300], "Clear Shots": [60, 50, 40], "High Press Shots": [90, 80, 70],
               "Penalties": [5, 4, 3], "Penalty Goals Conceded": [2, 3, 4]}, games)
    pas = _csv({"Passing%": [78, 75, 70], "Deep Completions": [100, 90, 80], "Box Crosses": [300, 250, 200],
                "Passes": [15000, 14000, 12000], "Successful Passes": [11800, 10500, 8400]}, games)
    return [("res.csv", res), ("shoot.csv", sh), ("pass.csv", pas)]


def test_import_and_checks():
    b = T.build_batch(_files(), "USL Championship", "2030", "combined")
    assert not [c for c in b.checks if c.level == "error"], b.checks
    m = b.merged
    assert set(m["team"]) == set(CLUBS)                         # League Average removed
    goals = m[(m["metric"] == "Goals") & (m["team"] == "Alpha FC")].iloc[0]
    assert abs(goals["per_match"] - 40 / 30) < 1e-9             # totals per match
    pp = m[(m["metric"] == "Passing%") & (m["team"] == "Alpha FC")].iloc[0]
    assert abs(pp["value"] - 11800 / 15000 * 100) < 1e-9 and pp["reported"] == 78   # full precision kept beside reported
    assert pp["per_match"] != pp["per_match"]                     # percentages never divided by matches (NaN)
    assert "Penalties Faced" in set(m["metric"])                  # mislabeled shooting column renamed
    assert m.duplicated(["team", "metric"]).sum() == 0
    bid = T.commit_batch(b, path=DB, raw_dir=TMP)
    try:
        T.commit_batch(b, path=DB, raw_dir=TMP)
        assert False, "duplicate import allowed"
    except ValueError:
        pass
    bt = T.benchmark_table(T.load(DB), "2030", "combined")
    g = bt[bt["metric"] == "Goals"].set_index("team")
    assert g.loc["Alpha FC", "rank"] == 1


def test_season_required_and_mismatch():
    b = T.build_batch(_files(), "USL Championship", "", "regular")
    assert any(c.check == "season" and c.level == "error" for c in b.checks)
    assert any(c.check == "competition phase" and c.level == "warning" for c in b.checks)
    f = _files()
    f[1] = ("shoot.csv", _csv({"Non Penalty Shots": [1, 2, 3], "Clear Shots": [1, 1, 1], "High Press Shots": [1, 1, 1]},
                              games=(30, 31, 33)))
    b = T.build_batch(f, "USL Championship", "2030", "combined")
    assert any(c.check == "match counts" and c.level == "error" for c in b.checks)


if __name__ == "__main__":
    test_import_and_checks()
    test_season_required_and_mismatch()
    print("ok")
