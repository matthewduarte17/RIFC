# Added 20 Nov 2026 (second change): wider coverage, bug fixes, validation

Club directory, optional changedetection.io page watching (own environment, installed only on your say-so), unconfirmed roster-page
changes, tracker cross-checks, coverage by club, correction/article-health/morning-brief fixes, loan-in and other reader fixes, and
`VALIDATION_MONITOR.md`. The monitor's tables moved to schema version 2 (backed up first, nothing dropped). `engine/upgrade.py` also carries
the new tables, the page-watch datastore and `club_sources.json`. No new packages are needed in the app. See README_MONITOR.md.

# Added 20 Nov 2026: recruitment monitor

New pages Recruitment Inbox, Daily Brief and Monitor Status; new package `engine/monitor`; tests in `tests/test_monitor.py`.
Changed existing files: `app.py` (three menu entries), `start.command` (background catch-up scan, one-time schedule offer),
`engine/upgrade.py` (carries the monitor's tables and settings through upgrades and checks them). The translation model, its
benchmarks, `data/` and every other file are unchanged; the data version is unchanged, so no data upgrade is triggered. See README_MONITOR.md.

# Release notes: safe upgrades (version 2026.11.11)

Model, pairs and backtest unchanged (engine/model.py, backtest.py, transfers.py identical by hash).

* **Your data now lives outside the app folder**, in `RIFC Translation Engine` in your home folder (`data/`, `backups/`, `.venv/`). A new ZIP never replaces it. The `data/` folder inside the app folder is only the release's starting data and is never used live.
* **The launcher upgrades your data safely.** On each start it compares versions. When the app is newer it builds the upgraded data in a staging folder: the release's data with everything of yours on top (shortlist with roles and notes, club verifications, identity decisions and rules, player IDs, use levels and reasons, your own imports reimported from your `raw/` copies, StatsBomb batches and their on or off state, files you removed). It then checks each item against your current data. Any failed check stops the upgrade and changes nothing. Only after every check passes does it move your current data into `backups/pre_upgrade_<date>` and switch over. Same version: nothing happens. An older app folder refuses to touch newer data.
* **First update from an older version.** The launcher finds the old app folder (next to the new one, or in Desktop, Downloads or Documents), shows what it holds and asks which to keep. The old folder is only read.
* **Release decisions** are listed in `data/RELEASE.json`. This release: MLS NEXT Pro 2026 moves to Profiles and discovery only (from Excluded, as earlier releases shipped it, or Full use). A level you set later is kept.
* **Removed files stay removed**: removing an import now records it, so an upgrade never brings it back.
* **Python packages** are reinstalled only when requirements.txt changes.
* **Database format.** The release database is written in DuckDB storage format 1.0, so it opens on any DuckDB from 1.0 onward. DuckDB 2.0 needs Python 3.12 or newer; with an older Python the app would otherwise have had to rebuild the database from the original files.
* **Data Library** shows the data folder, the data version, the last upgrade and its report.
* **Tested** (tests/acceptance_upgrade.py, 32 of 32 checks, with DuckDB 2.0 and again with DuckDB 1.4): first start next to an installation from 7 November (old format shortlist keys, MLS NEXT Pro 2026 excluded); an upgrade after recruitment work (shortlist note with accents, club verification, identity decision and rule, a dataset excluded by hand, a removed file, your own import, a StatsBomb batch switched off); same version; an older ZIP; a failed check (your own file's original missing); a clean start. The launcher itself was run end to end and the app opened on the upgraded data, every page without errors.

# Release notes: MLS NEXT Pro 2026 in profiles and discovery (11 November 2026)

Model, pairs (1,766) and backtest unchanged (model files identical by hash; the model is not outdated). Backup before the change: backups/pre_mlsnp2026_display_20261111_094252.

* **MLS NEXT Pro 2026 set to "Profiles and discovery only".** In search, profiles, Recruitment Discovery, scatter plots, Potential Targets and the shortlist, with Championship projections from the existing model. Not in training pairs, the backtest or any benchmark.
* **Labels.** Search results, career table, projection banner, projection cards, discovery hover and detail cards, target cards, the shortlist and the one page report all say "partial dataset". The projection banner states that the confidence dots rate the translation model on past moves and say nothing about how complete the source data is.
* **No self comparison.** A player whose record comes from a partial dataset is never ranked against his own league season (that percentile is left blank). Championship percentiles and medians come from complete Championship data only.
* **Counts (2026 MLS NEXT Pro):** 1,053 rows; 1 near duplicate profile hidden (B. Swinehart and B.Swinehart, Carolina Core, 788 and 787 minutes); 1,052 unique players. 109 goalkeepers (not in outfield discovery), 943 outfield players. 81 outfield players have no per 90 statistics (all under 45 minutes; Wyscout does not report per 90 for tiny samples), so 862 can be projected and plotted. 390 of them have 900+ minutes, the default discovery cutoff. For 17 players the 2026 MLS NEXT Pro record is not their latest record, because they also played more minutes in USL in 2026.
* **Duplicates, explained.** The 2026 files are age bands 14 to 19, 20 to 25 and 26+, which do not overlap, so the importer had nothing to skip; that is why the check showed zero. Earlier seasons did overlap: 2025 had 96 exact copies skipped on import (mainly age 22, in both the 19 to 22 and 22+ files), plus 4 players listed in both files with ages one year apart. Those 4 were not caught as exact copies, but player linking joins them and the season counts once. The duplicate check now reports all of this.
* **Replacement workflow** (Data Library, under each season: Replace files). Pick the files to replace, upload the corrected exports and review the preview: files and age bands, rows of the old file found again, new rows, overlap, merged total and problems (a file at 500 rows again, age gaps). On confirm, the new files are deduplicated only against the files that stay, never against the ones they replace. They take the season's current use level, and the old file is kept, marked "replaced" and unused. Full use is offered only after the merged season passes validation. Tested on a copy of your data by splitting the 20 to 25 file: 1,053 rows, no duplicates, model untouched.
* **Safeguards against silent contamination.** Any new file added to a season that is not in Full use, even through the normal import page, inherits that season's level. The model counts as outdated only when the training data itself changes.

# Release notes: search, league filter, projection explanation and dataset management (9 November 2026)

Model, pairs and backtest unchanged (engine/model.py, backtest.py, transfers.py identical by hash). Real data state unchanged: MLS NEXT Pro 2026 stays excluded.

## Search and league filter (Recruitment Discovery)

* **Why the old search failed.** "Search and label players" only looked inside the points already plotted, and matched text without removing accents. O. Hernández (Chattanooga Red Wolves, USL League One 2026, winger, 2,868 min) is in the database. Typed as "O. Hernandez" he never matched "Hernández", so he was not labelled even while viewing wingers. While viewing attacking midfielders he could not appear anyway: his latest season is as a winger. C. Parano and R. Cicerone are 2026 Championship wingers, outside the default League One and MLS NEXT Pro view.
* **Find any player** (top of the page): searches every player in the database regardless of filters, ignoring accents and initials; one result per player with last club, position, league and season; Open profile; Show on chart. When he is not plotted, the page lists every reason: position, league, minimum minutes, pool, season and club filters, missing axis metric, projection unavailable, reliability filter, axis thresholds, rows in an excluded import, unresolved identity. Show on chart changes only the filters that hide him, lists what it changed, and offers Restore filters. A name that is not in the data says so.
* **Highlight on chart** (renamed): labels plotted matches and explains unplotted ones.
* **Source leagues** are buttons beside Position and View (no menu), plus All leagues and Reset leagues. RIFC stars stay visible and uncounted. Every Discovery control now keeps its value for the session, also after visiting other pages.

## Why this projection? (Player Profile)

* Default view: Now, Total adjustment and Projected cards; up to three plain reasons; a compact 80% range with a reliability label. The waterfall, the model scale table, historical errors and the method are under "View technical methodology".
* **Audit of the Championship to Championship league step.** Not a modelling error; a presentation error. The model has league terms for the source and destination league, learned jointly with the intercept from all 1,766 season pairs, including 854 same league Championship pairs. Under the ridge penalty the split between the intercept and the league terms is arbitrary, and the old breakdown reported the league terms as a "league step learned from 0 players who moved". The new breakdown is exact on the model scale and order free: "Regression toward typical output" now includes his own league's level; "League change" is the Championship projection minus the same projection staying in his league, so it is zero for a Championship player and is never attributed to one transition. The step contributions add up exactly on the log (or logit) scale; per 90 is shown only for the start and end. Same league projections stay Low confidence: the backtest only tests moves between leagues.

## Dataset management (Data Library and Data and Model Status)

* One card per league season with files, player rows, use level, validation status, reason for exclusion, last import date and expandable checks (export cap, age band coverage, duplicates, columns, player and club counts, season completeness, import errors).
* **Three use levels**: Full use (everything, including training and backtest; changing it makes the model outdated until refit, with a backup first); Profiles and discovery only (players, profiles, search, discovery and projections from the current model; never training pairs, backtest or benchmarks); Excluded. Full use of a dataset that fails validation needs a written override.
* **MLS NEXT Pro 2026**: the three files are the original exports, not corrected replacements. The ages 20 to 25 file has exactly 500 rows, Wyscout's export limit, so an unknown number of players is missing. Wyscout sorts these exports by market value, so the rows cut are players with a market value of 0, not a random sample. Everything else checks out: age bands 14 to 19, 20 to 25 and 26+ without gaps, no duplicates, the same 30 fields as 2024 and 2025, 1,053 players (2024: 1,032; 2025: 1,042), 31 clubs, full season. It stays excluded from the model. To fix it, re-export the 20 to 25 age band in smaller pieces (for example 20 to 22 and 23 to 25, each under 500 rows), import them, and switch the season to Full use. Until then it can be switched to Profiles and discovery only; tested on a copy of the data, that shows 1,053 season records and leaves the model's 1,766 pairs untouched.

# Release notes: player identity, career profiles and discovery (7 November 2026)

The translation model, its pairs and its backtest are unchanged (engine/model.py, backtest.py and transfers.py identical by hash; leakage audit rerun: 217 checks pass). Backups: backups/pre_identity_20261107_180406.

## Data corrections

| | Before | After |
|:--|--:|--:|
| Search and discovery entries (outfield) | 5,451 season entries | 2,951 players |
| Duplicate entries folded into one profile | | 2,500 |
| Player IDs (stable, kept in identity_registry) | | 3,259 |
| Verified multi season identities (all evidence agrees) | | 1,628 |
| Single season players | | 1,626 |
| Identities left for manual review | | 5 (4 uncertain, 1 probable) |
| Model season pairs, cross league / same league | 300 / 1,466 | 300 / 1,466 (unchanged) |

* **False merges found: 3.** The model had joined T. Williams (Orlando City B and Columbus Crew II, MLS NEXT Pro 2024), J. Garcia (Monterey Bay FC and Pittsburgh Riverhounds, Championship 2025 and 2026) and C. Diaz (three MLS NEXT Pro 2024 clubs). A Wyscout export lists a player once per league season, so two clubs in the same league season are two people. Profiles now show 7 separate players. These links touch 4 of the model's 1,766 season pairs (1 cross league). The model keeps them until you press "Apply this split to the model" on Identity Review and refit.
* **J. Pérez** stays two players (existing rule); **O. Wright** stays one player.
* 113 players played in two competitions in one season (mostly MLS NEXT Pro plus a USL loan). They are separate records; per 90 rates are never combined across competitions; the season total shows minutes, goals and assists only.
* No player has two clubs in one league season after the split.
* 188 pairs of profiles share a name and are kept apart for a stated reason (birth year, nationality, height, same league season). 2 pairs have similar ages and no deciding field; they are listed for review and stay separate unless you link them.
* Wyscout's "Team" field is not consistent across files exported the same day (38 players have different values), so it is never used as the current club. Current club is "unverified" until you record a verification with date and source.

## New

* **One player, one profile.** Search returns one result per player (exact and close names first, then alternate names, clubs, leagues; no unrelated names). The profile has the career table, trends, club history, every season, the current club panel and manual verification, and the projection from a season record you choose (latest eligible by default).
* **Recruitment Discovery**: one point per player from his own latest eligible season; league filter next to Position and View (default USL League One and MLS NEXT Pro, All leagues and Reset buttons); live count per league; current pool versus historical and unverified pool; recency, verification, latest season filters; Historical seasons mode with earlier seasons hollow; RIFC players as reference stars, never counted; shorter hover cards; a detail panel for selected players with ranges, verification, profile and shortlist; labels for selected players.
* **Potential Targets**: editable criteria (metric, reference, value, mandatory or preference); references: best RIFC player, RIFC median, RIFC mean, Championship median, Championship 75th percentile, any Championship percentile, custom value; the actual benchmark and its source shown; three groups (Meets requirements, Near matches within a set margin, Exploratory targets with weaker evidence) with the exact reason for each player; the biggest causes of exclusion when nobody meets every requirement.
* **Identity Review** (Admin): audit, review list, confirm, apply a split to the model, link same name profiles, undo.
* **Shortlist** is one entry per player (old entries migrated).

## Multi season test (report only, not switched on)

The model reads one source season: the player's league season just before the move. A two season input (that season plus the previous season in the same league, weighted by minutes) was tested with the same model and time based backtest. 34% of test moves have a previous season. It lowered error on 21 of 23 metrics. It passes the adoption rule set before the run (2%+ lower error, P of at least 0.9, honest ranges) for xG (3.8% lower), successful dribbles (4.8%), crosses (4.1%) and progressive carries (2.6%). Goals, assists and defensive duels did not improve meaningfully. Switching it on changes those four projections and needs a refit; it is waiting for your decision. Results: Model Reliability page and data/models/multiseason_test.json.

# Release notes: visual redesign and recruitment discovery (6 November 2026, later)

Model, data, imports, validation and backtest are unchanged (engine/model.py, backtest.py and transfers.py are byte for byte identical to before; checked by hash).

* **Dark design system**: navy theme, one card style, one chart template, a confidence meter (dots, not traffic lights) used everywhere. Colours: gold for the candidate, teal for RIFC players, grey for Championship benchmarks.
* **Player Profile** (renamed from Player Projection):
  * header lists name, age, position, full club and league names, season, minutes, provider, contract expiry, current club and nationality, and wraps instead of truncating;
  * an automatic sentence on the role's first projected metrics, with the season total at your minutes and the backtest error;
  * projection cards per role (striker: goals, non penalty goals, xG, assists, G+A) with season total, per 90, current per 90, change, Championship percentile, 80% range, confidence and validation;
  * "Why this projection?": a waterfall and table of the model's own steps (current, regression to the mean, xG or xA, league transition with the real movers' before and after averages, age, projection) plus the backtest error;
  * percentile strips (P10 to P90, P25 to P75, median, projected, current, chosen RIFC players) and a separate 50% and 80% range chart;
  * minutes scenario with the range recomputed at every minutes value;
  * full table moved into an expander.
* **Recruitment Discovery** (new, opens first): scatter of every eligible player with position presets, projected or current numbers, filters, search, RIFC markers, median lines, click and box selection, open profile and bulk shortlist. Batch projections are the same calculation as the single player page (tested: 856 metric checks, zero differences).
* **Potential Targets** (new): recruitment priorities with Validated and Exploratory modes, reasons, video priorities, RIFC comparison, shortlist with notes; warns when a metric has no validated projection for the position.
* **Compare with RIFC**: takeaways first, then small multiple bars with ranges and the Championship band.
* **Model Reliability** (new, recruitment section): transitions, verdicts, accuracy against simple methods, methodology.
* **One page report**: restyled, prints in colour on one letter page (checked for a striker and a center back).

Findings worth knowing:
* No striker with 900+ minutes in the latest seasons projects above the RIFC striker average for xG (0.50 per 90, observed: JJ Williams 0.47 in 1,656 min, S. Korzeniowski 0.53 in 971 min). The highest projections are about 0.47 to 0.49. Projections are pulled toward the average and observed numbers are not, so that bar is high; the empty list is a real answer.
* For wingers, goals and xG have no High or Medium confidence projection, and attacking midfielders have none on any metric. Those priorities only run in Exploratory mode, and the page says so.

# Release notes: recruitment version (6 November 2026)

## Summary of changes

* **Recruitment section** (Player Projection, Compare with RIFC, Recruitment Shortlist, RIFC Team Profile, Championship Team Benchmarks), with admin pages in their own section of the sidebar.
* **Player Projection redesigned** around one question: what can we expect at Championship level, against what we have, and on what evidence. It shows:
  * a recruitment summary with expected contribution, strengths, concerns, benchmarks, RIFC comparisons, confidence and video priorities;
  * a projection table with the current number, translated number, RIFC adjusted number, percentile, historical error, 80% range, status and confidence;
  * minutes scenarios and a plain language "why the numbers change".
* **Role selection** (center forward, winger, attacking midfielder, central or defensive midfielder, fullback, center back) sets the metrics, benchmarks and comparison group.
* **Seven new player metrics**, all from columns already in your Wyscout exports:
  * passes to penalty area, crosses, accurate crosses;
  * defensive duels won, defensive duel success %;
  * successful defensive actions, aerial duel success %.
* **Compare with RIFC**: candidate projections against current RIFC players' observed numbers, striker questions, and manual choice of comparison players.
* **Recruitment Shortlist**: saved candidates across leagues at standard minutes, with filters, sorting, intervals, RIFC reference lines and reports.
* **One page report** (HTML that prints to one letter page) from Player Projection and the Shortlist.
* **StatsBomb team data**: a multi file importer with a metric registry, a required season and phase, match count checks, League Average verification and duplicate protection. Team benchmarks run per season.
* **Team context test**: an out of sample test of whether StatsBomb team context improves player projections. One metric adopted (provisional).
* **Identity fix**: J. Pérez split into the separate players he actually is. O. Wright confirmed as one player, with the right duplicate profile now kept.
* **Unchanged**: the existing translation model (method, settings and code), the launcher, the raw files, and the 2026 MLS NEXT Pro exclusion.

Backups:
* `backups/pre_recruitment_20261106_103347` holds the code, database, models and manifest before these changes.
* The database from before the rebuild is kept as `data/engine_unreadable_20261106_103647.duckdb`.

## Confirmed datasets

| Data | Seasons | Notes |
|:--|:--|:--|
| Wyscout USL Championship | 2023, 2024, 2025, 2026 | 2 age band files each |
| Wyscout USL League One | 2023, 2024, 2025, 2026 | |
| Wyscout MLS NEXT Pro | 2024, 2025 active; 2026 excluded | 2026 ages 20 to 25 file hit the 500 row cap |
| StatsBomb USL Championship team | 2024, 2025, 2026 | 18 files, 6 categories per season, combined regular season and playoffs |

The StatsBomb files have no season column. Each group's club list matches exactly one Wyscout season, and no other:
* Memphis 901 and North Carolina, no Lexington: 2024.
* Lexington, no Memphis: 2025.
* Brooklyn and Sporting JAX: 2026.

The season was set from that evidence, not from the file names. Phase is combined, because match counts differ between clubs:
* 2024: 32 to 38 matches. RIFC and Colorado Springs played 38.
* 2025: 29 to 34.
* 2026: 29 to 31, with playoffs in progress at export.

**Please confirm the three seasons.**

## Data quality findings

* **Mislabeled column:** "Penalty Goals Conceded" appears in two StatsBomb files. In the shooting file its league total equals penalties taken every season (121, 97, 103), so it counts penalties faced. It is stored as "Penalties Faced". The results file version sums to penalty goals, as its name says.
* **League Average rows:** in 2025 the OBV file's League Average row is empty (0 games). League Average rows also don't always equal the mean of the clubs (2025 Goals: row 39.0, clubs 40.75). Benchmarks use the clubs' own values; the row is kept for verification.
* **Rounding:** percentages are rounded to whole numbers and ratios to two decimals. Eleven rate metrics are recomputed from totals at full precision; the reported values are kept.
* **Successful Box Cross%:** the reported value doesn't equal successful box crosses ÷ box crosses (likely an average of match level percentages). The season ratio is used and the reported value kept.
* **Missing matches:** some clubs have fewer matches than a full regular season (for example Hartford 32 in 2024, Birmingham 29 in 2025), so some matches are missing from StatsBomb coverage. Per match values use each club's own match count.
* **Identity:**
  * J. Pérez was two players merged: Tampa Bay, born 1998, 165 cm; and Chattanooga and Tormenta, born 1997, 175 cm. Fixed by a general rule (profiles more than 7 cm apart in height are never linked) plus an explicit rule in `data/identity_overrides.json`. Only this one identity changed across 3,254. One false move removed (301 moves become 300).
  * O. Wright is one real player. A duplicate Wyscout profile with a different height was kept by mistake; the copy that matches his other seasons is now kept. His move is genuine.
* **No earlier team data to merge:** the earlier per match team export you mentioned isn't in the app, so nothing was merged with the new season total files.

## Model validation (time based, moves into 2025 and 2026, 2026 MLS NEXT Pro excluded)

After the identity fix, backtest errors moved by less than 1.5% on every existing metric. All 161 leakage checks pass, the future scramble test leaves predictions unchanged, and the player disjoint rerun changes error by under 2%.

### All transitions pooled

| Metric                       | Reads      |   Test moves |   MAE | vs carry over   | vs best simple   | 80% coverage   | Verdict                         |
|:-----------------------------|:-----------|-------------:|------:|:----------------|:-----------------|:---------------|:--------------------------------|
| Goals                        | xG         |          240 | 0.093 | -26%            | 1%               | 81%            | Reliable                        |
| Non penalty goals            | xG         |          240 | 0.088 | -24%            | 0%               | 80%            | Reliable                        |
| Expected goals (xG)          | own record |          240 | 0.063 | -18%            | -2%              | 80%            | Reliable                        |
| Assists                      | xA         |          240 | 0.068 | -22%            | 0%               | 74%            | Reliable                        |
| Expected assists (xA)        | own record |          240 | 0.047 | -20%            | 1%               | 75%            | Reliable                        |
| Goal contributions (G+A)     | xG + xA    |          240 | 0.12  | -28%            | -1%              | 82%            | Reliable                        |
| Shots                        | own record |          240 | 0.363 | -19%            | -8%              | 85%            | Reliable                        |
| Key passes                   | own record |          240 | 0.172 | -11%            | -1%              | 78%            | Reliable                        |
| Progressive passes           | own record |          240 | 1.213 | -16%            | -14%             | 80%            | Reliable                        |
| Progressive carries          | own record |          240 | 0.514 | -18%            | -8%              | 80%            | Reliable                        |
| Successful dribbles          | own record |          240 | 0.385 | -15%            | -5%              | 79%            | Reliable                        |
| Pass completion %            | own record |          240 | 3.801 | -14%            | -9%              | 80%            | Reliable                        |
| Final third entries          | own record |          240 | 1.111 | -15%            | -12%             | 82%            | Reliable                        |
| Touches in attacking box     | own record |          240 | 0.567 | -16%            | -9%              | 82%            | Reliable                        |
| Interceptions                | own record |          240 | 0.668 | -9%             | -6%              | 81%            | Reliable                        |
| Aerial duels won             | own record |          240 | 0.473 | -5%             | -4%              | 79%            | Reliable                        |
| Passes to penalty area       | own record |          240 | 0.618 | -11%            | -7%              | 84%            | Reliable                        |
| Crosses                      | own record |          240 | 0.529 | -14%            | -12%             | 87%            | Reliable                        |
| Accurate crosses             | own record |          240 | 0.234 | -15%            | -14%             | 81%            | Reliable                        |
| Defensive duels won          | own record |          240 | 0.672 | -25%            | -15%             | 71%            | Reliable                        |
| Defensive duel success %     | own record |          240 | 7.416 | -9%             | 4%               | 69%            | Not reliable: ranges too narrow |
| Successful defensive actions | own record |          240 | 1.076 | -21%            | -14%             | 80%            | Reliable                        |
| Aerial duel success %        | own record |          240 | 9.966 | -3%             | -5%              | 91%            | Not reliable: ranges too wide   |

### Moves available per transition

| transition    |   pairs in data | by outcome season            |   backtest moves (per metric) | trust            |
|:--------------|----------------:|:-----------------------------|------------------------------:|:-----------------|
| MLSNP to USLC |             102 | 2025: 53, 2026: 49           |                           102 | Adequate         |
| USL1 to USLC  |              56 | 2024: 22, 2025: 14, 2026: 20 |                            34 | Limited          |
| USLC to MLSNP |              14 | 2024: 11, 2025: 3            |                             3 | Too few to trust |
| USLC to USL1  |              71 | 2024: 24, 2025: 23, 2026: 24 |                            47 | Limited          |
| MLSNP to USL1 |              47 | 2025: 15, 2026: 32           |                            47 | Limited          |
| USL1 to MLSNP |              10 | 2024: 3, 2025: 7             |                             7 | Too few to trust |

### Verdict by transition

| metric                       | MLSNP to USLC       | USL1 to USLC                                 | USLC to USL1                                  | MLSNP to USL1                              |
|:-----------------------------|:--------------------|:---------------------------------------------|:----------------------------------------------|:-------------------------------------------|
| Accurate crosses             | Reliable            | Promising                                    | Promising                                     | Promising                                  |
| Aerial duel success %        | NR: ranges too wide | NR: worse than carry over                    | Promising                                     | NR: worse than carry over, ranges too wide |
| Aerial duels won             | Reliable            | Promising                                    | NR: worse than carry over                     | Promising                                  |
| Assists                      | Reliable            | NR: ranges too narrow                        | Promising                                     | Promising                                  |
| Crosses                      | NR: ranges too wide | Promising                                    | NR: worse than carry over                     | NR: ranges too wide                        |
| Defensive duel success %     | Reliable            | NR: worse than carry over, ranges too narrow | NR: ranges too narrow                         | NR: simple baseline better                 |
| Defensive duels won          | Reliable            | Promising                                    | NR: ranges too narrow                         | Promising                                  |
| Expected assists (xA)        | Reliable            | Promising                                    | NR: simple baseline better, ranges too narrow | Promising                                  |
| Expected goals (xG)          | Reliable            | Promising                                    | NR: simple baseline better                    | Promising                                  |
| Final third entries          | Reliable            | Promising                                    | NR: worse than carry over                     | Promising                                  |
| Goal contributions (G+A)     | Reliable            | Promising                                    | NR: simple baseline better                    | Promising                                  |
| Goals                        | Reliable            | Promising                                    | NR: simple baseline better                    | Promising                                  |
| Interceptions                | Reliable            | Promising                                    | Promising                                     | Promising                                  |
| Key passes                   | Reliable            | NR: simple baseline better                   | NR: simple baseline better, ranges too narrow | Promising                                  |
| Non penalty goals            | Reliable            | Promising                                    | NR: simple baseline better                    | Promising                                  |
| Pass completion %            | Reliable            | Promising                                    | Promising                                     | Promising                                  |
| Passes to penalty area       | Reliable            | NR: ranges too wide                          | NR: worse than carry over                     | Promising                                  |
| Progressive carries          | Reliable            | Promising                                    | NR: worse than carry over, ranges too narrow  | Promising                                  |
| Progressive passes           | Reliable            | Promising                                    | Promising                                     | NR: ranges too wide                        |
| Shots                        | Reliable            | Promising                                    | Promising                                     | Promising                                  |
| Successful defensive actions | Reliable            | Promising                                    | Promising                                     | Promising                                  |
| Successful dribbles          | Reliable            | Promising                                    | NR: worse than carry over, ranges too narrow  | Promising                                  |
| Touches in attacking box     | Reliable            | Promising                                    | Promising                                     | Promising                                  |

## Team context (StatsBomb) test

Rules were fixed before the test ran:
* **Team data:** the destination club's season before the move, so only data available at the time of signing.
* **Train and test:** trained on 2025 moves (using 2024 club data), tested on 2026 moves (using 2025 club data).
* **Adoption bar:** at least 2% lower error, at least 90% of bootstrap resamples favouring it, at least 30 test moves, and the same direction when refit on all moves.
* **Test moves:** 150 per metric (USLC club to club 92, MLSNP to USLC 41, USL1 to USLC 17).

| label                        |   test moves |   MAE neutral |   MAE team adjusted |   error change % |   P(adjusted better) | decision    |
|:-----------------------------|-------------:|--------------:|--------------------:|-----------------:|---------------------:|:------------|
| Goals                        |          150 |         0.082 |               0.082 |            0.191 |                0.311 | Not adopted |
| Non penalty goals            |          150 |         0.079 |               0.079 |            0.119 |                0.366 | Not adopted |
| Expected goals (xG)          |          150 |         0.062 |               0.062 |           -0.33  |                0.675 | Not adopted |
| Assists                      |          150 |         0.057 |               0.057 |            0.018 |                0.404 | Not adopted |
| Expected assists (xA)        |          150 |         0.038 |               0.038 |            0.052 |                0.202 | Not adopted |
| Goal contributions (G+A)     |          150 |         0.108 |               0.108 |           -0.056 |                0.75  | Not adopted |
| Shots                        |          150 |         0.297 |               0.297 |           -0.14  |                0.922 | Not adopted |
| Key passes                   |          150 |         0.182 |               0.182 |           -0.008 |                0.553 | Not adopted |
| Progressive passes           |          150 |         1.065 |               1.076 |            1.058 |                0.319 | Not adopted |
| Progressive carries          |          150 |         0.385 |               0.386 |            0.25  |                0.473 | Not adopted |
| Successful dribbles          |          150 |         0.3   |               0.302 |            0.718 |                0.275 | Not adopted |
| Pass completion %            |          150 |         3.569 |               3.398 |           -4.772 |                0.927 | Adopt       |
| Final third entries          |          150 |         0.916 |               0.93  |            1.47  |                0.187 | Not adopted |
| Touches in attacking box     |          150 |         0.54  |               0.543 |            0.471 |                0.35  | Not adopted |
| Interceptions                |          150 |         0.656 |               0.655 |           -0.126 |                0.803 | Not adopted |
| Aerial duels won             |          150 |         0.475 |               0.465 |           -1.922 |                0.756 | Not adopted |
| Passes to penalty area       |          150 |         0.589 |               0.589 |           -0.029 |                0.764 | Not adopted |
| Crosses                      |          150 |         0.484 |               0.484 |           -0.051 |                0.519 | Not adopted |
| Accurate crosses             |          150 |         0.194 |               0.195 |            0.892 |                0.157 | Not adopted |
| Defensive duels won          |          150 |         0.594 |               0.593 |           -0.221 |                0.663 | Not adopted |
| Defensive duel success %     |          150 |         8.725 |               8.735 |            0.116 |                0.432 | Not adopted |
| Successful defensive actions |          150 |         0.944 |               0.946 |            0.225 |                0.327 | Not adopted |
| Aerial duel success %        |          150 |         9.159 |               9.263 |            1.141 |                0.004 | Not adopted |

Result:
* **Adopted, provisional: pass completion %.** It improved accuracy in all three transition groups. With 23 metrics tested, one could pass by chance, so it needs the 2027 season to confirm. A club with 10% more passes than the league adds about 1 point of pass completion.
* **Not adopted: everything else.** Goals, xG, chance creation, carrying and defending projections stay neutral. RIFC team numbers are shown as context only, never as evidence of a player's ability.
* **USL League One:** has no StatsBomb data. League One players are evaluated on the neutral translation, and the missing source club context is stated.

## Working recruitment features (tested)

Tested end to end on the real data and headless on synthetic data:
* **J. Barea acceptance test:**
  * select him and review his 2025 MLS NEXT Pro production;
  * see his projected Championship output, why it changes and how reliable it is;
  * compare him with JJ Williams and S. Korzeniowski;
  * see strengths and concerns, and change the minutes;
  * compare him with S. Marou on the Shortlist;
  * download the one page report, checked to fit one letter page.
* **RIFC Team Profile:** 2024 to 2026 attack, chance creation, box entry, open play vs set piece, pressing, and season by season changes.
* **Championship Team Benchmarks:** all clubs, per match, ranked within season.
* **StatsBomb importer:** blocks a missing season, wrong seasons mixed, mismatched match counts and repeat imports.
* **Rebuild:** reimports both player and team data from the raw files.

## Outstanding limitations

* **Too few moves to judge:** USLC to MLSNP (3 test moves) and USL1 to MLSNP (7).
* **USL1 to USLC:** 34 test moves, so position level results are thin. Attacking and defensive midfielders have under 20 test moves.
* **Not in the Wyscout export, so not projected:** tackles, ball recoveries, ground duels and shot creating actions.
* **Team context:** one test season only. The pass completion adjustment is provisional.
* **Playing time:** no minutes model. Minutes are always your scenario.
* **2026 StatsBomb data:** a snapshot with playoffs in progress.

## Future imports

* **Player data (Wyscout):** Import Player Data, one file per league per season and age band. Leave the age reference on "export date". Then refit on Translation Model, rerun Backtest and rerun Team Context Test.
* **Team data (StatsBomb):** Import Team Data. Upload the six category files of one season together, type the season, choose the phase, check the checks, import. Then rerun Team Context Test.
* **MLS NEXT Pro 2026:** after importing the 20 to 22 and 23 to 25 re-exports, restore the three excluded 2026 files on Data Library and refit.
