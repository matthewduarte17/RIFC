"""Recruitment monitor: public announcements connected to the players already in this database.

Pipeline (ordinary code, no AI needed):
  sources -> fetch (robots aware, retries, failures reported) -> classify -> extract mentions
  -> match to existing player IDs (never on a similar name alone) -> dedupe into events
  -> inbox (your decisions are kept) -> daily brief.

Nothing here writes to player_seasons, the model, its pairs, its backtest or any dataset use level.
It adds its own tables (prefix rm_) that refer to existing player IDs and never copy player data.
"""
