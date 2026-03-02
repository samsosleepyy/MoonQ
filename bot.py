import os
import json
import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore, db as rtdb

from flask import Flask

# ================== ENV ==================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_JSON = os.getenv("FIREBASE_JSON")
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")  # Realtime DB URL (optional)
FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "discord_configs")  # Firestore fallback collection
AUTO_UPDATE_SECONDS = int(os.getenv("AUTO_UPDATE_SECONDS", "60"))

# Progress logging controls (to avoid log spam)
PROGRESS_STEP = int(os.getenv("PROGRESS_STEP", "10"))  # prints 10,20,30,...
MAX_PROGRESS_LINES_PER_CHANNEL = int(os.getenv("MAX_PROGRESS_LINES_PER_CHANNEL", "50"))

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")
if not FIREBASE_JSON:
    raise RuntimeError("Missing FIREBASE_JSON environment variable")

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[{ts}] {msg}", flush=True)

# ================== Firebase Init ==================
sa_info = json.loads(FIREBASE_JSON)
cred = credentials.Certificate(sa_info)

if FIREBASE_DATABASE_URL:
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
    log("Firebase initialized with Realtime Database URL (RTDB-first enabled).")
else:
    firebase_admin.initialize_app(cred)
    log("Firebase initialized WITHOUT Realtime Database URL (RTDB-first disabled; Firestore fallback only).")

fs = firestore.client()  # Firestore (fallback only)

# ================== Discord Init ==================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # MUST enable SERVER MEMBERS INTENT in Discord Dev Portal
bot = commands.Bot(command_prefix="!", intents=intents)

# Prevent auto_update and /start from running heavy work at the same time
update_lock = asyncio.Lock()

# ================== Web Server (for Render Web Service port binding) ==================
app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

@app.get("/favicon.ico")
def favicon():
    return "", 204

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()

# ================== Helpers ==================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def has_any_admin_role(member: discord.Member, ignore_role_id: Optional[int] = None) -> bool:
    """True if member has any role (other than ignore_role_id) with Administrator permission."""
    for r in member.roles:
        if r.is_default():
            continue
        if ignore_role_id is not None and r.id == ignore_role_id:
            continue
        if r.permissions.administrator:
            return True
    return False

def is_ticket_channel(ch: discord.abc.GuildChannel) -> bool:
    name = getattr(ch, "name", "") or ""
    return "ticket" in name.lower()

async def ensure_members_loaded(guild: discord.Guild) -> None:
    try:
        await guild.chunk(cache=True)
    except Exception:
        pass

# ================== DB (RTDB first, fallback Firestore) ==================

def _write_payload_sync(guild_id: int, payload: Dict[str, Any]) -> str:
    try:
        if not FIREBASE_DATABASE_URL:
            raise RuntimeError("No FIREBASE_DATABASE_URL configured")
        rtdb.reference(f"discord_configs/{guild_id}").set(payload)
        return "rtdb"
    except Exception:
        fs.collection(FIREBASE_COLLECTION).document(str(guild_id)).set(payload, merge=True)
        return "firestore"

async def write_payload(guild_id: int, payload: Dict[str, Any]) -> str:
    return await asyncio.to_thread(_write_payload_sync, guild_id, payload)

def _read_payload_sync(guild_id: int) -> Dict[str, Any]:
    try:
        if not FIREBASE_DATABASE_URL:
            raise RuntimeError("No FIREBASE_DATABASE_URL configured")
        data = rtdb.reference(f"discord_configs/{guild_id}").get()
        return data or {}
    except Exception:
        doc = fs.collection(FIREBASE_COLLECTION).document(str(guild_id)).get()
        return doc.to_dict() or {}

async def read_payload(guild_id: int) -> Dict[str, Any]:
    return await asyncio.to_thread(_read_payload_sync, guild_id)

def _read_configs_sync() -> List[Tuple[int, Dict[str, Any]]]:
    try:
        if not FIREBASE_DATABASE_URL:
            raise RuntimeError("No FIREBASE_DATABASE_URL configured")
        root = rtdb.reference("discord_configs").get() or {}
        out: List[Tuple[int, Dict[str, Any]]] = []
        for k, v in root.items():
            try:
                gid = int(k)
            except Exception:
                continue
            out.append((gid, v or {}))
        return out
    except Exception:
        out: List[Tuple[int, Dict[str, Any]]] = []
        for doc in fs.collection(FIREBASE_COLLECTION).stream():
            out.append((int(doc.id), doc.to_dict() or {}))
        return out

async def read_configs() -> List[Tuple[int, Dict[str, Any]]]:
    return await asyncio.to_thread(_read_configs_sync)

