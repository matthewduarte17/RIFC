# Validation audit (5 November 2026)

Model code unchanged (engine/model.py and engine/backtest.py identical to before the audit). Rerun any time on the Validation Audit page of the app.

## Data state
* 2026 MLS Next Pro (all three files) excluded, not deleted: the ages 20 to 25 file hit the 500 row export cap, so the season is incomplete. Reactivate all three on the Data Library page after importing the 20 to 22 and 23 to 25 re-exports.
* 301 cross league moves, 1,466 same league season pairs. Backtest: 240 moves per metric, outcome seasons 2025 and 2026.

## 1. Leakage
* 161 of 161 checks pass, over every metric and test season: training outcomes end before test seasons start; no training source season in or after the test season; no test move in training; no test outcome season used in training.
* Future scramble test: every value from 2025 on scrambled (4,483 values); 2025 predictions changed by exactly 0.
* Player disjoint rerun: removing every earlier move of each test player from training changed error by between minus 2% and plus 1%. The model has no player specific terms.
* 50% of test moves involve players with earlier history in training. Allowed: those seasons were known at prediction time.
* Known limits: identity linking runs once over all seasons (moves no numbers); choosing the xG or xA version of a metric used backtest error (slightly optimistic for those figures).

## 2. By transition (backtest moves per metric)
| Transition | Moves | Trust | Result |
|:--|--:|:--|:--|
| MLSNP to USLC | 102 | Adequate | Reliable on all 16 metrics |
| USL1 to USLC | 34 | Limited | Promising on 13; not reliable on xG (ranges too wide), assists (ranges too narrow), key passes (league average better) |
| USLC to USL1 | 47 | Limited | Weakest: 10 of 16 not reliable (goals, non penalty goals, xG, xA, G+A, key passes, carries, dribbles, final third entries, aerials) |
| MLSNP to USL1 | 47 | Limited | Promising on 15; progressive passes ranges too wide |
| USLC to MLSNP | 3 | Too few | No verdict |
| USL1 to MLSNP | 7 | Too few | No verdict |

USL1 to USLC has 56 moves in the data but 34 testable: moves into 2024 have nothing earlier to train on.

## 3. By position (all transitions pooled)
* CB (50 moves), FB (59): Reliable on most metrics. CB goals, non penalty goals, G+A, shots, progressive passes and final third entries have ranges too wide (conservative). FB attacking output and pass completion not reliable.
* ST (43): xG, goals and non penalty goals promising; G+A, assists, xA, key passes, touches in box: target league positional average better; shots and pass completion ranges too wide; interceptions worse than carry over.
* W (33), CM (28): mixed, mostly promising.
* AM (15), DM (12): too few for any verdict.
* Transition by position cells are almost all under 20 moves. Largest: MLSNP to USLC CB 28, FB 21.

## 5. Identity
* Likely two players merged (check): J. Pérez (Tampa Bay Rowdies 1998 born vs Chattanooga and Tormenta 1997 born; 3,544 minutes in 2024 across two clubs, heights 10 cm apart) and O. Wright (heights 15 cm apart). 2 false moves of 301; 1 in the backtest.
* 32 duplicate Wyscout profiles already collapsed; 145 identities with a Wyscout age off by one between files (same player, fine); 112 same name players correctly kept apart; 8 spelling variants on one team (2 duplicates handled, 6 different players).

## 7. Confidence on every projection
High: validated and 50+ backtest moves for both the transition and the position. Medium: validated and 20+. Low: not validated, under 20 moves, or the model failed the checks in that transition or position. None: not projected.
Real candidates into USLC: from MLS Next Pro 2025, 26% of player metric projections High, 26% Medium, 47% Low (CB and FB carry the High ratings); from USL League One 2026, no High (USL1 to USLC has only 34 test moves), 44% Medium, 56% Low.
