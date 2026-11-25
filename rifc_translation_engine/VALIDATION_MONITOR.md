# Validation of the monitor against hand-checked real roster moves

> **Updated 25 Nov 2026:** loan-in follow-up and a second untouched set are in the last section. The sections above it describe the 20 Nov run and are kept as history; the combined totals are now 296 statements.

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


---

# Follow-up, 25 Nov 2026: loan-in misses

## What was actually wrong
Re-running the validation first showed that loan-ins were already **read** correctly (all 44 loan-in statements were read as loan-ins). The misses were in **linking** them to a player, plus one loan-return that was not read:

| Player | Problem | Cause |
|---|---|---|
| Temi Ereku | queued for review | the headline says "**Madison** add … on loan from **Birmingham**": short club names were not recognised, and nothing extracted the club after "loan from"; then a position difference (headline "midfielder", database "CB") blocked it |
| Brigham Larsen | queued for review | two similar database players at Pittsburgh (B. Larsen and B. Titer Larsen); the announcement names two clubs and only one of them matches both |
| Kieran Sargeant | not read | "Forster Ajago returned to Real Salt Lake **and Kieran Sargeant to** Houston Dynamo FC at the conclusion of their loan agreements" (the second name follows an elided verb) |

Not misses: Kiingi, Soto, Dourado, Moussa, Midence and Ramsey are not in the database at all, so "unmatched" is correct. Daniel Flores has a similar name at other clubs and is left unmatched on purpose.

## Fixes
- **Short club names** ("Madison", "Birmingham", "Omaha") resolve to a club only when exactly one club in the whole catalogue could be meant. "Portland" (Hearts of Pine or Timbers II) and "Charlotte" (not a club in our data) are never resolved. (My first version missed the Portland ambiguity; caught by a probe and fixed before this run.)
- **The club a player comes from** ("on loan from X", "via transfer from X") is extracted and resolved the same way, so it can corroborate who he is. The lending club is never read as a player (my first version did this; fixed).
- **Two similar players:** if only one of them matches *both* clubs the announcement names, he is linked and the others stay visible as weaker candidates. With one club named, or a tie on both, it stays a question for you.
- **Position labels:** a position difference between the announcement and the database (providers and clubs label wingers, midfielders and defenders loosely) is now a note, not a blocker, when two named clubs agree. It still blocks when only one club agrees, and a goalkeeper-versus-outfield mismatch always blocks.
- **Loan wording:** "on a season-long / emergency loan", "loan X from Y" (in) versus "loans X to Y" (out), "arrives / moves / heads … on loan", and the elided second name in return lists.

## Results (same hand-labelled data; 296 statements now)
Loan statements only (44 loan-ins, 9 loan returns, 3 loan recalls = 56):

| | Read with right type | **Linked** | Review | Unmatched | Not read | Wrong links |
|---|---|---|---|---|---|---|
| Start of this session | 55 | 46 | 2 | 7 | 1 | 0 |
| After the fixes | **56** | **49** | **0** | 7 | **0** | 0 |

- Exactly three rows changed (Larsen, Ereku, Sargeant), all as intended. **No other row, loan or not, changed.**
- All statements: read 292 of 296, right type 292 of 296, **0 wrong automatic links**, 0 false alarms on 8 award, preview and head-coach controls.
- Tests: 79 pass (5 new ones cover each fix, the Ereku one through the real reading path after my first version of that test passed for the wrong reason because it handed the matcher the second club by hand).

## How much to trust this
- **The two linking fixes (Ereku, Larsen) and the Sargeant fix were made on rows I had already seen, so the improvement from 46 to 49 is not an independent result.** It shows these cases now work and nothing else moved, not that loan-in linking is solved in general.
- **The only unseen data this time** was a new set of 29 hand-labelled League One and Championship headlines (Feb to May 2025 plus two new items) with 3 controls, labelled before running anything. It contains four loan-ins (Moussa, Midence, Pepi, Ramsey): **all four were read as loan-ins before and after**. None of the four is in the database, so this set could not test linking at all.
- **Four non-loan misses on that unseen set are not fixed, on purpose, to keep it an independent measure:** "former … Defender of the Year **Damià Viader joins Madison**", "Christian Chaney **returns to** Charlotte Independence", "Luis Álvarez **returns to** Charlotte Independence for third season", "Omaha **strengthen attack with** forward Prosper Kasim". Unseen read rate for plain signings in that style: **25 of 29 (86%)**, none misclassified.
- Real loan-ins in this data are mostly from the USL league site's wording. Other writers (club sites, local press, MLS NEXT Pro) have not been measured.
