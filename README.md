# Discord Ticket Viewers Bot - v4 (autoload + progress logs)

## New in v4
- Auto-load stored `/start` config from DB on startup and run an update pass immediately.
- Render logs show what the bot is doing:
  - counts viewers with progress: 10,20,30,... (configurable via `PROGRESS_STEP`)
  - prints summary of updated channels and skipped (cached) channels
- Still RTDB-first with Firestore fallback.
- `/favicon.ico` returns 204 to avoid noisy 404 logs.

## Environment Variables
- DISCORD_TOKEN
- FIREBASE_JSON
- FIREBASE_DATABASE_URL (optional, enables RTDB; otherwise Firestore fallback)
- AUTO_UPDATE_SECONDS (default 60)
- FIREBASE_COLLECTION (default discord_configs)

## Progress logging controls (optional)
- PROGRESS_STEP (default 10)
- MAX_PROGRESS_LINES_PER_CHANNEL (default 50)

## Notes
If you want the bot to recompute a channel that was cached, delete that channel data from the DB payload.
