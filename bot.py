import os
import json
import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple, Set

import discord
from discord import app_commands
from discord.ext import commands, tasks

import firebase_admin
from firebase_admin import credentials, firestore, db as rtdb

from flask import Flask

# ================== ENV ==================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_JSON = os.getenv("FIREBASE_JSON")
FIREBASE_DATABASE_URL = os.getenv("FIREBASE_DATABASE_URL")  # RTDB URL (optional)
FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "discord_configs")  # Firestore fallback collection

# Default auto update to 5 minutes (ลดโหลด)
AUTO_UPDATE_SECONDS = int(os.getenv("AUTO_UPDATE_SECONDS", "300"))

# Progress logging controls (avoid log spam)
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

fs = firestore.client()  # Firestore fallback

# ================== Discord Init ==================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # MUST enable SERVER MEMBERS INTENT
bot = commands.Bot(command_prefix="!", intents=intents)

# ✅ Lock per guild (concurrency across servers)
guild_locks: Dict[int, asyncio.Lock] = {}

def get_guild_lock(guild_id: int) -> asyncio.Lock:
    lock = guild_locks.get(guild_id)
    if lock is None:
        lock = asyncio.Lock()
        guild_locks[guild_id] = lock
    return lock

# ================== Web Server (Render Web Service port binding) ==================
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

def _progress_print(prefix: str, i: int, total: int, line_counter: List[int]) -> None:
    if PROGRESS_STEP <= 0:
        return
    if i % PROGRESS_STEP != 0:
        return
    if line_counter[0] >= MAX_PROGRESS_LINES_PER_CHANNEL:
        return
    line_counter[0] += 1
    log(f"{prefix} {i}/{total}")

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

# ================== Reverse Viewers Lookup (overwrites-first) ==================

def _get_overwrite(target_overwrites: Dict[Any, discord.PermissionOverwrite], target: Any) -> Optional[discord.PermissionOverwrite]:
    return target_overwrites.get(target)

def _apply_overwrite(perms: discord.Permissions, ow: Optional[discord.PermissionOverwrite]) -> None:
    if ow is None:
        return
    allow, deny = ow.pair()
    perms.handle_overwrite(allow=allow.value, deny=deny.value)

def role_can_view_channel(role: discord.Role, channel: discord.abc.GuildChannel) -> bool:
    """
    Approximate effective view_channel for a ROLE using:
    base role perms -> category overwrites -> channel overwrites (everyone + role only).
    This follows the request to use overwrites and avoids per-member permissions_for loops.
    """
    perms = role.permissions

    everyone = channel.guild.default_role
    category = getattr(channel, "category", None)

    # category overwrites first
    if isinstance(category, discord.CategoryChannel):
        c_ows = category.overwrites
        _apply_overwrite(perms, _get_overwrite(c_ows, everyone))
        _apply_overwrite(perms, _get_overwrite(c_ows, role))

    # then channel overwrites
    ch_ows = channel.overwrites
    _apply_overwrite(perms, _get_overwrite(ch_ows, everyone))
    _apply_overwrite(perms, _get_overwrite(ch_ows, role))

    return bool(perms.view_channel)

def collect_denied_role_ids(channel: discord.abc.GuildChannel) -> Set[int]:
    """Collect role IDs explicitly denied view_channel in category/channel overwrites."""
    denied: Set[int] = set()
    category = getattr(channel, "category", None)

    if isinstance(category, discord.CategoryChannel):
        for target, ow in category.overwrites.items():
            if isinstance(target, discord.Role) and ow.view_channel is False:
                denied.add(target.id)

    for target, ow in channel.overwrites.items():
        if isinstance(target, discord.Role) and ow.view_channel is False:
            denied.add(target.id)

    return denied

def collect_user_overrides(channel: discord.abc.GuildChannel) -> Tuple[Set[int], Set[int]]:
    """Return (user_allow_ids, user_deny_ids) from category+channel overwrites."""
    allow_ids: Set[int] = set()
    deny_ids: Set[int] = set()

    category = getattr(channel, "category", None)
    if isinstance(category, discord.CategoryChannel):
        for target, ow in category.overwrites.items():
            if isinstance(target, discord.Member):
                if ow.view_channel is True:
                    allow_ids.add(target.id)
                elif ow.view_channel is False:
                    deny_ids.add(target.id)

    for target, ow in channel.overwrites.items():
        if isinstance(target, discord.Member):
            if ow.view_channel is True:
                allow_ids.add(target.id)
                deny_ids.discard(target.id)
            elif ow.view_channel is False:
                deny_ids.add(target.id)
                allow_ids.discard(target.id)

    return allow_ids, deny_ids

# ================== Core build (cache skip + reverse lookup + logs) ==================

