# Recruitment monitor

Reads public announcements (roster decisions, options declined or exercised, releases, departures, loans, signings),
connects them to players **already in this database**, and keeps a recruitment inbox and a daily brief. It adds three
pages to the app and a small background scan. It does not retrain or change the translation model, its benchmarks, any
dataset use level, any player ID, note or shortlist entry. It contacts no player, agent or club; it only reads public pages.

## Where it is in the app
* **Recruitment Inbox** (Recruitment menu): every announcement, filterable by league, position, age, announcement type,
  shortlist, data freshness, match status and review status (New, Review, Monitor, Pass). Select a row to see three
  separate blocks: **The source confirms**, **Our database records** (with season and export dates), **The system infers**.
  Buttons: save a decision and note, open the player profile, add to the shortlist (the existing shortlist and roles),
  answer an uncertain match, read one pasted announcement link, export CSV.
* **Daily Brief** (Recruitment menu): what is new or changed since the previous brief, shortlist first. Also saved as a file in
  `RIFC Translation Engine/data/briefs/`.
* **Monitor Status** (Admin menu): every source with its last result and failures, player coverage, scan history, the
  automatic schedule, AI status and the log.

## What runs by itself
1. When you start the app (`start.command`), a scan runs in the background if the last good one is older than 20 hours.
2. Optionally, a daily scan at a time you choose (default 07:15) using macOS launchd. `start.command` offers this once.
   Or: Monitor Status > Automatic daily scan, or `python -m engine.monitor schedule install`.
   **Does the Mac need to be awake?** The job runs while you are logged in. If the Mac is asleep at the set time, launchd
   runs it when the Mac wakes. If the Mac is off, it runs at your next login. It does not run at a logged-out login
   window. The app does not need to be open.
3. Each scan: reads the sources, stores each announcement with its link, date and the time we found it, matches names to
   existing player IDs, groups repeats into one event, updates the inbox, writes the brief, and sends a macOS
   notification if there is something to review or a source is failing.

Command line (same code): `python -m engine.monitor run | scan | brief | status | sources | link URL | export file.csv | schedule install|remove|status`.
Use the app's own Python: `"$HOME/RIFC Translation Engine/.venv/bin/python"` with `RIFC_TE_HOME="$HOME/RIFC Translation Engine"` set.

## Sources (and what was actually verified)
Default sources, all read within their robots.txt rules, headlines and short excerpts only:
* USL Championship and USL League One official news feeds (RSS) and their article pages. Verified live on 20 Nov 2026.
* MLSsoccer.com news (page list). Verified live on 20 Nov 2026.
* TopDrawerSoccer men's college soccer (page list). Verified live on 20 Nov 2026.
* MLS NEXT Pro news (mlsnextpro.com). **Not verified**: the build environment could not reach this host. If it fails on your
  Mac it shows as failed on Monitor Status; add the right feed address in `monitor_sources.json`.
* The USL "Transaction Tracker" page is not used: it is out of date (its newest entry has no year and is years old).
* **Google News is off by default.** It would add wide headline coverage, but its robots.txt disallows automated clients and its
  feed notice limits it to personal feed readers. Turn it on only if you accept that: `"google_news": {"enabled": true}` in
  `monitor_config.json`. Its links hide the article text, so those items are headline level only.

A source that fails, is blocked by robots.txt, or returns a page it cannot read is shown with the reason, counted in a row, and
listed in the brief. Retries use backoff for temporary errors only; a refusal is never retried or worked around.

Add club or other feeds without code, in `RIFC Translation Engine/data/monitor_sources.json`:
```json
{"sources": [{"id": "my_club", "name": "Club news", "kind": "rss", "url": "https://example.com/news.rss", "league_hint": "USL League One"}],
 "disable": ["tds_college_men"]}
```
`kind` is `rss` or `html_links` (with `link_include`, a regex the link path must match). Club names that the news spells differently
from the database can be added in `club_aliases.json` as `{"news spelling": "database spelling"}`.

## How names are matched (and what is never done)
* The database spells names the Wyscout way ("B. Diene"); news spells them in full. A link needs **a compatible name and a club
  that appears in that player's recorded history and only one such candidate**. A similar name alone never links anyone.
* Anything ambiguous goes to the review queue ("Match needs review"): two players fit, the club only partly agrees, only his old
  history agrees, or the player's own identity is flagged in Identity Review. You answer once; the answer is remembered for every
  later scan (same name at the same club).
* Players not in the database stay **news leads**: no statistics are shown or invented. College players and MLS first-team
  players are leads (this database holds only USL Championship, USL League One and MLS NEXT Pro).
* Datasets follow your existing use levels: excluded datasets are not used; "Profiles and discovery only" seasons are used to
  recognise players (and are labelled as partial) but never to train anything.

## What it does not claim
* A departure, declined option or "out of contract" does **not** establish that a player is a free agent, affordable, interested
  or eligible to sign. The brief and inbox say this each time. A signing, extension or exercised option is an availability update,
  shown as a lead only for players you follow (shortlist, club verification, or status Monitor).
* **Review priority is a reading order, not a fit rating.** No RIFC needs are configured in this app, so none are invented. The
  factors are listed on every item: shortlist, announcement type, minutes, validated projections at Medium or High confidence,
  Championship percentile, and data freshness. An announcement older than the player's latest record is down-weighted and labelled.
* Names are read from sentences by pattern. Every item keeps the sentence it came from; check the wording before acting.

## AI (optional, off)
Nothing uses AI by default. Fetching, matching, filtering and ranking are ordinary code. An optional reader for articles the
pattern reader cannot parse exists, but it needs `ai.enabled` and `ANTHROPIC_API_KEY` (an API key bills per use and is separate
from a Claude subscription) and prices you enter yourself; it caches every answer, limits calls per scan and a monthly dollar
cap, and discards any name that is not word for word in the article. It was tested with a stand-in service, not live.

## Your data and upgrades
* The monitor adds tables (names starting `rm_`) to `data/engine.duckdb`, referring to existing player IDs only. The first time,
  your database is copied to `backups/pre_monitor_<time>/` before anything is added.
* Your inbox decisions, notes, match answers, scans, source state, briefs and settings are carried through app upgrades by the
  launcher, and the upgrade's own checks verify them.
* A shortlist added from the inbox uses the same shortlist as the rest of the app.
* If the app has to rebuild the database from the raw files (rare; it moves the old file aside, never deletes it), decisions
  in the old file are not copied back. That already applies to your shortlist and notes today.

## Tests
`python -m pytest -q tests/test_monitor.py` (offline, fake internet, controlled players; plus one run over the real release
database proving the engine's tables are unchanged). `python tests/smoke_ui.py` clicks through every page including the new ones.
