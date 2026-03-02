import discord
from discord.ext import commands, tasks
import firebase_admin
from firebase_admin import credentials, firestore
import os
import asyncio

# --- ตั้งค่า Firebase ---
firebase_config = {
  "type": "service_account",
  "project_id": "moonshop-3e906",
  "private_key_id": "e4c0306876d472935bfeea4996bfedda4419e800",
  "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDG2V6QzMKOh6Sr\nYsA0kQ5O0Ddi+NcClOGhi7M7z/wv8XdWCDhkyIF3kKtDtk0dHntWrD8XwKhUGmzg\nvduGbQQDEvA6mgiOKVd0m51lwU62TZkZfeziYOheY6Pi56y1nsQCUVSUaDVweN6R\nwyUdykwpXDn+FqOlOK7smu8u4NWTVdZI38la1bBLdRMllVeKgEgHWiTrPbVxKcl6\n9GIYb9HU0Bvri/6yXw8RsbmYD+OwCGkRQVzgbAwV/2M7h2sYOgP3Sy3e+W6Ojtnb\nvqxNPw/nqocH80lvA6UJhWSUT0g0JaDMyY5Drd1MO9inaz4D3d8Tc3W8u0bgx+vB\n23X5pFZfAgMBAAECggEAEmlF7DpJOVEt2gCGs1dK79kvh6Zqof6O9ZotujgDrZy5\n4+lW713xPtTSRq62bR/JY7kHDnf0HfVkZ1qs3MFzQaWbQJHKgP8q7c0KwcUoOJDu\nwAF80WkPms22+udggmB03ZISNrt/Vy6ZzP04jo2Qh7PWWsRV2pJo/9dIlhqTK9T4\n9pLnboTZJVFq4dR8INB7MyDkHBIyRl/gpkGfMJcrpS6Vy95vJ6L8o4dBAkn/IsAe\nJwp48cWBssZuTv2+wJYULTgsjJxylQfHrZo8HwG2CG5OsXlaEcXCQ5MiMRgZO7YU\nnVFG08kp58yxneVXPSk1f7QaYVu/3SMCQ4BVLnK1QQKBgQDqYvpjJIC3bGcWTLnE\nfcyk+L8np8qvWGWe4EEf4m7PeZy0flphkKW/kvJVcEHMci3m4pktL/Zow1LKn1/a\nAwi66nEuqrceUJa2Uvc6Ayw1JLb1PrzF7RYRFzkZJ0zP4YH1e4i6dE5OfEP9Xz0h\n+pckevsSGsyEYRTgxIdtR5zUWQKBgQDZL3p+SLbdGq96GCrOZXDPSX8tUVvONw0c\nAFz22nCLtQ16OTUy5eUBl+WByoalzq0TWKZ/1lNnPA5CUQ6EfyKSUbA9cPftSjBN\n1VAQh0EMn1R4pp3Pe9wx2BUJmiOdy1PVszg2/h5Y8G2WUi46+0A0OpNNS5wEyCR4\nkudT16qJdwKBgQCvhrYKPxDhzB2bNpQ70RXLSbklgmOoUqOvijNbJGBloaY4CRO4\nUvG9eNdgInQ0HiG/8VxS2cNHi1baBOZsRq9oAyAFmbUOz70+Bv28BRo7JiaZnIUU\nGEvZOrH441SDrVZ8tymasHTgE/F6srL+WkKMAEk7srQMQwO2m5brwKBy6QKBgQCA\nYBIVp0F/vBBRKQvUaB2gSR9FWDvdzqiPDp/kwgWYbvKCdmI9raoJoRFmAKJKS7n+\nH357PeKauOLszCC6rLNwrZxxFN9XgWy/9QCYZHpMzbkOf930EJB9Xe5BeLzovpDV\nVlQ6HUcu5x6/pd/xuSWgOadsHu8f3HXCV4MpCeehzQKBgGY7rInseYEaLYJsJpHH\n5zezagPF7ykfpoKoMIY894T1c+Nfze371lo/jUSR1L2ng4RZeaAswox2pIl8Ppdv\nyknSmtFzQ3sbDmCQf47TLywwExHKZww4EbwzuZAS6pOWAwE3CdrS+BIsasjaf6vt\nVM0Siyqw8wrKCHt5lbRbjbaf\n-----END PRIVATE KEY-----\n",
  "client_email": "firebase-adminsdk-fbsvc@moonshop-3e906.iam.gserviceaccount.com",
  "client_id": "110515965242532293736",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/firebase-adminsdk-fbsvc%40moonshop-3e906.iam.gserviceaccount.com",
  "universe_domain": "googleapis.com"
}