def build_cached_viewers_map(existing_payload: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    """Return {channel_id: {"viewers": [...], "viewers_count": N}} from existing stored payload."""
    out: Dict[int, Dict[str, Any]] = {}
    for cat in (existing_payload.get("categories") or []):
        for ch in (cat.get("ticket_channels") or []):
            cid = ch.get("channel_id")
            if isinstance(cid, int):
                out[cid] = {
                    "viewers": ch.get("viewers") or [],
                    "viewers_count": int(ch.get("viewers_count") or 0),
                }
    return out

def _progress_print(prefix: str, i: int, total: int, line_counter: List[int]) -> None:
    # line_counter is a single-item list used as mutable integer
    if PROGRESS_STEP <= 0:
        return
    if i % PROGRESS_STEP != 0:
        return
    if line_counter[0] >= MAX_PROGRESS_LINES_PER_CHANNEL:
        return
    line_counter[0] += 1
    log(f"{prefix} {i}/{total}")

# ================== Core build (with cache skip + logs) ==================

async def build_ticket_payload(
    guild: discord.Guild,
    role_id: int,
    category_ids: List[int],
    cached_viewers: Dict[int, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Returns (payload, stats)"""
    role = guild.get_role(role_id)
    if role is None:
        return ({
            "guild_id": guild.id,
            "guild_name": guild.name,
            "error": f"Role {role_id} not found",
            "updated_at": now_iso(),
        }, {"computed_channels": [], "skipped_channels": [], "ticket_channels_total": 0})

    await ensure_members_loaded(guild)

    log(f"Building payload for guild={guild.name} ({guild.id}) role={role.name} ({role.id}) categories={len(category_ids)}")

    # Eligible members (ตามกฎคุณ)
    eligible_members: List[discord.Member] = []
    for idx, m in enumerate(guild.members):
        if idx % 300 == 0:
            await asyncio.sleep(0)  # yield
        if m.bot:
            continue
        if not any(r.id == role.id for r in m.roles):
            continue
        if has_any_admin_role(m, ignore_role_id=role.id):
            continue
        eligible_members.append(m)

    log(f"Eligible members for role '{role.name}': {len(eligible_members)}")

    categories_out: List[Dict[str, Any]] = []
    computed_channels: List[str] = []
    skipped_channels: List[str] = []

    for ci, cid in enumerate(category_ids):
        if ci % 3 == 0:
            await asyncio.sleep(0)

        cat = guild.get_channel(cid)
        if not isinstance(cat, discord.CategoryChannel):
            continue

        ticket_channels_out: List[Dict[str, Any]] = []
        for ch_i, ch in enumerate(cat.channels):
            if ch_i % 10 == 0:
                await asyncio.sleep(0)

            if not is_ticket_channel(ch):
                continue

            cached = cached_viewers.get(ch.id)
            if cached is not None:
                skipped_channels.append(f"{cat.name}/{ch.name}")
                ticket_channels_out.append({
                    "channel_id": ch.id,
                    "channel_name": ch.name,
                    "channel_type": str(ch.type),
                    "viewers_count": int(cached.get("viewers_count") or 0),
                    "viewers": cached.get("viewers") or [],
                    "source": "cache_skip",
                })
                continue

            # Otherwise compute viewers (expensive) with progress logs
            computed_channels.append(f"{cat.name}/{ch.name}")
            prefix = f"Counting viewers for {cat.name}/{ch.name}:"
            line_counter = [0]

            viewers: List[Dict[str, Any]] = []
            total = len(eligible_members)
            for i, m in enumerate(eligible_members, start=1):
                if i % 300 == 0:
                    await asyncio.sleep(0)
                _progress_print(prefix, i, total, line_counter)

                perms = ch.permissions_for(m)
                if perms.view_channel:
                    viewers.append({
                        "user_id": m.id,
                        "username": m.name,
                        "display_name": m.display_name,
                    })

            log(f"Done {cat.name}/{ch.name}: viewers={len(viewers)} checked={total}")
            ticket_channels_out.append({
                "channel_id": ch.id,
                "channel_name": ch.name,
                "channel_type": str(ch.type),
                "viewers_count": len(viewers),
                "viewers": viewers,
                "source": "computed",
            })

        if ticket_channels_out:
            categories_out.append({
                "category_id": cat.id,
                "category_name": cat.name,
                "ticket_channels_count": len(ticket_channels_out),
                "ticket_channels": ticket_channels_out,
            })

    payload = {
        "guild_id": guild.id,
        "guild_name": guild.name,
        "selected_role": {"role_id": role.id, "role_name": role.name},
        "categories_count": len(categories_out),
        "categories": categories_out,
        "updated_at": now_iso(),
    }
    stats = {
        "computed_channels": computed_channels,
        "skipped_channels": skipped_channels,
        "ticket_channels_total": len(computed_channels) + len(skipped_channels),
    }
    return payload, stats

async def update_one_guild(guild: discord.Guild, cfg: Dict[str, Any], source: str) -> None:
    role_id = cfg.get("role_id")
    category_ids = cfg.get("category_ids") or []
    if not role_id or not category_ids:
        return

    existing = await read_payload(guild.id)
    cached_viewers = build_cached_viewers_map(existing)

    payload, stats = await build_ticket_payload(guild, int(role_id), [int(x) for x in category_ids], cached_viewers)
    payload["config"] = cfg
    payload["updated_by"] = {"source": source}

    saved_to = await write_payload(guild.id, payload)

    # Summary logs
    log(f"Saved guild={guild.name} ({guild.id}) to {saved_to}. Ticket channels={stats['ticket_channels_total']} computed={len(stats['computed_channels'])} skipped={len(stats['skipped_channels'])}")

    if stats["computed_channels"]:
        log("Updated channels (computed): " + ", ".join(stats["computed_channels"][:40]) + (" ..." if len(stats["computed_channels"]) > 40 else ""))
    if stats["skipped_channels"]:
        log("Skipped channels (cached): " + ", ".join(stats["skipped_channels"][:40]) + (" ..." if len(stats["skipped_channels"]) > 40 else ""))

# ================== Events ==================

@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        log(f"Command sync failed: {repr(e)}")

    log(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Auto-load configs from DB and run one pass immediately (so it works even if /start was done earlier)
    await asyncio.sleep(3)
    try:
        docs = await read_configs()
        log(f"Loaded configs from DB: {len(docs)} guild(s)")
        # Run one pass now (without waiting for the first loop tick)
        for idx, (guild_id, data) in enumerate(docs):
            await asyncio.sleep(0)
            cfg = (data.get("config") or {})
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                log(f"Guild not found in cache: {guild_id} (bot not in guild?)")
                continue
            async with update_lock:
                await update_one_guild(guild, cfg, source="startup_autoload")
    except Exception as e:
        log(f"Startup autoload error: {repr(e)}")

    if not auto_update.is_running():
        auto_update.start()
        log(f"Auto update started every {AUTO_UPDATE_SECONDS}s")

# ================== Slash Command ==================

@bot.tree.command(
    name="start",
    description="บันทึกเฉพาะช่องที่มีคำว่า ticket และคนที่เห็นช่องนั้น (ตรวจ DB ก่อน ถ้ามีแล้วจะข้าม)",
)
@app_commands.describe(
    bot_role="บทบาทที่ต้องการนับผู้ใช้",
    category1="หมวดหมู่ (บังคับ)",
    category2="หมวดหมู่ (ไม่บังคับ)",
    category3="หมวดหมู่ (ไม่บังคับ)",
)
async def start(
    interaction: discord.Interaction,
    bot_role: discord.Role,
    category1: discord.CategoryChannel,
    category2: Optional[discord.CategoryChannel] = None,
    category3: Optional[discord.CategoryChannel] = None,
):
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)

    categories = [category1]
    for c in [category2, category3]:
        if c and c.id not in {x.id for x in categories}:
            categories.append(c)

    cfg = {
        "role_id": bot_role.id,
        "category_ids": [c.id for c in categories],
        "auto_update_seconds": AUTO_UPDATE_SECONDS,
    }

    async with update_lock:
        await update_one_guild(guild, cfg, source="slash_start")

    await interaction.followup.send(
        "✅ ตั้งค่าและอัปเดทข้อมูลเรียบร้อยแล้ว (ดูรายละเอียดใน Render logs ได้เลย)",
        ephemeral=True
    )

# ================== Auto Update ==================

@tasks.loop(seconds=60)
async def auto_update():
    try:
        docs = await read_configs()
        log(f"Auto update tick: guilds={len(docs)}")

        for idx, (guild_id, data) in enumerate(docs):
            await asyncio.sleep(0)
            cfg = (data.get("config") or {})
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                continue

            async with update_lock:
                await update_one_guild(guild, cfg, source="auto_update")

    except Exception as e:
        log(f"Auto update error: {repr(e)}")

@auto_update.before_loop
async def before_auto_update():
    await bot.wait_until_ready()
    try:
        auto_update.change_interval(seconds=AUTO_UPDATE_SECONDS)
    except Exception:
        pass

# ================== Run ==================
bot.run(DISCORD_TOKEN)
