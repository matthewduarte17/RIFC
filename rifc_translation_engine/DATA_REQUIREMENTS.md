# Data requirements

The engine learns only from players who appear in your imported data in one season and again the next season. Everything below follows from that.

## 1. What to export

### A. Player season stats (required, the core of the model)

**Full league exports, every player, one file per league per season.** You do not need a separate transfer list: when the same player shows up in League One 2024 and the Championship 2025, the engine finds the move itself.

Required fields

| Field | Notes |
|---|---|
| Player name | Full names link best. Wyscout short names ("J. Smith") are linked when the birth year matches exactly one full name |
| Team | Club for that row |
| Position | Wyscout codes (CF, LWF, AMF, LCMF, DMF, RB, LCB...), FBref (FW, MF, DF) or plain words |
| Minutes played | |
| League, Season | A column, or set once per file on the import page |
| Age or birth date | Needed to link players across files. If the age column is the **current** age (Wyscout does this for past seasons), set "As of the export date" on import |

Metrics (map whatever you have; anything missing is simply not projected)

| Attacking | Possession | Defensive |
|---|---|---|
| Goals, non penalty goals, xG, npxG | Progressive passes | Tackles |
| Assists, xA | Progressive carries | Interceptions |
| Shots, key passes, shot creating actions | Successful dribbles | Aerial duels won |
| Penalty goals, penalties taken | Pass completion % | Ground duels won |
| | Passes to final third | Ball recoveries |
| | Touches in box | |

Totals or per 90 both work; the engine converts. Wyscout "Aerial duels per 90" plus "Aerial duels won, %" is converted to aerial duels won per 90 automatically. No metric is derived where the arithmetic is not exact (for example npxG is never estimated from xG).

### B. Team season stats (needed for the RIFC layer)

One row per team per season, every team in each league you import: team, matches, goals for, xG for (if available), penalties awarded, possession %. Without this the RIFC projection equals the neutral projection plus your manual assumptions.

### C. Transfer list (optional)

Player, from league and season, to league and season. Only used to confirm links the name and birth year matching cannot make on its own.

## 2. Leagues and seasons to prioritise

The model translates through observed moves, so collect leagues that feed the Championship, and the Championship itself, for the same run of seasons:

1. USL Championship (destination, every season you can get)
2. USL League One
3. MLS Next Pro
4. MLS (players dropping down)
5. Your Global Scouting Program target leagues (Scandinavia tier 2 and 3, CONCACAF leagues, etc.) once the domestic core works

Seasons: as far back as the provider's coverage allows, ideally 2019 onward. More seasons means more moves and a real backtest.

**Keep a provider consistent per metric.** xG, progressive actions and duels are defined differently by Wyscout, StatsBomb and Opta. The engine only pairs provider specific metrics within one provider. Goals, assists and shots may mix providers.

## 3. Minimum history

These are the defaults; the engine refuses to project below them and tells you why.

| Rule | Default | Why |
|---|---|---|
| Minutes on both sides of a pair | 450 | Below this, per 90 numbers are mostly noise |
| Cross league pairs per metric | 30 | Fewer cannot separate league effects from noise |
| Cross league pairs per position group | 10 | Position specific effects need some direct evidence |
| Cross league pairs touching the source league and the target league | 8 each | A league with fewer moves has an unidentified effect |
| A chain of observed moves linking source to target league | required | Otherwise the comparison is a guess |
| Backtest pairs to call a projection "validated" | 20 | Below this, accuracy figures are unreliable |
| 80% interval coverage in backtest | 70% to 90% | Intervals must be honest |

What that means in practice (rough guide; the Translation Model page shows the real counts):

* **Minimum to fit a League One to Championship model:** about 3 seasons of both leagues in full. Movers between them each year should give you dozens of pairs over 3 seasons, which covers the overall gates but may leave some positions short.
* **Minimum to validate it:** 4 or more seasons, so at least one season can be held out and still have 30+ earlier moves to train on.
* **Comfortable:** 5 to 7 seasons across League One, Championship, MLS Next Pro and MLS. Enough for position level checks and a backtest per league pair.

International leagues will need several seasons of both the league and the Championship before any player from them can be projected; until then the engine says "Insufficient data" rather than inventing a factor.

## 4. Wyscout export checklist

Your 2026 USL Championship and League One exports are the right format: Advanced search, all players, full column set. Repeat the same export for each earlier season.

* Seasons: 2023, 2024 and 2025 for USL Championship and USL League One first, then MLS Next Pro for the same years.
* If Wyscout caps rows, split by age band as you did (28 and under, 29 and over). Import both files under the same league and season; the importer recognises them as complementary.
* Keep the same column set every time, so one mapping fits all files.
* Age: Wyscout shows today's age for past seasons. The import page defaults to "As of the export date" for Wyscout; leave it.
* "Team within selected timeframe" is used as the season club. "Team" is stored as the current club, and a blank current club is flagged as a possible free agent.
* Team stats: Teams tab, one export per league per season (matches, goals, xG, penalties, possession). This switches on the RIFC layer.
* Wyscout does not export total tackles, ground duels won, ball recoveries or shot creating actions in this view, so those metrics are not projected from Wyscout data. Aerial duels won and successful dribbles are built from the volume and success % columns.

## 5. File naming tip

Name exports like `USL League One 2024.xlsx` or `Eliteserien 2024-25.csv`; the import page reads league and season from the file name when it can.

## 6. StatsBomb team data (RIFC context)

* Export the six team views per season: results and xG, shooting, set pieces, passing, pressing, OBV.
* Upload all six together on Import Team Data. Type the season (the files don't contain it) and choose the phase: regular season, playoffs or combined. If clubs have different match counts, the export includes playoffs or has missing matches; pick combined, or export the regular season on its own.
* Never import the same season and phase twice. To replace one, exclude the old batch first.
* The team data is used for Championship benchmarks and the RIFC profile. It reaches player projections only where the Team Context Test shows it improves accuracy on past moves.
* There's no League One or MLS NEXT Pro team data. Those players get the neutral Championship projection.

## 7. Replacing a capped export (MLS NEXT Pro 2026)

1. In Wyscout, export MLS NEXT Pro 2026 ages 20 to 22 and ages 23 to 25 separately (same columns, same advanced search). Each file must have fewer than 500 rows; if one hits 500, split it again.
2. Data Library, MLS Next Pro 2026, Details and controls, Replace files: select the old ages 20 to 25 file, upload both new files, read the preview, tick the box and press Import and replace.
3. Read the season's checks. When they pass, change the use level to Full use, then back up and refit on the same page.
