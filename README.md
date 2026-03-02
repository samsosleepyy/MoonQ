# Discord Ticket Viewers Bot (RTDB first, Firestore fallback) - v3 (cache-skip)

## What changed
- Before computing viewers, the bot reads existing data from DB (RTDB first, fallback Firestore).
- If a ticket channel already has stored viewers, it will **skip recomputing** that channel (fast).
  - Channel entry includes `"source": "cache_skip"` when skipped.

## What it does
- Slash command: `/start bot_role category1 [category2] [category3]`
- Scans channels inside selected categories
- Keeps ONLY channels whose name contains `ticket` (case-insensitive)
- For each ticket channel, saves viewers list with rules:
  - must have the selected role
  - if they have ANY other role with Administrator permission -> skipped

## Storage logic
- Writes to Firebase Realtime Database first (needs `FIREBASE_DATABASE_URL`)
- If RTDB fails or not configured -> writes to Firestore (fallback)

## Environment Variables
- DISCORD_TOKEN
- FIREBASE_JSON
- FIREBASE_DATABASE_URL (optional, enables RTDB)
- AUTO_UPDATE_SECONDS (optional, default 60)
- FIREBASE_COLLECTION (optional)

## Note
With cache-skip enabled, changes in permissions/roles will NOT be reflected for channels that were previously stored (until you delete that channel entry from DB).
