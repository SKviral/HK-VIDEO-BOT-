# 🤖 Telegram Auto-Accept Bot — সেটআপ গাইড

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 📐 Architecture Overview
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```
telegram_bot.py (single file)
├── CONFIG          — Token, Admin IDs, constants
├── DATABASE        — SQLite (WAL mode), init + helpers
├── CORE LOGIC      — do_accept(), process_due_queue()
├── HANDLERS
│   ├── ChatJoinRequestHandler  → auto-accept / queue
│   ├── CommandHandler          → /start, /panel, /health
│   ├── CallbackQueryHandler    → সব button action
│   └── MessageHandler          → multi-step input (state machine)
├── KEYBOARD BUILDERS — সব inline menu
├── SCHEDULER        — queue processor (30s), auto-backup (24h)
└── WELCOME SYSTEM   — per-channel message + photo
```

**Database Tables:**
| Table | কাজ |
|---|---|
| `admins` | multi-admin system |
| `channels` | registered channels + settings |
| `join_requests` | সব request log |
| `pending_queue` | delayed accept queue (restart-safe) |
| `bot_settings` | global key-value store |

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ⚙️ ইনস্টলেশন ও রান গাইড
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Step 1 — Python চেক করুন
```bash
python3 --version   # 3.10+ লাগবে
```

### Step 2 — Virtual environment তৈরি করুন (recommended)
```bash
python3 -m venv venv
source venv/bin/activate        # Linux/Mac
# অথবা
venv\Scripts\activate           # Windows
```

### Step 3 — Dependencies ইনস্টল করুন
```bash
pip install -r requirements.txt
```

### Step 4 — Config সেট করুন
`telegram_bot.py` ফাইলের উপরে এই দুটো লাইন পরিবর্তন করুন:
```python
BOT_TOKEN  = "আপনার_বট_টোকেন"   # @BotFather থেকে নিন
ADMIN_IDS  = [আপনার_user_id]     # @userinfobot থেকে নিন
```

### Step 5 — Bot রান করুন
```bash
python3 telegram_bot.py
```

### Step 6 — Systemd service (Linux production)
```ini
# /etc/systemd/system/acceptbot.service
[Unit]
Description=Telegram Auto-Accept Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/bot
ExecStart=/home/ubuntu/bot/venv/bin/python telegram_bot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable acceptbot
sudo systemctl start acceptbot
sudo systemctl status acceptbot
```

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 📡 চ্যানেল যোগ করার নিয়ম
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Bot-কে আপনার চ্যানেল/গ্রুপের **Administrator** বানান
2. Bot-এর জন্য এই permissions দিন:
   - ✅ Invite Users via Link
   - ✅ Manage Chat (join request approve করতে)
3. `/panel` দিন → **চ্যানেল ম্যানেজমেন্ট** → **নতুন চ্যানেল যোগ করুন**
4. Channel ID পাঠান (যেমন: `-1001234567890`)

> **Channel ID পেতে:** চ্যানেলে @userinfobot কে forward করুন বা https://web.telegram.org তে গিয়ে URL থেকে নিন।

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 🗂️ Admin Button/Menu Structure
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

