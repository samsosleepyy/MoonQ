import discord
from discord.ext import commands
import firebase_admin
from firebase_admin import credentials, firestore
import os
import asyncio

# --- ตั้งค่า Firebase ผ่าน Render Secret Files ---
# บน Render เราจะตั้งค่าให้ Secret File สร้างไฟล์ชื่อนี้ไว้ในโฟลเดอร์ราก
CRED_PATH = os.environ.get('FIREBASE_KEY_PATH', 'serviceAccountKey.json')

try:
    cred = credentials.Certificate(CRED_PATH)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ เชื่อมต่อ Firebase สำเร็จ")
except Exception as e:
    print(f"❌ เกิดข้อผิดพลาดในการเชื่อมต่อ Firebase: {e}")

# --- ตั้งค่า Discord Bot ---
intents = discord.Intents.default()
intents.members = True 
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'✅ บอท {bot.user} พร้อมทำงานบน Render แล้ว!')

@bot.command()
async def save_channels(ctx):
    await ctx.send("กำลังประมวลผลข้อมูลช่องและสิทธิ์การมองเห็น อาจใช้เวลาสักครู่...")
    
    guild = ctx.guild
    channel_data = {}

    for channel in guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue

        viewers = []
        
        for member in guild.members:
            if member.bot:
                continue
            
            # การเช็คสิทธิ์ไม่ได้ดึงข้อมูลจาก API ใหม่ (ใช้ Cache) 
            # แต่ใช้ CPU หนัก เราจึงต้องพัก Event Loop เพื่อไม่ให้บอทค้าง
            perms = channel.permissions_for(member)
            
            if perms.administrator:
                continue
            
            if perms.view_channel:
                viewers.append({
                    "id": str(member.id),
                    "name": member.name
                })
            
            # ป้องกัน Event Loop Blocking (สำคัญมากสำหรับเซิร์ฟเวอร์ที่มีคนเยอะ)
            await asyncio.sleep(0) 

        channel_data[str(channel.id)] = {
            "channel_name": channel.name,
            "type": str(channel.type),
            "viewers": viewers
        }
        
        # หน่วงเวลาเล็กน้อยระหว่างเปลี่ยนช่อง เพื่อลดภาระการประมวลผลต่อเนื่อง
        await asyncio.sleep(0.1)

    # --- บันทึกลง Firebase Firestore ---
    try:
        doc_ref = db.collection('server_channels').document(str(guild.id))
        doc_ref.set({"channels": channel_data})
        await ctx.send("✅ บันทึกข้อมูลลง Firebase เรียบร้อยแล้ว!")
    except Exception as e:
        await ctx.send(f"❌ เกิดข้อผิดพลาดในการบันทึกข้อมูลลง Firebase: {e}")

# --- ดึง Token จาก Environment Variables ---
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN')
if not DISCORD_TOKEN:
    print("❌ ไม่พบ DISCORD_TOKEN ใน Environment Variables")
else:
    bot.run(DISCORD_TOKEN)
