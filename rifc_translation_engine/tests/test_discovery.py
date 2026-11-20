"""Discovery batch projections must equal the single player projection (SYNTHETIC data).

Run:  python -m pytest -q tests
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rifc_te_disc_"))
os.environ["RIFC_TE_HOME"] = str(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from engine import discovery as D  # noqa: E402
from engine.api import Engine, Scenario, player_from_agg  # noqa: E402
from engine.config import ModelSettings  # noqa: E402
from engine.ingest import ImportOptions, import_file  # noqa: E402
from engine.schema import USLC  # noqa: E402
from tests.synthetic import generate  # noqa: E402

DB = TMP / "disc.duckdb"


def test_batch_equals_single():
    for f, lg, s in generate(TMP / "fixtures"):
        import_file(f, ImportOptions("player_season", "Wyscout", lg, s), db_path=DB, raw_dir=TMP)
    eng = Engine.fit(ModelSettings(), db_path=DB)
    eng.run_backtest(db_path=DB)
    T = D.project_all(eng, 2200)
    agg = eng.agg.copy()
    agg["key"] = agg["player_name"] + "|" + agg["team"].astype(str) + "|" + agg["league"] + "|" + agg["season"]
    rng = np.random.default_rng(0)
    checked = 0
    for k in rng.choice(T["key"].to_numpy(), min(25, len(T)), replace=False):
        p = player_from_agg(agg[agg["key"] == k].iloc[0])
        one = eng.project(p, USLC, Scenario(minutes=2200)).set_index("metric")
        row = T[T["key"] == k].iloc[0]
        for m in one.index:
            a, b = one.loc[m].get("pred", np.nan), row[f"{m}_proj"]
            assert (np.isnan(a) and np.isnan(b)) or abs(a - b) < 1e-9, (k, m, a, b)
            assert one.loc[m, "confidence"] == row[f"{m}_conf"]
            checked += 1
    assert checked > 0


def test_identity_layer_matches_model():
    """Profiles start from the model's links; projections from profile records equal the model table's."""
    from engine import identity as I
    eng = Engine.load(db_path=DB) or Engine.fit(ModelSettings(), db_path=DB)
    idn = I.build(eng, path=DB)
    assert idn.audit["same_links_as_model"]
    again = I.build(eng, path=DB)
    assert dict(zip(idn.players["cluster"], idn.players["uid"])) == dict(zip(again.players["cluster"],
                                                                             again.players["uid"]))
    A = D.project_all(eng, 2200)
    B = D.project_all(eng, 2200, idn.records)
    B["pid"] = B["uid"].map(idn.players.set_index("uid")["model_pid"])
    m = A.merge(B, on=["pid", "league", "season", "provider"], suffixes=("_a", "_b"))
    assert len(m) == len(A)
    a, b = m["goals_proj_a"].astype(float), m["goals_proj_b"].astype(float)
    assert (np.isclose(a, b) | (a.isna() & b.isna())).all()
    # one search result per player
    name = idn.players["display_name"].iloc[0]
    res = I.search(idn, name)
    assert res["uid"].is_unique and res["display_name"].iloc[0] == name