```
/start বা /panel
└── 🤖 Main Menu
    │
    ├── 📡 চ্যানেল ম্যানেজমেন্ট
    │   ├── [চ্যানেল ১] [চ্যানেল ২] ...  (pagination)
    │   ├── ➕ নতুন চ্যানেল যোগ করুন
    │   └── [চ্যানেল নির্বাচন করলে]
    │       ├── ▶️/⏸️ অটো-একসেপ্ট টগল
    │       ├── ⏱️ ডিলে সেট (মিনিট)
    │       ├── 🔇/🔔 সাইলেন্ট মোড টগল
    │       ├── 💬 ওয়েলকাম মেসেজ সেট
    │       ├── 🖼️ ওয়েলকাম ফটো সেট
    │       ├── 📊 এই চ্যানেলের স্ট্যাটস
    │       ├── ⏳ পেন্ডিং কিউ দেখুন
    │       ├── 🔗 ইনভাইট লিংক সেট
    │       └── 🗑️ চ্যানেল সরান (confirm step)
    │
    ├── 📊 স্ট্যাটিস্টিক্স
    │   ├── সামগ্রিক: total / accepted / pending / today / weekly / monthly
    │   ├── [প্রতিটি চ্যানেলের বাটন]
    │   │   └── per-channel stats + CSV export
    │   └── 🔄 রিফ্রেশ
    │
    ├── ⏳ পেন্ডিং কিউ
    │   ├── সব pending list (max 20 দেখায়)
    │   └── [প্রতিটি চ্যানেলের কিউ আলাদা]
    │
    ├── 👮 অ্যাডমিন ম্যানেজমেন্ট
    │   ├── [অ্যাডমিন ১] [❌ সরান]
    │   ├── [অ্যাডমিন ২] [❌ সরান]
    │   └── ➕ নতুন অ্যাডমিন যোগ (User ID দিয়ে)
    │
    ├── ⚙️ সেটিংস
    │   └── → চ্যানেল সেটিংসে redirect
    │
    ├── 💾 ব্যাকআপ / রিস্টোর
    │   ├── 💾 ব্যাকআপ ডাউনলোড (DB file পাঠায়)
    │   └── 📥 রিস্টোর (DB file upload → confirm → restore)
    │
    └── 🏥 হেলথ চেক
        └── channels / admins / queue / stats / DB size / time
```

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 💬 Commands List
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Command | কাজ |
|---|---|
| `/start` | Main control panel খুলবে |
| `/panel` | একই, control panel |
| `/health` | Quick health report |

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## ⚠️ Important Notes
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### Bot Permissions
Bot-এর BotFather settings এ **"Group Privacy"** বন্ধ করুন:
```
@BotFather → /mybots → আপনার bot → Bot Settings → Group Privacy → Disable
```

### Join Request Feature
Telegram এ join request চালু করতে:
- Channel → Edit → এ গিয়ে **"Approve New Members"** চালু করুন

### Super Admin
`ADMIN_IDS` তে যাদের ID দেওয়া আছে তারা **super admin** — bot এর ভেতর থেকে সরানো যাবে না।

### Delay System
- Delay = 0 → তাৎক্ষণিক accept
- Delay = 5 মিনিট → queue তে রাখবে, 30 সেকেন্ড পর পর scheduler check করবে
- Bot restart হলেও queue এর data SQLite তে থাকে

### Silent Mode
- চালু থাকলে welcome message পাঠাবে না
- শুধু accept করবে

### Welcome Message Formatting
HTML tags সাপোর্টেড:
```html
<b>Bold</b>
<i>Italic</i>
<code>Code</code>
<a href="url">Link</a>
```

### Auto Backup
- প্রতি ২৪ ঘণ্টায় স্বয়ংক্রিয়ভাবে সব super admin কে DB file পাঠাবে
- `AUTO_BACKUP_HOURS` পরিবর্তন করে সময় বাড়ানো/কমানো যাবে

### Logs
- `bot.log` ফাইলে সব activity log হয়
- Console এও দেখা যাবে

---

## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## 🔧 Troubleshooting
## ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| সমস্যা | সমাধান |
|---|---|
| Bot respond করছে না | Token ঠিক আছে কিনা চেক করুন |
| Request accept হচ্ছে না | Bot কে channel admin করুন + "Approve Members" permission দিন |
| Welcome message যাচ্ছে না | User হয়তো bot block করেছে, এটা স্বাভাবিক |
| Channel ID কাজ করছে না | `-100` prefix সহ দিন (যেমন `-1001234567890`) |
| Queue কাজ করছে না | Delay 0 এর বেশি সেট করুন এবং bot চলছে কিনা দেখুন |
