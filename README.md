# Discord Firebase Render Bot (FIREBASE_JSON + Auto Update)

คำสั่ง:
- `/start bot_role category1 [category2] [category3]`

บอทจะบันทึก:
- ID ของหมวดหมู่ที่เลือก + รายชื่อช่องในหมวดหมู่นั้น
- รายชื่อผู้ใช้ที่มี role ที่เลือก (แต่ **ไม่นับ** คนที่มี role อื่นที่มี permission `Administrator`)
- ลง Firestore และ **Auto Update ทุก 60 วินาที**

---

## สรุปสิ่งที่ต้องทำ (Checklist)

### 1) อัปขึ้น GitHub
- แตก zip
- push ทั้งโปรเจกต์ขึ้น repo

### 2) ตั้งค่า Discord Developer Portal
- เปิด **SERVER MEMBERS INTENT** (สำคัญมาก ไม่งั้นอ่านสมาชิกไม่ได้)

### 3) สร้าง Render Service
แนะนำ: **Background Worker**
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`

### 4) ตั้งค่า Environment Variables บน Render
- `DISCORD_TOKEN` = โทเค็นบอท
- `FIREBASE_JSON` = วาง JSON service account ทั้งก้อนลงไป “ตรงๆ”
- `FIREBASE_COLLECTION` = (ไม่ใส่ก็ได้) default `discord_configs`
- `AUTO_UPDATE_SECONDS` = (ไม่ใส่ก็ได้) default `60`

> หมายเหตุ: ใน JSON จะมีค่า private_key แบบมี `\n` เช่น
> `"private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"`
> ห้ามลบ `\n`

---

## วิธีใช้งาน
1) เชิญบอทเข้าเซิร์ฟเวอร์
2) เรียกคำสั่ง `/start` แล้วเลือก role + category 1-3 อัน
3) ระบบจะบันทึกทันที และอัปเดตให้ใหม่ทุก 1 นาที

---

## Firestore structure (โดยสรุป)
Collection: `discord_configs`
Document ID: `{guild_id}`

Fields:
- `config.role_id`
- `config.category_ids`
- `categories[]`
- `users[]`
- `users_count`
- `updated_at`
