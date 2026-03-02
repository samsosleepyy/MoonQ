# Discord Ticket Viewers Bot - v5

## Changes requested (implemented)
- Reverse viewers lookup: uses channel/category overwrites to decide visibility (much less looping than per-member permissions_for).
- Lock per guild: bot can update multiple servers in parallel safely.
- Auto update default changed from 60s -> 300s (5 minutes).

## Behavior
- Keeps only channels containing "ticket" in the name.
- Viewer rule:
  - must have selected role
  - if has ANY other Administrator role -> skip
- Cache skip: if a ticket channel already has viewers stored, it will be skipped (source=cache_skip).

## Env
- DISCORD_TOKEN
- FIREBASE_JSON
- FIREBASE_DATABASE_URL (optional RTDB-first)
- AUTO_UPDATE_SECONDS (default 300)
- PROGRESS_STEP (default 10)
- MAX_PROGRESS_LINES_PER_CHANNEL (default 50)