async def build_ticket_payload(
    guild: discord.Guild,
    role_id: int,
    category_ids: List[int],
    cached_viewers: Dict[int, Dict[str, Any]],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    role = guild.get_role(role_id)
    if role is None:
        return ({
            "guild_id": guild.id,
            "guild_name": guild.name,
            "error": f"Role {role_id} not found",
            "updated_at": now_iso(),
        }, {"computed_channels": [], "skipped_channels": [], "ticket_channels_total": 0})

    await ensure_members_loaded(guild)

    log(f"Building payload guild={guild.name} ({guild.id}) role={role.name} ({role.id}) categories={len(category_ids)}")

    # Build eligible members once per guild
    eligible_by_id: Dict[int, discord.Member] = {}
    for idx, m in enumerate(guild.members):
        if idx % 500 == 0:
            await asyncio.sleep(0)
        if m.bot:
            continue
        if not any(r.id == role.id for r in m.roles):
            continue
        if has_any_admin_role(m, ignore_role_id=role.id):
            continue
        eligible_by_id[m.id] = m

    log(f"Eligible members for role '{role.name}': {len(eligible_by_id)}")

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

            # CACHE skip
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

            computed_channels.append(f"{cat.name}/{ch.name}")

            # If selected role cannot see the channel -> none
            if not role_can_view_channel(role, ch):
                log(f"{cat.name}/{ch.name}: role cannot view (overwrites) -> viewers=0")
                ticket_channels_out.append({
                    "channel_id": ch.id,
                    "channel_name": ch.name,
                    "channel_type": str(ch.type),
                    "viewers_count": 0,
                    "viewers": [],
                    "source": "computed_overwrites_role_blocked",
                })
                continue

            denied_role_ids = collect_denied_role_ids(ch)
            user_allow_ids, user_deny_ids = collect_user_overrides(ch)

            viewer_ids = set(eligible_by_id.keys())

            # Apply user denies
            if user_deny_ids:
                viewer_ids.difference_update(user_deny_ids)

            # Apply deny roles (coarse filter)
            if denied_role_ids:
                filtered = set()
                prefix = f"Filtering deny-roles for {cat.name}/{ch.name}:"
                line_counter = [0]
                total = len(viewer_ids)
                for i, mid in enumerate(viewer_ids, start=1):
                    if i % 500 == 0:
                        await asyncio.sleep(0)
                    _progress_print(prefix, i, total, line_counter)
                    mem = eligible_by_id.get(mid)
                    if mem is None:
                        continue
                    if any(r.id in denied_role_ids for r in mem.roles):
                        continue
                    filtered.add(mid)
                viewer_ids = filtered

            # Add user-allow ids (only if eligible)
            if user_allow_ids:
                viewer_ids.update({uid for uid in user_allow_ids if uid in eligible_by_id})

            viewers: List[Dict[str, Any]] = []
            for idx2, mid in enumerate(viewer_ids):
                if idx2 % 1000 == 0:
                    await asyncio.sleep(0)
                mem = eligible_by_id.get(mid)
                if mem is None:
                    continue
                viewers.append({
                    "user_id": mem.id,
                    "username": mem.name,
                    "display_name": mem.display_name,
                })

            log(f"Done {cat.name}/{ch.name}: viewers={len(viewers)} (reverse/overwrites) eligible={len(eligible_by_id)}")
            ticket_channels_out.append({
                "channel_id": ch.id,
                "channel_name": ch.name,
                "channel_type": str(ch.type),
                "viewers_count": len(viewers),
                "viewers": viewers,
                "source": "computed_reverse_overwrites",
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

    log(f"Saved guild={guild.name} ({guild.id}) to {saved_to}. Ticket channels={stats['ticket_channels_total']} computed={len(stats['computed_channels'])} skipped={len(stats['skipped_channels'])}")
    if stats["computed_channels"]:
        log("Updated channels (computed): " + ", ".join(stats["computed_channels"][:60]) + (" ..." if len(stats["computed_channels"]) > 60 else ""))
    if stats["skipped_channels"]:
        log("Skipped channels (cached): " + ", ".join(stats["skipped_channels"][:60]) + (" ..." if len(stats["skipped_channels"]) > 60 else ""))

# ================== Events ==================

@bot.event
async def on_ready():
    try:
        await bot.tree.sync()
    except Exception as e:
        log(f"Command sync failed: {repr(e)}")

    log(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Auto-load configs and run one pass immediately
    await asyncio.sleep(2)
    try:
        docs = await read_configs()
        log(f"Loaded configs from DB: {len(docs)} guild(s)")

        tasks_run = []
        for (guild_id, data) in docs:
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                log(f"Guild not found in cache: {guild_id} (bot not in guild?)")
                continue
            cfg = (data.get("config") or {})
            lock = get_guild_lock(guild.id)

            async def runner(g: discord.Guild, c: Dict[str, Any], l: asyncio.Lock):
                async with l:
                    await update_one_guild(g, c, source="startup_autoload")

            tasks_run.append(asyncio.create_task(runner(guild, cfg, lock)))

        if tasks_run:
            await asyncio.gather(*tasks_run)

    except Exception as e:
        log(f"Startup autoload error: {repr(e)}")

    if not auto_update.is_running():
        auto_update.start()
        log(f"Auto update started every {AUTO_UPDATE_SECONDS}s")

# ================== Slash Command ==================

@bot.tree.command(
    name="start",
    description="ตั้งค่าและอัปเดทเฉพาะช่องที่มีคำว่า ticket (reverse overwrite lookup + cache skip)",
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

    lock = get_guild_lock(guild.id)
    async with lock:
        await update_one_guild(guild, cfg, source="slash_start")

    await interaction.followup.send(
        "✅ ตั้งค่าและอัปเดทข้อมูลเรียบร้อยแล้ว (ดูรายละเอียดใน Render logs)",
        ephemeral=True
    )

# ================== Auto Update ==================

@tasks.loop(seconds=60)
async def auto_update():
    try:
        docs = await read_configs()
        log(f"Auto update tick: guilds={len(docs)}")

        tasks_run = []
        for (guild_id, data) in docs:
            guild = bot.get_guild(int(guild_id))
            if guild is None:
                continue
            cfg = (data.get("config") or {})
            lock = get_guild_lock(guild.id)

            async def runner(g: discord.Guild, c: Dict[str, Any], l: asyncio.Lock):
                async with l:
                    await update_one_guild(g, c, source="auto_update")

            tasks_run.append(asyncio.create_task(runner(guild, cfg, lock)))

        if tasks_run:
            await asyncio.gather(*tasks_run)

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
