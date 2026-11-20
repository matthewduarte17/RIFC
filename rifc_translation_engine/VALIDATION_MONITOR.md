# Validation of the monitor against hand-checked real roster moves

Run on 20 Nov 2026 with `python tools/validate_monitor.py`. Ground truth: `tests/fixtures/validation_sample.csv`.
Details per player (these contain database-derived links, so they stay out of the public repository): `validation/validation_details_*.csv`.

## What was tested
267 statements about named players, taken from real public announcements and **labelled by hand by reading them**, before the monitor was run on them:

| Set | What | Statements |
|---|---|---|
| dev | USL Championship transactions page, 34 recent headlines (Aug to Nov 2026) | 36 |
| hold-out 1 | USL League One transactions, 30 headlines + 3 roster-decision articles (Oakland, Pittsburgh, Monterey Bay, Nov to Dec 2025) | 29 + 61 |
| hold-out 2 | 17 more USL Championship headlines + 4 roster-decision articles (Lexington, Loudoun, Phoenix, El Paso) | 17 + 86 |
| negative controls | award, preview and head-coach headlines: no move should be reported | 5 |
| untouched | 38 older USL League One headlines, labelled by hand, scored once before any change | 38 |

Each statement says: the player, the club, and the move (signing, extension, loan in, loan recall, transfer out, option exercised or declined, under contract, out of contract, released, retirement). "Read" means the monitor extracted that player. "Right type" means it called the move the right thing. For players I judged to be in the database, "linked" means the matcher attached the announcement to that player without asking, "queued" means it put the question in the review queue instead.

## Results

### Before any fix (as shipped yesterday), 229 statements (everything except the untouched set)
- Read: **69%**. Right type: **52%**. Wrong type: **17%** (40). Missed: **31%** (71).
- Of 217 players in the database: linked **59%**, queued 7%, unmatched 2%, not read 31%.
- On the fresh hold-out 2 alone, before the fixes: read **57%**, right type **49%**, 9 wrong, 44 missed.
- Wrong automatic links: **0**. False alarms on award and preview headlines: **0 of 4**.

What went wrong, by kind:
- **Misclassified (40):** 24 loans in were read as ordinary signings ("acquires X on loan from Y"; there was no loan-in type); 3 extensions were read as signings ("signs X to a new contract"); 4 options exercised were read as "under contract"; others.
- **Missed (71):** "announces transfer of X" (4), "will also return …" and "previously under contract" lists (12+), mutual terminations, buyouts, "completed their loans", "was out of contract", bare verbs ("add", "ink", "bring aboard"), "Unveiled as first signing".
- **Not linked:** "Joel Garcia **Jr.**" (suffix), "Gabi" vs "Gabriel" (nickname), "Elmasnouy" vs "Elmasnaouy" (spelling).

### After the fixes, 267 statements
- Read **266 of 267**. Right type **266 of 267**. Wrong type **0**. Missed **1** (a second player named after the verb in a two-player sentence).
- Of 217 players in the database: linked **88%** (191), queued for review **10%** (22), left unmatched 3, not read 1.
- Wrong automatic links: **0**. Names not in the database linked to someone: **0 of 10**. False alarms on controls: **0 of 5**.

### The honest reading of those numbers
- **The near-perfect "after" scores are not an estimate of how the monitor will do in general.** I fixed the grammar using the failures on the development and hold-out 1 sets, and I had read the hold-out 2 articles before fixing. Their writer, the USL league site's house style, is also the same across the sets.
- **The only untouched measure** is the 38 older League One headlines, scored once before any change: read **33 of 38 (87%)**, every one of those with the right type, 0 false alarms. The five misses were two phrasings ("X **joins** Club [on loan from Y]" and "**bolster** the backline with defender X"). I then fixed those, so the 38 of 38 afterwards is not independent.
- A fair expectation for new announcements in the same league-site style is therefore **roughly 85 to 90% read, with the right type almost always**, and the misses are silent unless they sit in an article that yields no players (those are flagged as "read, but no players found"). For other writers' phrasing (club sites, MLS NEXT Pro, local press) it has **not been measured**.

## Label errors found and corrected (disclosed)
My first pass at "is this player in the database" used a narrow rule (same club in 2025 or 2026). The matcher then linked Dane Jacomen, whom I had labelled "not in the database", and I checked: he is (Loudoun 2023, Westchester 2025 on loan, Tulsa 2026), so **the link was right and my label was wrong**. I re-audited every "not in the database" label on the roster-list sets against all clubs and corrected 11 of those 15 (4 are genuinely absent; 2 more are marked unknown). The scores above use the corrected labels, and the wrong label has been changed in the file, not hidden. Players where I could not tell are labelled "unknown" and left out of the linking counts.

## Errors that remain (all)
- Kieran Sargeant (Lexington, loan return): the sentence names a second player after "returned to Real Salt Lake and …"; not read.
- Jonathan Robinson, Ben Martino, Harvey Neville: possibly in the database (similar names at other clubs and seasons), left unmatched because nothing corroborates the club. This is the intended caution: it errs toward not linking.

## What this does NOT show
- **Roster-page change detection on real club pages: not validated.** All 42 league-hosted roster pages draw their player lists with JavaScript, so a plain fetch reads no players there; club websites could not be reached from the build environment. The mechanism is proved against the real changedetection.io on a local test site only (a cosmetic edit produces no change; a real roster edit produces events; a broken page is reported).
- **Club news and roster pages on club websites, MLS NEXT Pro club pages, and the independent tracker** are configured but not verified from here.
- **Timeliness** (how soon after an announcement the monitor sees it) and **completeness** (what share of all real announcements it finds) were not measured. This test starts from announcements I chose; it cannot count announcements it never saw.
- One person (me) labelled the data. The database membership labels used a lookup by surname, initial and club plus my judgement. There are only 5 negative controls.
