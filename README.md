# Discord Ticket Viewers Bot (RTDB first, Firestore fallback) - v2

## Fixes in this version
- Adds frequent `await asyncio.sleep(0)` yields during heavy loops so the bot stays responsive (prevents `Unknown interaction`)
- Starts auto update 5 seconds after bot is ready
- Adds `/favicon.ico` route to stop noisy 404 logs

## What it does
- Slash command: `/start bot_role category1 [category2] [category3]`
- Scans channels inside selected categories
- Keeps ONLY channels whose name contains `ticket` (case-insensitive)
- For each ticket channel, saves a list of members who can view it, with rules:
  - must have the selected role
  - if they have ANY other role with Administrator permission -> skipped

## Storage logic
- Writes to Firebase Realtime Database first (needs `FIREBASE_DATABASE_URL`)
- If RTDB fails or not configured -> writes to Firestore (fallback)

## Environment Variables
- DISCORD_TOKEN
- FIREBASE_JSON (paste full service account JSON)
- FIREBASE_DATABASE_URL (optional, to enable RTDB-first writes)
- AUTO_UPDATE_SECONDS (optional, default 60)
- FIREBASE_COLLECTION (optional, Firestore fallback collection name)

## Requirements
Enable SERVER MEMBERS INTENT in Discord Developer Portal.
