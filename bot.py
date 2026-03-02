import os
import json
import discord
from discord import app_commands
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore
from typing import Optional, Dict, Any, List
import threading
from flask import Flask

# ================== ENV ==================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
FIREBASE_JSON = os.getenv("FIREBASE_JSON")
FIREBASE_COLLECTION = os.getenv("FIREBASE_COLLECTION", "discord_configs")
AUTO_UPDATE_SECONDS = int(os.getenv("AUTO_UPDATE_SECONDS", "60"))  # default 60s

if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable")
if not FIREBASE_JSON:
    raise RuntimeError("Missing FIREBASE_JSON environment variable")

# ================== Firebase Init ==================
sa_info = json.loads(FIREBASE_JSON)
cred = credentials.Certificate(sa_info)
firebase_admin.initialize_app(cred)
db = firestore.client()

# ================== Discord Init ==================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # IMPORTANT: Enable SERVER MEMBERS INTENT in Discord Developer Portal

bot = commands.Bot(command_prefix="!", intents=intents)


def has_any_admin_role(member: discord.Member, ignore_role_id: Optional[int] = None) -> bool:
    """Return True if member has any role (other than ignore_role_id) with Administrator permission."""
    for r in member.roles:
        if r.is_default():
            continue  # @everyone
        if ignore_role_id is not None and r.id == ignore_role_id:
            continue
        if r.permissions.administrator:
            return True
    return False


def extract_category_payload(category: discord.CategoryChannel) -> Dict[str, Any]:
    channels: List[Dict[str, Any]] = []
    for ch in category.channels:
        channels.append({
            "channel_id": ch.id,
            "channel_name": ch.name,
            "channel_type": str(ch.type),
        })
    return {
        "category_id": category.id,
        "category_name": category.name,
        "channels": channels,
    }


async def build_and_save_payload_for_guild(
    guild: discord.Guild,
    role_id: int,
    category_ids: List[int],
    updated_by: Optional[Dict[str, Any]] = None,
) -> None:
    """Rebuild categories/channels and users for the guild config, then save to Firestore."""
    role = guild.get_role(role_id)
    if role is None:
        payload: Dict[str, Any] = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "error": f"Role {role_id} not found in guild",
            "updated_at": firestore.SERVER_TIMESTAMP,
        }
        if updated_by:
            payload["updated_by"] = updated_by
        db.collection(FIREBASE_COLLECTION).document(str(guild.id)).set(payload, merge=True)
        return

    # Categories
    category_payloads: List[Dict[str, Any]] = []
    for cid in category_ids:
        cat = guild.get_channel(cid)
        if isinstance(cat, discord.CategoryChannel):
            category_payloads.append(extract_category_payload(cat))
        else:
            category_payloads.append({
                "category_id": cid,
                "category_name": None,
                "channels": [],
                "error": "Category not found",
            })

    # Users
    matched_users: List[Dict[str, Any]] = []

    # For large guilds, chunk fetches members into cache (requires intents.members)
    try:
        await guild.chunk(cache=True)
    except Exception:
        pass

    for m in guild.members:
        if m.bot:
            continue
        if not any(r.id == role.id for r in m.roles):
            continue
        # Exclude if they have other Administrator roles
        if has_any_admin_role(m, ignore_role_id=role.id):
            continue
        matched_users.append({
            "user_id": m.id,
            "username": m.name,
            "display_name": m.display_name,
        })

    payload2: Dict[str, Any] = {
        "guild_id": guild.id,
        "guild_name": guild.name,
        "selected_role": {"role_id": role.id, "role_name": role.name},
        "categories": category_payloads,
        "users": matched_users,
        "users_count": len(matched_users),
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    if updated_by:
        payload2["updated_by"] = updated_by

    db.collection(FIREBASE_COLLECTION).document(str(guild.id)).set(payload2, merge=True)


@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print("Command sync failed:", e)

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    if not auto_update.is_running():
        auto_update.start()
        print(f"Auto update started: every {AUTO_UPDATE_SECONDS} seconds")


@bot.tree.command(
    name="start",
    description="บันทึกหมวดหมู่ + ช่อง + รายชื่อผู้ใช้ตามบทบาท ลง Firebase (และจะอัปเดตอัตโนมัติทุก 1 นาที)"
)
@app_commands.describe(
    bot_role="บทบาทที่ต้องการนับผู้ใช้",
    category1="หมวดหมู่ (Category) อันที่ 1 (บังคับ)",
    category2="หมวดหมู่ (Category) อันที่ 2 (ไม่บังคับ)",
    category3="หมวดหมู่ (Category) อันที่ 3 (ไม่บังคับ)",
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

    # Save the config so auto-update can use it
    config_patch: Dict[str, Any] = {
        "config": {
            "role_id": bot_role.id,
            "category_ids": [c.id for c in categories],
            "auto_update_seconds": AUTO_UPDATE_SECONDS,
        },
        "last_configured_by": {
            "user_id": interaction.user.id,
            "username": interaction.user.name,
            "display_name": interaction.user.display_name,
        },
        "configured_at": firestore.SERVER_TIMESTAMP,
    }
    db.collection(FIREBASE_COLLECTION).document(str(guild.id)).set(config_patch, merge=True)

    # Build and save full payload now
    await build_and_save_payload_for_guild(
        guild=guild,
        role_id=bot_role.id,
        category_ids=[c.id for c in categories],
        updated_by={
            "user_id": interaction.user.id,
            "username": interaction.user.name,
            "display_name": interaction.user.display_name,
        },
    )

    await interaction.followup.send(
        f"✅ บันทึกสำเร็จ และเปิด Auto Update แล้ว (ทุก {AUTO_UPDATE_SECONDS} วินาที)\n"
        f"- Role: {bot_role.name}\n"
        f"- Categories: {', '.join([c.name for c in categories])}",
        ephemeral=True
    )


@tasks.loop(seconds=60)
async def auto_update():
    # interval will be adjusted in before_loop
    try:
        col = db.collection(FIREBASE_COLLECTION)
        for doc in col.stream():
            data = doc.to_dict() or {}
            cfg = data.get("config") or {}
            role_id = cfg.get("role_id")
            category_ids = cfg.get("category_ids")

            if not role_id or not category_ids:
                continue

            guild_id = int(doc.id)
            guild = bot.get_guild(guild_id)
            if guild is None:
                col.document(doc.id).set({
                    "guild_id": guild_id,
                    "error": "Bot not in guild or guild not available",
                    "updated_at": firestore.SERVER_TIMESTAMP,
                }, merge=True)
                continue

            await build_and_save_payload_for_guild(
                guild=guild,
                role_id=int(role_id),
                category_ids=[int(x) for x in category_ids],
                updated_by={"source": "auto_update"},
            )
    except Exception as e:
        print("Auto update error:", repr(e))


@auto_update.before_loop
async def before_auto_update():
    await bot.wait_until_ready()
    try:
        auto_update.change_interval(seconds=AUTO_UPDATE_SECONDS)
    except Exception:
        pass

app = Flask(__name__)

@app.get("/")
def home():
    return "OK", 200

def run_web():
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_web, daemon=True).start()
bot.run(DISCORD_TOKEN)
