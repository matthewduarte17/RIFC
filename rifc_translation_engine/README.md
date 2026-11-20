# RIFC Player Translation Engine

Local app that projects how a player's per 90 production translates into the USL Championship and Rhode Island FC, learned from players who actually moved between leagues. No hand set league factors; projections are refused when the data cannot support them.

## Start (MacBook)

1. Unzip `rifc_translation_engine.zip` anywhere, for example Downloads or Documents.
2. Double click `start.command`. The first run sets everything up (a few minutes, needs internet); later runs start in seconds.
3. Your browser opens at http://localhost:8501. Keep the Terminal window open while you use the app; close it to stop.

If macOS says the file is from an unidentified developer: right click `start.command`, choose Open, then Open again. If Python is missing, the window tells you where to get it (python.org).

## Where your data lives, and updating to a new version

Your data is kept in its own folder in your home folder, **`RIFC Translation Engine`**, not in the app folder:

* `data/` the live database (`engine.duckdb`), the original copy of every file you imported (`raw/`), fitted models, identity rules and decisions;
* `backups/` a full copy of `data/` taken before every upgrade (never deleted automatically);
* `.venv/` the Python packages the app uses.

The `data/` folder inside the app folder is only the release's starting data. The app never runs on it and never writes to it.

**To update:** unzip the new ZIP and double click its `start.command`. That is all. You never copy database files by hand. On each start the launcher:

1. compares your data's version with the app's version (Data Library shows both at the bottom);
2. if the app is newer, builds the upgraded data in a separate staging folder: the release's data, then everything that is yours on top of it, namely your shortlist with roles and notes, club verifications, identity decisions and rules, player IDs, each file's use level and reason, files you imported yourself (reimported from your own `raw/` copies with the same settings), StatsBomb batches, and files you removed (they stay removed);
3. checks every item against your current data and prints the result; if any check fails it stops, changes nothing and keeps the merged copy in `backups/failed_upgrade_<date>` for inspection;
4. only then moves your current `data/` into `backups/pre_upgrade_<date>` and switches to the upgraded one. The report is saved in both places (`last_upgrade_report.txt`).

If you start an older app folder by mistake, it refuses to touch newer data.

A release may change a dataset's use level (listed in `data/RELEASE.json`). This release moves MLS NEXT Pro 2026 to Profiles and discovery only. A level you set yourself after this release is kept.

**First update from an older version** (where the data was inside the app folder): do not delete or replace the old folder. Unzip the new version next to it (macOS names it `rifc_translation_engine 2`), close the old app's Terminal window, and double click the new `start.command`. The launcher finds the old folder (in the same place, Desktop, Downloads or Documents), shows what it holds and asks which one to keep; press Return. The old folder is only read, so you can delete it once you are happy.

To restore a backup: quit the app, rename `data` in `RIFC Translation Engine` to anything else, and copy `backups/<name>/data` back in its place.

This release ships with Wyscout USL Championship and USL League One 2023 to 2026, MLS Next Pro 2024 and 2025 (Full use) and 2026 (Profiles and discovery only, partial export), and StatsBomb Championship team data 2024 to 2026 already imported, fitted and backtested (see RELEASE_NOTES.md and MODEL_NOTES.md). The app opens on Recruitment Discovery, in a dark theme (set in `.streamlit/config.toml`). Your existing scouting sheets and databases are never written to.

Building a release: `python tools/make_release.py --version <version> --out <zip>` after setting the same version in `data/RELEASE.json`. It writes the database in DuckDB storage format 1.0, so Macs with an older Python (and so an older DuckDB) can open it.

## Pages

