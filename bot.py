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

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")
if not FIREBASE_JSON:
    raise RuntimeError("Missing FIREBASE_JSON environment variable")

# ================== Firebase Init ==================
sa_info = json.loads(FIREBASE_JSON)
cred = credentials.Certificate(sa_info)

if FIREBASE_DATABASE_URL:
    firebase_admin.initialize_app(cred, {"databaseURL": FIREBASE_DATABASE_URL})
else:
    firebase_admin.initialize_app(cred)

fs = firestore.client()  # Firestore (fallback only)

# ================== Discord Init ==================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # MUST enable SERVER MEMBERS INTENT in Discord Dev Portal
bot = commands.Bot(command_prefix="!", intents=intents)

# Prevent auto_update and /start from blocking each other
update_lock = asyncio.Lock()

# ================== Web Server (for Render Web Service port binding) ==================
app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

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
    # Helps for larger guilds
    try:
        await guild.chunk(cache=True)
    except Exception:
        pass

async def build_ticket_payload(
    guild: discord.Guild,
    role_id: int,
    category_ids: List[int],
) -> Dict[str, Any]:
    role = guild.get_role(role_id)
    if role is None:
        return {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "error": f"Role {role_id} not found",
            "updated_at": now_iso(),
        }

    await ensure_members_loaded(guild)

    # Eligible members (ตามกฎคุณ)
    eligible_members: List[discord.Member] = []
    for m in guild.members:
        if m.bot:
            continue
        if not any(r.id == role.id for r in m.roles):
            continue
        if has_any_admin_role(m, ignore_role_id=role.id):
            continue
        eligible_members.append(m)

    categories_out: List[Dict[str, Any]] = []

    for cid in category_ids:
        cat = guild.get_channel(cid)
        if not isinstance(cat, discord.CategoryChannel):
            continue

        ticket_channels_out: List[Dict[str, Any]] = []
        for ch in cat.channels:
            if not is_ticket_channel(ch):
                continue

            viewers: List[Dict[str, Any]] = []
            for m in eligible_members:
                perms = ch.permissions_for(m)
                if perms.view_channel:
                    viewers.append({
                        "user_id": m.id,
                        "username": m.name,
                        "display_name": m.display_name,
                    })

            ticket_channels_out.append({
                "channel_id": ch.id,
                "channel_name": ch.name,
                "channel_type": str(ch.type),
                "viewers_count": len(viewers),
                "viewers": viewers,
            })

        if ticket_channels_out:
            categories_out.append({
                "category_id": cat.id,
                "category_name": cat.name,
                "ticket_channels_count": len(ticket_channels_out),
                "ticket_channels": ticket_channels_out,
            })

    return {
        "guild_id": guild.id,
        "guild_name": guild.name,
        "selected_role": {"role_id": role.id, "role_name": role.name},
        "categories_count": len(categories_out),
        "categories": categories_out,
        "updated_at": now_iso(),
    }

# ================== DB Write (RTDB first, fallback Firestore) ==================

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

# ================== DB Read configs (RTDB first, fallback Firestore) ==================

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

# ================== Events ==================

@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        print("Command sync failed:", repr(e))

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    if not auto_update.is_running():
        auto_update.start()
        print(f"Auto update started every {AUTO_UPDATE_SECONDS}s")

# ================== Slash Command ==================

@bot.tree.command(
    name="start",
    description="บันทึกเฉพาะช่องที่มีคำว่า ticket และคนที่เห็นช่องนั้น (RTDB ก่อน, fallback Firestore)",
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
    # IMPORTANT: defer immediately to avoid Unknown interaction
    await interaction.response.defer(ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("คำสั่งนี้ใช้ได้เฉพาะในเซิร์ฟเวอร์เท่านั้น", ephemeral=True)

    categories = [category1]
    for c in [category2, category3]:
        if c and c.id not in {x.id for x in categories}:
            categories.append(c)

    role_id = bot_role.id
    category_ids = [c.id for c in categories]

    async with update_lock:
        payload = await build_ticket_payload(guild, role_id, category_ids)

        payload["config"] = {
            "role_id": role_id,
            "category_ids": category_ids,
            "auto_update_seconds": AUTO_UPDATE_SECONDS,
        }
        payload["updated_by"] = {
            "source": "slash_start",
            "user_id": interaction.user.id,
            "username": interaction.user.name,
            "display_name": interaction.user.display_name,
        }

        saved_to = await write_payload(guild.id, payload)

    total_ticket_channels = sum(c.get("ticket_channels_count", 0) for c in payload.get("categories", []))

    await interaction.followup.send(
        f"✅ บันทึกสำเร็จ\n"
        f"- Saved to: {saved_to}\n"
        f"- Role: {bot_role.name}\n"
        f"- Categories scanned: {len(categories)}\n"
        f"- Ticket channels found: {total_ticket_channels}\n"
        f"- Auto update: every {AUTO_UPDATE_SECONDS}s",
        ephemeral=True
    )

# ================== Auto Update ==================

@tasks.loop(seconds=60)
async def auto_update():
    try:
        async with update_lock:
            docs = await read_configs()

            for guild_id, data in docs:
                cfg = data.get("config") or {}
                role_id = cfg.get("role_id")
                category_ids = cfg.get("category_ids")
                if not role_id or not category_ids:
                    continue

                guild = bot.get_guild(int(guild_id))
                if guild is None:
                    continue

                payload = await build_ticket_payload(guild, int(role_id), [int(x) for x in category_ids])
                payload["config"] = cfg
                payload["updated_by"] = {"source": "auto_update"}

                await write_payload(int(guild_id), payload)

    except Exception as e:
        print("Auto update error:", repr(e))

@auto_update.before_loop
async def before_auto_update():
    await bot.wait_until_ready()
    try:
        auto_update.change_interval(seconds=AUTO_UPDATE_SECONDS)
    except Exception:
        pass

# ================== Run ==================
bot.run(DISCORD_TOKEN)
