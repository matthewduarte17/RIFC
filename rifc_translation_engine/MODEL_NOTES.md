> Superseded in parts by RELEASE_NOTES.md (6 Nov 2026): identity fix, 2026 MLS Next Pro excluded, new metrics, team context test. Figures below are from 5 Nov.

# Model notes (updated 5 November 2026)

## Data

Wyscout advanced search exports, 21 files, ages imported with the export date as reference.

| League | 2023 | 2024 | 2025 | 2026 |
|:--|--:|--:|--:|--:|
| USL Championship | 600 | 639 | 654 | 654 |
| USL League One | 291 | 303 | 355 | 466 |
| MLS Next Pro | | 1,041 | 1,057 | 1,053 |

* Overlapping age band files: 93 identical rows skipped (2025 MLS Next Pro, age 22 in two files), plus 12 exact repeats inside files.
* 32 duplicate Wyscout profiles collapsed (same season stats under two profiles).
* **MLS Next Pro 2026, ages 20 to 25, has exactly 500 rows and looks cut off by the export limit** (age 25 count drops sharply). Re-export it as 20 to 22 and 23 to 25 and reimport.
* One identity flag to check: J. Pérez, 2024, over 1,500 minutes in both MLS Next Pro and the Championship (likely two players).

## Pairs

317 cross league pairs and 1,698 same league season to season pairs.

| From \ To | USL Championship | USL League One | MLS Next Pro |
|:--|--:|--:|--:|
| MLS Next Pro | 102 | 47 | |
| USL League One | 56 | | 15 |
| USL Championship | | 71 | 26 |

Cross league pairs by position: FB 74, CB 64, ST 60, W 50, CM 40, AM 15, DM 14.

## Backtest, all positions (cross league moves into 2025 and 2026, trained only on earlier moves)

Below zero in "Model vs best simple baseline" means the model was more accurate than both the league pair conversion ratio and the target league positional average.

| Metric                                  |   Test moves |   MAE model |   MAE carry over |   MAE pair ratio |   MAE league avg | Model vs best simple baseline   | 80% coverage   |
|:----------------------------------------|-------------:|------------:|-----------------:|-----------------:|-----------------:|:--------------------------------|:---------------|
| Goals                                   |          256 |       0.097 |            0.124 |            0.117 |            0.093 | 4%                              | 81%            |
| Goals, using xG                         |          256 |       0.094 |            0.124 |            0.117 |            0.093 | 1%                              | 82%            |
| Non penalty goals                       |          256 |       0.092 |            0.114 |            0.111 |            0.088 | 4%                              | 80%            |
| Non penalty goals, using xG             |          256 |       0.089 |            0.114 |            0.111 |            0.088 | 1%                              | 81%            |
| Expected goals (xG)                     |          256 |       0.066 |            0.08  |            0.071 |            0.068 | -2%                             | 80%            |
| Assists                                 |          256 |       0.069 |            0.088 |            0.085 |            0.069 | -1%                             | 74%            |
| Assists, using xA                       |          256 |       0.068 |            0.088 |            0.085 |            0.069 | -1%                             | 75%            |
| Expected assists (xA)                   |          256 |       0.047 |            0.058 |            0.055 |            0.047 | 1%                              | 75%            |
| Goal contributions (G+A)                |          256 |       0.125 |            0.168 |            0.147 |            0.125 | 0%                              | 79%            |
| Goal contributions (G+A), using xG + xA |          256 |       0.122 |            0.168 |            0.147 |            0.125 | -2%                             | 82%            |
| Shots                                   |          256 |       0.375 |            0.454 |            0.405 |            0.424 | -7%                             | 84%            |
| Key passes                              |          256 |       0.173 |            0.197 |            0.195 |            0.171 | 1%                              | 77%            |
| Progressive passes                      |          256 |       1.223 |            1.438 |            1.451 |            1.521 | -16%                            | 84%            |
| Progressive carries                     |          256 |       0.519 |            0.627 |            0.565 |            0.647 | -8%                             | 78%            |
| Successful dribbles                     |          256 |       0.383 |            0.455 |            0.408 |            0.571 | -6%                             | 80%            |
| Pass completion %                       |          256 |       3.8   |            4.401 |            4.588 |            4.132 | -8%                             | 79%            |
| Final third entries                     |          256 |       1.09  |            1.288 |            1.258 |            1.301 | -13%                            | 85%            |
| Touches in attacking box                |          256 |       0.591 |            0.696 |            0.681 |            0.639 | -8%                             | 82%            |
| Interceptions                           |          256 |       0.654 |            0.727 |            0.701 |            0.746 | -7%                             | 84%            |
| Aerial duels won                        |          256 |       0.477 |            0.503 |            0.495 |            0.658 | -4%                             | 79%            |

## Strikers only (46 test moves)

| Metric | MAE model | MAE carry over | MAE league avg | 80% coverage | Status |
|:--|--:|--:|--:|--:|:--|
| xG | 0.139 | 0.159 | 0.142 | 85% | Validated |
| Goals, using xG | 0.160 | 0.193 | 0.150 | 76% | Not validated (league avg 7% better) |
| Goal contributions | 0.182 | 0.240 | 0.173 | 89% | Not validated (league avg 5% better) |
| Shots | 0.540 | 0.653 | 0.543 | 93% | Not validated (intervals too wide) |
| Touches in box | 1.069 | 1.222 | 0.899 | 80% | Not validated (league avg better) |

## What it says

* With three leagues the model beats carrying numbers over on every metric, with a dependable edge (bootstrap near 1.0 for all).
* Possession and defensive volume translate well: progressive passes (16% better than the best simple method), final third entries (13%), progressive carries, dribbles, pass completion, interceptions, shots.
* Goals: across all positions the xG version is now within about 1% of the positional average and validated. For strikers alone it still is not: a striker's goal record in League One or MLS Next Pro says little about his Championship goals. **xG is the striker number that translates.**
* Real movers into the Championship (forwards and wingers): from MLS Next Pro, 29 players, goals per 90 0.36 to 0.23, xG 0.34 to 0.28; from League One, 20 players, goals 0.40 to 0.27, xG 0.34 to 0.28.
* Learned league effects on xG (model scale): Championship lowest output once there, MLS Next Pro highest, League One between. Standard errors are about 0.09, so the ordering is not yet precise.

## Next data

1. Re-export MLS Next Pro 2026 ages 20 to 25 in two pieces.
2. MLS Next Pro 2023 (adds moves into 2024).
3. Team stats per league season (switches on the RIFC team and penalty layers).