**Recruitment**
* **Recruitment Discovery** (opens first): one point per player, from his own latest eligible season, with a league filter (default League One and MLS NEXT Pro), current and historical pools, and a Historical seasons mode. Choose the position, projected Championship or current league numbers, a preset view or your own axes, colour, size by minutes, filters and name search. RIFC players are teal stars at their observed numbers. Click or box select players to see them, open a profile or add them to the shortlist.
* **Potential Targets**: pick a role and a recruitment priority (preset or your own criteria). Validated mode uses only High or Medium confidence projections; Exploratory watchlist uses everything and is labelled as such. Each player shows why he surfaced, video priorities and the RIFC comparison. StatsBomb RIFC team context sits at the top.
* **Player Profile**: one profile per player with every season (career table, trends, club history), last recorded club kept apart from the current club (unverified until you record a verification), then the projection for the season you choose. Full header (nothing truncated), a plain sentence on the headline projection, projection cards for the role, "Why this projection?" with the model's own steps, percentile strips against Championship players and RIFC players, a separate uncertainty chart, a minutes scenario, RIFC comparison, scouting notes, the full table and the one page report.
* **Compare with RIFC**: a candidate against two to four RIFC players, with takeaways, striker questions and per metric bars (projected, current, RIFC observed, Championship band and median).
* **Recruitment Shortlist**: saved candidates side by side at standard minutes.
* **RIFC Team Profile** and **Championship Team Benchmarks**: StatsBomb team data by season.
* **Model Reliability**: tested moves per transition, verdict per metric and transition, model versus simple methods, methodology.

**Admin**
* Identity Review: how season rows became players, the review list and manual decisions.
* Data and Model Status, Import Player Data, Import Team Data, Data Library, Translation Model, Backtest, Validation Audit, Team Context Test.

Order after new data: import, refit (Translation Model), Backtest, Team Context Test.

## Recruitment monitor (new)

Reads public recruitment news, connects it to the players already in this database, keeps a **Recruitment Inbox** and a **Daily Brief**
(Recruitment menu) and a **Monitor Status** page (Admin menu). It runs a catch-up scan each time you start the app and can run a
daily scan on its own. It never changes the model, benchmarks, datasets, player IDs or shortlist except when you press a button.
See `README_MONITOR.md`.

## How the model works

For each metric, one regression on paired seasons (source season, next season):

* **Scale**: log(per 90 + small offset) for counts, logit for percentages, so effects are proportional.
* **Regression to the mean**: the slope on the player's current number, with an interaction for minutes played. A 20 goal season on 2,500 minutes is partly signal and partly variance; the data decides how much.
* **League effects**: a destination effect and a source effect per league, plus position specific versions and league pair deviations. Estimated jointly from every move, so a pair with few direct movers borrows strength through other leagues.
* **Partial pooling**: Gaussian priors (ridge penalties) on each block. Thin evidence shrinks toward "no adjustment" instead of producing extreme factors. The overall prior strength is tuned by time based cross validation.
* **Covariates**: age curve; team attacking index of the old and new team (when team data exists); start share (role).
* **Fitting**: closed form weighted ridge in NumPy rather than scikit-learn, because the closed form gives the coefficient covariance the intervals need.
* **Uncertainty**: parameter uncertainty plus outcome noise that grows as minutes shrink (fitted on leave one out residuals). Intervals are prediction intervals for the per 90 the player will actually post.

Status shown on every projected metric:

* **Validated**: enough data, backtest beats carrying the current per 90 over, and 80% intervals covered 70% to 90% of outcomes.
* **Not validated**: fitted but the backtest is missing, too small, or failed a check. Hidden unless you tick "Show estimates that are not validated".
* **Insufficient data**: not projected. The reason is listed.

### RIFC layer

Neutral projection = average Championship team. Team specific projection = same model with the chosen team's attacking index (learned effect, adjustable in the sidebar). RIFC results are never used as evidence of the player's ability. Penalty duty and usage changes are manual assumptions and appear in their own column.

## Connecting to the club database later

All logic sits in `engine/`. `engine/api.py` is the only entry point the app uses:

```python
from engine.api import Engine, PlayerInput, Scenario
eng = Engine.load()
p = PlayerInput(name="...", position_group="ST", league="USL League One", season="2025",
                minutes=2500, age=24, per90={"goals": 0.72, "assists": 0.14})
eng.project(p, "USL Championship", Scenario(minutes=2200))
```

`engine.ingest.import_file(path, ImportOptions(...))` imports without the UI, so a scheduled job can feed it from another system.

## Tests

`python3 -m pytest -q tests` runs the pipeline on **synthetic** fixtures in a temp folder (fake leagues with planted effects) to check the code recovers them. `python3 tests/smoke_ui.py` clicks through every page headlessly. Neither touches your database, and neither says anything about real world accuracy; only the Backtest page on your data does that.

`python3 tests/acceptance_upgrade.py <zip>` runs six upgrade scenarios on simulated Macs in a temp folder: first start next to an old installation, a later upgrade after recruitment work, same version, an older ZIP, a failed check, and a clean start. Your real data is only read.