try:
    cred = credentials.Certificate(firebase_config)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ เชื่อมต่อ Firebase สำเร็จ")
except Exception as e:
    print(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ Firebase: {e}")

# --- ตั้งค่า Discord Bot ---
intents = discord.Intents.default()
intents.members = True 
# ใช้ตัวแปร bot เหมือนเดิม แต่คราวนี้เราจะใช้ร่วมกับ Slash Commands
bot = commands.Bot(command_prefix='!', intents=intents)

# --- ฟังก์ชันเซฟข้อมูล (แบบ Synchronous เพื่อรันใน Thread) ---
def save_to_firestore(guild_id, server_data):
    """ฟังก์ชันทำงานเบื้องหลังเพื่อไม่ให้รบกวนการเชื่อมต่อ Discord"""
    try:
        doc_ref = db.collection('server_channels').document(str(guild_id))
        doc_ref.set({"categories": server_data}, merge=True)
        print(f"✅ บันทึกข้อมูลลง Firebase สำเร็จ (Guild ID: {guild_id})")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูล Firestore: {e}")

# --- ฟังก์ชันหลักในการดึงข้อมูลเซิร์ฟเวอร์ ---
async def update_guild_data(guild):
    print(f"🔄 กำลังรวบรวมข้อมูลเซิร์ฟเวอร์: {guild.name}")
    server_data = {}

    for channel in guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue

        category_id = str(channel.category_id) if channel.category_id else "uncategorized"
        
        if category_id not in server_data:
            server_data[category_id] = {}

        viewers = []
        
        for member in guild.members:
            if member.bot:
                continue
            
            perms = channel.permissions_for(member)
            if perms.administrator:
                continue
            if perms.view_channel:
                viewers.append({"id": str(member.id), "name": member.name})
            
            # ป้องกันบอทค้างตอนคนเยอะๆ
            await asyncio.sleep(0) 

        server_data[category_id][channel.name] = {
            "channel_id": str(channel.id),
            "type": str(channel.type),
            "viewers": viewers
        }
        await asyncio.sleep(0.1)

    # ส่งคำสั่งเซฟลง Firebase ไปทำงานเบื้องหลัง (ไม่ให้บอทค้าง)
    await asyncio.to_thread(save_to_firestore, guild.id, server_data)


# --- ฟังก์ชันทำงานอัตโนมัติทุกๆ 1 นาที ---
@tasks.loop(minutes=1)
async def auto_update_task():
    print("⏳ [Auto Update] เริ่มรอบอัปเดตข้อมูลทุก 1 นาที...")
    for guild in bot.guilds:
        await update_guild_data(guild)


# --- คำสั่ง Slash Command: /start ---
@bot.tree.command(name="start", description="สั่งอัปเดตข้อมูลทันที และเริ่มระบบอัปเดตอัตโนมัติทุก 1 นาที")
async def start_command(interaction: discord.Interaction):
    # ตอบกลับผู้ใช้ทันที
    await interaction.response.send_message("✅ รับทราบ! กำลังอัปเดตข้อมูลเซิร์ฟเวอร์ และจะอัปเดตให้อัตโนมัติทุกๆ 1 นาทีครับ", ephemeral=True)
    
    # 1. ทำการอัปเดตของเซิร์ฟเวอร์นี้ทันที 1 ครั้ง
    bot.loop.create_task(update_guild_data(interaction.guild))
    
    # 2. ตรวจสอบว่า loop 1 นาทีทำงานอยู่หรือไม่ ถ้ายังให้เริ่มทำงาน
    if not auto_update_task.is_running():
        auto_update_task.start()
        print("▶️ เริ่มระบบ Auto Update ทุก 1 นาทีแล้ว")


# --- อีเวนต์เมื่อบอทพร้อมทำงาน ---
@bot.event
async def on_ready():
    print(f'✅ บอท {bot.user} รันสำเร็จแล้ว!')
    
    try:
        # คำสั่งนี้คือพระเอกที่จะเคลียร์คำสั่ง / เก่าๆ ออกทั้งหมด และยัดคำสั่งใหม่เข้าไปแทน
        synced = await bot.tree.sync()
        print(f"🧹 ล้างคำสั่งเก่า และซิงค์ Slash Commands ใหม่จำนวน {len(synced)} คำสั่งเรียบร้อยแล้ว!")
    except Exception as e:
        print(f"❌ มีปัญหาตอนซิงค์คำสั่ง Slash Command: {e}")


# --- รันบอท ---
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
if not DISCORD_TOKEN:
    print("❌ ไม่พบ DISCORD_TOKEN ใน Environment Variables")
else:
    bot.run(DISCORD_TOKEN)
