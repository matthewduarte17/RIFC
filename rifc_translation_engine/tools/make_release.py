"""Build the release ZIP.

  python tools/make_release.py --version 2026.11.11 --out /path/to/rifc_translation_engine.zip

* copies the code, docs and data/ (the release's own data) into a clean folder named rifc_translation_engine;
* leaves out backups, caches, temporary model files and unreadable database copies;
* keeps only the model files the database refers to;
* rewrites the database in DuckDB storage format v1.0.0, so every DuckDB from 1.0 onward can open it
  (DuckDB 2.0 needs Python 3.12 or newer, and many Macs run an older Python);
* checks data/RELEASE.json carries the same version.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

import duckdb

APP = Path(__file__).resolve().parent.parent
NAME = "rifc_translation_engine"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".venv", "backups", "dist", ".DS_Store",
                              "engine_unreadable_*", "identity_*.pkl", "discovery_run*.pkl", "_scratch*",
                              "last_upgrade_report.txt", "*.wal")


def build(version: str, out: Path) -> Path:
    rel = json.loads((APP / "data" / "RELEASE.json").read_text())
    if rel["version"] != version:
        raise SystemExit(f"data/RELEASE.json says {rel['version']}, not {version}. Update it first.")
    tmp = Path(tempfile.mkdtemp(prefix="rifc_release_"))
    dst = tmp / NAME
    shutil.copytree(APP, dst, ignore=SKIP)
    db_file = dst / "data" / "engine.duckdb"
    db_file.unlink()
    src = tmp / "engine.duckdb"            # a copy, so the live database is never opened for writing
    shutil.copy2(APP / "data" / "engine.duckdb", src)
    con = duckdb.connect(str(src))
    con.execute(f"ATTACH '{db_file}' AS rel (STORAGE_VERSION 'v1.0.0')")
    con.execute("COPY FROM DATABASE engine TO rel")
    con.execute("DETACH rel")
    keep = {Path(p).name for (p,) in con.execute("SELECT artifact_path FROM model_runs UNION ALL "
                                                 "SELECT artifact_path FROM backtest_runs").fetchall()}
    con.close()
    for f in (dst / "data" / "models").glob("*.pkl"):
        if f.name not in keep:
            f.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(dst.rglob("*")):
            info = zipfile.ZipInfo.from_file(f, f.relative_to(tmp))
            if f.name.endswith(".command"):
                info.external_attr = (0o755 | 0o100000) << 16
            if f.is_dir():
                z.writestr(info, "")
            else:
                z.writestr(info, f.read_bytes(), zipfile.ZIP_DEFLATED)
    shutil.rmtree(tmp)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(build(a.version, Path(a.out)))
