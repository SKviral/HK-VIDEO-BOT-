#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║        Telegram Auto-Accept Request Bot — Multi-Channel         ║
║                     Single-File Version                         ║
╚══════════════════════════════════════════════════════════════════╝

Architecture:
  - python-telegram-bot (v20+, async)
  - SQLite for persistent storage
  - Inline keyboard UI for all admin controls
  - APScheduler for delayed accepts & auto-backup
  - Per-channel: delay, welcome message, photo, stats
"""

# ─── IMPORTS ────────────────────────────────────────────────────────────────
import asyncio
import io
import json
import logging
import os
import shutil
import sqlite3
import traceback
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Optional
from pymongo import MongoClient

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import (
    Bot,
    Chat,
    ChatJoinRequest,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─── CONFIG ─────────────────────────────────────────────────────────────────
# টোকেন প্রাইভেসি: প্রথমে এনভায়রনমেন্ট ভেরিয়েবল চেক করবে
BOT_TOKEN = os.getenv("APPROVE_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN")

ADMIN_IDS = [7756038841]  # ← প্রথম super-admin
# মেইন অ্যাডমিন আইডি এনভায়রনমেন্ট ভেরিয়েবল থেকে রিড করে ডাইনামিকালি অ্যাড করা হচ্ছে যাতে /start কাজ করে
main_admin_env = os.getenv("MAIN_ADMIN_ID")
if main_admin_env:
    try:
        ADMIN_IDS.append(int(main_admin_env))
    except ValueError:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "bot_data.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
DEFAULT_DELAY_SECONDS = 0  # 0 = instant accept
AUTO_BACKUP_HOURS = 24  # প্রতি ২৪ ঘণ্টায় auto-backup

# ─── LOGGING ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("AutoAcceptBot")

# ─── DATABASE ────────────────────────────────────────────────────────────────
MONGO_URL = os.getenv("MONGO_URL")
USING_MONGO = False

# MongoDB Initialization
try:
    if MONGO_URL and MONGO_URL != "আপনার_MongoDB_URL":
        mongo_client = MongoClient(MONGO_URL)
        db = mongo_client['telegram_bot_db']
        
        # Collections
        admins_col = db['approve_admins']
        channels_col = db['approve_channels']
        requests_col = db['approve_join_requests']
        queue_col = db['approve_queue']
        settings_col = db['approve_settings']
        
        # Create indexes
        channels_col.create_index("channel_id", unique=True, background=True)
        requests_col.create_index("channel_id", background=True)
        requests_col.create_index("status", background=True)
        queue_col.create_index("accept_after", background=True)
        admins_col.create_index("user_id", unique=True, background=True)
        
        # Insert initial super admins
        for uid in ADMIN_IDS:
            admins_col.update_one(
                {"user_id": uid},
                {"$set": {"user_id": uid, "username": "super_admin", "added_by": uid, "added_at": datetime.utcnow().isoformat()}},
                upsert=True
            )
        logger.info("MongoDB Initialized successfully for Approve Bot ✓")
        USING_MONGO = True
except Exception as e:
    logger.error(f"❌ Failed to connect to MongoDB: {e}")
    USING_MONGO = False


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """সব SQLite টেবিল তৈরি করো (ফলব্যাক হিসেবে)।"""
    try:
        with get_db() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                added_by    INTEGER,
                added_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS channels (
                channel_id      INTEGER PRIMARY KEY,
                title           TEXT,
                username        TEXT,
                invite_link     TEXT,
                auto_accept     INTEGER DEFAULT 1,
                delay_seconds   INTEGER DEFAULT 0,
                silent_mode     INTEGER DEFAULT 0,
                welcome_msg     TEXT,
                welcome_photo   TEXT,
                welcome_buttons TEXT, -- JSON array
                request_msg_enabled INTEGER DEFAULT 0,
                request_msg     TEXT,
                request_photo   TEXT,
                request_buttons TEXT, -- JSON array
                category        TEXT DEFAULT 'Uncategorized',
                added_by        INTEGER,
                added_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS join_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      INTEGER,
                user_id         INTEGER,
                username        TEXT,
                full_name       TEXT,
                requested_at    TEXT DEFAULT (datetime('now')),
                accept_at       TEXT,
                accepted_at     TEXT,
                status          TEXT DEFAULT 'pending',
                UNIQUE(channel_id, user_id, requested_at)
            );

            CREATE TABLE IF NOT EXISTS pending_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id      INTEGER,
                user_id         INTEGER,
                username        TEXT,
                full_name       TEXT,
                queued_at       TEXT DEFAULT (datetime('now')),
                accept_after    TEXT,
                UNIQUE(channel_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS bot_settings (
                key     TEXT PRIMARY KEY,
                value   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_requests_channel ON join_requests(channel_id);
            CREATE INDEX IF NOT EXISTS idx_requests_status  ON join_requests(status);
            CREATE INDEX IF NOT EXISTS idx_queue_accept     ON pending_queue(accept_after);
            """)

            # Table dynamic schema update (SQLite ALTER TABLE)
            try:
                conn.execute("ALTER TABLE channels ADD COLUMN category TEXT DEFAULT 'Uncategorized'")
            except sqlite3.OperationalError:
                pass

            # প্রথম super-admin insert
            for uid in ADMIN_IDS:
                conn.execute(
                    "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
                    (uid, uid),
                )
        logger.info("SQLite Database initialized ✓")
    except Exception as e:
        logger.error(f"init_db sqlite error: {e}")


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    if USING_MONGO:
        try:
            return admins_col.find_one({"user_id": user_id}) is not None
        except Exception as e:
            logger.error(f"is_admin mongo error: {e}")
    # SQLite fallback
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
            ).fetchone()
            return row is not None
    except Exception as e:
        logger.error(f"is_admin sqlite error: {e}")
        return False


def get_admins() -> list[dict]:
    if USING_MONGO:
        try:
            return list(admins_col.find({}, {"_id": 0}))
        except Exception as e:
            logger.error(f"get_admins mongo error: {e}")
    try:
        with get_db() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()]
    except Exception as e:
        logger.error(f"get_admins sqlite error: {e}")
        return []


def add_admin(user_id: int, username: str, added_by: int) -> bool:
    if USING_MONGO:
        try:
            admins_col.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id": user_id,
                    "username": username,
                    "added_by": added_by,
                    "added_at": datetime.utcnow().isoformat()
                }},
                upsert=True
            )
            return True
        except Exception as e:
            logger.error(f"add_admin mongo error: {e}")
            return False
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO admins (user_id, username, added_by) VALUES (?,?,?)",
                (user_id, username, added_by),
            )
        return True
    except Exception as e:
        logger.error(f"add_admin error: {e}")
        return False


def remove_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return False  # super-admin সরানো যাবে না
    if USING_MONGO:
        try:
            admins_col.delete_one({"user_id": user_id})
            return True
        except Exception as e:
            logger.error(f"remove_admin mongo error: {e}")
            return False
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
        return True
    except Exception as e:
        logger.error(f"remove_admin error: {e}")
        return False


def get_channels() -> list[dict]:
    if USING_MONGO:
        try:
            return list(channels_col.find({}, {"_id": 0}).sort("added_at", 1))
        except Exception as e:
            logger.error(f"get_channels mongo error: {e}")
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()
            res = []
            for r in rows:
                d = dict(r)
                for key in ["welcome_buttons", "request_buttons"]:
                    if d.get(key):
                        try:
                            d[key] = json.loads(d[key])
                        except Exception:
                            d[key] = []
                    else:
                        d[key] = []
                res.append(d)
            return res
    except Exception as e:
        logger.error(f"get_channels sqlite error: {e}")
        return []


def get_channel(channel_id: int) -> Optional[dict]:
    if USING_MONGO:
        try:
            return channels_col.find_one({"channel_id": channel_id}, {"_id": 0})
        except Exception as e:
            logger.error(f"get_channel mongo error: {e}")
    try:
        with get_db() as conn:
            row = conn.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
            if row:
                d = dict(row)
                for key in ["welcome_buttons", "request_buttons"]:
                    if d.get(key):
                        try:
                            d[key] = json.loads(d[key])
                        except Exception:
                            d[key] = []
                    else:
                        d[key] = []
                return d
            return None
    except Exception as e:
        logger.error(f"get_channel sqlite error: {e}")
        return None


def upsert_channel(channel_id: int, title: str, username: str, invite_link: str, added_by: int):
    if USING_MONGO:
        try:
            channels_col.update_one(
                {"channel_id": channel_id},
                {
                    "$set": {
                        "channel_id": channel_id,
                        "title": title,
                        "username": username,
                        "invite_link": invite_link,
                        "added_by": added_by
                    },
                    "$setOnInsert": {
                        "auto_accept": 1,
                        "delay_seconds": 0,
                        "silent_mode": 0,
                        "welcome_msg": None,
                        "welcome_photo": None,
                        "welcome_buttons": [],
                        "request_msg_enabled": 0,
                        "request_msg": None,
                        "request_photo": None,
                        "request_buttons": [],
                        "category": "Uncategorized",
                        "added_at": datetime.utcnow().isoformat()
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            logger.error(f"upsert_channel mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT INTO channels (channel_id, title, username, invite_link, added_by)
                VALUES (?,?,?,?,?)
                ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,
                    username=excluded.username, invite_link=excluded.invite_link
            """, (channel_id, title, username, invite_link, added_by))
    except Exception as e:
        logger.error(f"upsert_channel sqlite error: {e}")


def update_channel_setting(channel_id: int, key: str, value):
    allowed = {
        "auto_accept", "delay_seconds", "silent_mode",
        "welcome_msg", "welcome_photo", "invite_link",
        "welcome_buttons", "request_msg_enabled", "request_msg",
        "request_photo", "request_buttons", "category"
    }
    if key not in allowed:
        return
    if USING_MONGO:
        try:
            channels_col.update_one(
                {"channel_id": channel_id},
                {"$set": {key: value}}
            )
            return
        except Exception as e:
            logger.error(f"update_channel_setting mongo error: {e}")
    try:
        val_to_save = json.dumps(value) if isinstance(value, (list, dict)) else value
        with get_db() as conn:
            conn.execute(f"UPDATE channels SET {key}=? WHERE channel_id=?", (val_to_save, channel_id))
    except Exception as e:
        logger.error(f"update_channel_setting sqlite error: {e}")


def remove_channel(channel_id: int):
    if USING_MONGO:
        try:
            channels_col.delete_one({"channel_id": channel_id})
            queue_col.delete_many({"channel_id": channel_id})
            return
        except Exception as e:
            logger.error(f"remove_channel mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
            conn.execute("DELETE FROM pending_queue WHERE channel_id=?", (channel_id,))
    except Exception as e:
        logger.error(f"remove_channel sqlite error: {e}")


def log_request(channel_id, user_id, username, full_name, accept_after_dt=None):
    accept_at = accept_after_dt.isoformat() if accept_after_dt else None
    if USING_MONGO:
        try:
            requests_col.update_one(
                {"channel_id": channel_id, "user_id": user_id, "requested_at": {"$exists": True}},
                {
                    "$setOnInsert": {
                        "channel_id": channel_id,
                        "user_id": user_id,
                        "username": username,
                        "full_name": full_name,
                        "requested_at": datetime.utcnow().isoformat(),
                        "accept_at": accept_at,
                        "status": "pending" if accept_at else "accepted"
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            logger.error(f"log_request mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO join_requests
                    (channel_id, user_id, username, full_name, accept_at, status)
                VALUES (?,?,?,?,?,?)
            """, (channel_id, user_id, username, full_name, accept_at,
                  "pending" if accept_at else "accepted"))
    except Exception as e:
        logger.warning(f"log_request sqlite error: {e}")


def mark_accepted(channel_id, user_id):
    if USING_MONGO:
        try:
            requests_col.update_one(
                {"channel_id": channel_id, "user_id": user_id, "status": "pending"},
                {"$set": {"status": "accepted", "accepted_at": datetime.utcnow().isoformat()}}
            )
            return
        except Exception as e:
            logger.error(f"mark_accepted mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute("""
                UPDATE join_requests
                SET status='accepted', accepted_at=datetime('now')
                WHERE channel_id=? AND user_id=? AND status='pending'
            """, (channel_id, user_id))
    except Exception as e:
        logger.error(f"mark_accepted sqlite error: {e}")


def enqueue(channel_id, user_id, username, full_name, accept_after: datetime):
    if USING_MONGO:
        try:
            queue_col.update_one(
                {"channel_id": channel_id, "user_id": user_id},
                {
                    "$set": {
                        "channel_id": channel_id,
                        "user_id": user_id,
                        "username": username,
                        "full_name": full_name,
                        "queued_at": datetime.utcnow().isoformat(),
                        "accept_after": accept_after.isoformat()
                    }
                },
                upsert=True
            )
            return
        except Exception as e:
            logger.error(f"enqueue mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO pending_queue
                    (channel_id, user_id, username, full_name, accept_after)
                VALUES (?,?,?,?,?)
            """, (channel_id, user_id, username, full_name, accept_after.isoformat()))
    except Exception as e:
        logger.error(f"enqueue sqlite error: {e}")


def dequeue(channel_id, user_id):
    if USING_MONGO:
        try:
            queue_col.delete_one({"channel_id": channel_id, "user_id": user_id})
            return
        except Exception as e:
            logger.error(f"dequeue mongo error: {e}")
    try:
        with get_db() as conn:
            conn.execute(
                "DELETE FROM pending_queue WHERE channel_id=? AND user_id=?",
                (channel_id, user_id),
            )
    except Exception as e:
        logger.error(f"dequeue sqlite error: {e}")


def get_due_queue() -> list[dict]:
    now = datetime.utcnow().isoformat()
    if USING_MONGO:
        try:
            return list(queue_col.find({"accept_after": {"$lte": now}}, {"_id": 0}))
        except Exception as e:
            logger.error(f"get_due_queue mongo error: {e}")
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM pending_queue WHERE accept_after <= ?", (now,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_due_queue sqlite error: {e}")
        return []


def get_pending_queue(channel_id: int = None) -> list[dict]:
    if USING_MONGO:
        try:
            q = {"channel_id": channel_id} if channel_id else {}
            return list(queue_col.find(q, {"_id": 0}).sort("accept_after", 1))
        except Exception as e:
            logger.error(f"get_pending_queue mongo error: {e}")
    try:
        with get_db() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM pending_queue WHERE channel_id=? ORDER BY accept_after",
                    (channel_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pending_queue ORDER BY accept_after"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"get_pending_queue sqlite error: {e}")
        return []


def get_stats(channel_id: int = None) -> dict:
    if USING_MONGO:
        try:
            q = {"channel_id": channel_id} if channel_id else {}
            total = requests_col.count_documents(q)
            
            q_acc = q.copy()
            q_acc["status"] = "accepted"
            accepted = requests_col.count_documents(q_acc)
            
            q_pend = q.copy()
            q_pend["status"] = "pending"
            pending = requests_col.count_documents(q_pend)
            
            def period_count_mongo(days):
                since = (datetime.utcnow() - timedelta(days=days)).isoformat()
                q_per = q.copy()
                q_per["requested_at"] = {"$gte": since}
                return requests_col.count_documents(q_per)
                
            return {
                "total": total,
                "accepted": accepted,
                "pending": pending,
                "today": period_count_mongo(1),
                "weekly": period_count_mongo(7),
                "monthly": period_count_mongo(30),
            }
        except Exception as e:
            logger.error(f"get_stats mongo error: {e}")
    try:
        with get_db() as conn:
            base = "WHERE channel_id=?" if channel_id else ""
            params = (channel_id,) if channel_id else ()

            total = conn.execute(
                f"SELECT COUNT(*) FROM join_requests {base}", params
            ).fetchone()[0]
            accepted = conn.execute(
                f"SELECT COUNT(*) FROM join_requests {base} {'AND' if base else 'WHERE'} status='accepted'",
                params,
            ).fetchone()[0]
            pending = conn.execute(
                f"SELECT COUNT(*) FROM join_requests {base} {'AND' if base else 'WHERE'} status='pending'",
                params,
            ).fetchone()[0]

            def period_count(days):
                since = (datetime.utcnow() - timedelta(days=days)).isoformat()
                q = f"SELECT COUNT(*) FROM join_requests {base} {'AND' if base else 'WHERE'} requested_at >= ?"
                return conn.execute(q, (*params, since)).fetchone()[0]

            return {
                "total": total,
                "accepted": accepted,
                "pending": pending,
                "today": period_count(1),
                "weekly": period_count(7),
                "monthly": period_count(30),
            }
    except Exception as e:
        logger.error(f"get_stats sqlite error: {e}")
        return {"total": 0, "accepted": 0, "pending": 0, "today": 0, "weekly": 0, "monthly": 0}


def make_backup() -> str:
    """DB backup তৈরি করো (MongoDB বা SQLite), path return করো।"""
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    if USING_MONGO:
        try:
            backup_data = {
                "admins": list(admins_col.find({}, {"_id": 0})),
                "channels": list(channels_col.find({}, {"_id": 0})),
                "requests": list(requests_col.find({}, {"_id": 0})),
                "queue": list(queue_col.find({}, {"_id": 0})),
            }
            dest = f"{BACKUP_DIR}/backup_{ts}.json"
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2)
            logger.info(f"MongoDB Backup created: {dest}")
            return dest
        except Exception as e:
            logger.error(f"make_backup mongo error: {e}")
            
    dest = f"{BACKUP_DIR}/backup_{ts}.db"
    shutil.copy2(DB_PATH, dest)
    logger.info(f"SQLite Backup created: {dest}")
    return dest


def restore_from_file(file_path: str) -> bool:
    try:
        if file_path.endswith(".json") and USING_MONGO:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            admins_col.delete_many({})
            if data.get("admins"):
                admins_col.insert_many(data["admins"])
                
            channels_col.delete_many({})
            if data.get("channels"):
                channels_col.insert_many(data["channels"])
                
            requests_col.delete_many({})
            if data.get("requests"):
                requests_col.insert_many(data["requests"])
                
            queue_col.delete_many({})
            if data.get("queue"):
                queue_col.insert_many(data["queue"])
                
            logger.info("MongoDB restore successful!")
            return True
            
        elif file_path.endswith(".db"):
            conn = sqlite3.connect(file_path)
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            conn.close()
            shutil.copy2(file_path, DB_PATH)
            logger.info("SQLite restore successful!")
            return True
            
        return False
    except Exception as e:
        logger.error(f"restore error: {e}")
        return False


# ─── DECORATORS ──────────────────────────────────────────────────────────────
def admin_only(func):
    @wraps(func)
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = (update.effective_user or update.callback_query.from_user).id
        if not is_admin(uid):
            if update.message:
                await update.message.reply_text("⛔ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Admin only!", show_alert=True)
            return
        return await func(update, ctx)
    return wrapper


# ─── KEYBOARD BUILDERS ───────────────────────────────────────────────────────
def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 চ্যানেল ম্যানেজমেন্ট", callback_data="menu:channels")],
        [InlineKeyboardButton("📢 ব্রডকাস্ট পোস্ট", callback_data="menu:broadcast")],
        [InlineKeyboardButton("📊 স্ট্যাটিস্টিক্স",       callback_data="menu:stats"),
         InlineKeyboardButton("⏳ পেন্ডিং কিউ",           callback_data="menu:queue")],
        [InlineKeyboardButton("👮 অ্যাডমিন ম্যানেজমেন্ট", callback_data="menu:admins")],
        [InlineKeyboardButton("⚙️ সেটিংস",                callback_data="menu:settings"),
         InlineKeyboardButton("💾 ব্যাকআপ / রিস্টোর",    callback_data="menu:backup")],
        [InlineKeyboardButton("🏥 হেলথ চেক",              callback_data="menu:health")],
    ])


def kb_channels_list(page=0) -> InlineKeyboardMarkup:
    channels = get_channels()
    per_page = 5
    start = page * per_page
    chunk = channels[start: start + per_page]
    rows = []
    for ch in chunk:
        icon = "✅" if ch["auto_accept"] else "⏸️"
        rows.append([InlineKeyboardButton(
            f"{icon} {ch['title'][:28]}",
            callback_data=f"ch:manage:{ch['channel_id']}"
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"ch:list:{page-1}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{max(1,(len(channels)-1)//per_page+1)}", callback_data="noop"))
    if start + per_page < len(channels):
        nav.append(InlineKeyboardButton("▶️", callback_data=f"ch:list:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("➕ নতুন চ্যানেল যোগ করুন", callback_data="ch:add_guide")])
    rows.append([InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def kb_channel_manage(channel_id: int) -> InlineKeyboardMarkup:
    ch = get_channel(channel_id)
    if not ch:
        return kb_main_menu()
    category = ch.get("category") or "Uncategorized"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ অটো-একসেপ্ট সেটিংস", callback_data=f"ch:menu_aa:{channel_id}")],
        [InlineKeyboardButton("💬 তাৎক্ষণিক মেসেজ (Msg 1) সেটিংস", callback_data=f"ch:menu_msg1:{channel_id}")],
        [InlineKeyboardButton("🎉 অনুমোদন মেসেজ (Msg 2) সেটিংস", callback_data=f"ch:menu_msg2:{channel_id}")],
        [InlineKeyboardButton(f"📂 ক্যাটাগরি: {category}", callback_data=f"ch:set_cat:{channel_id}")],
        [InlineKeyboardButton("📊 স্ট্যাটস", callback_data=f"stats:channel:{channel_id}"),
         InlineKeyboardButton("⏳ পেন্ডিং কিউ", callback_data=f"queue:channel:{channel_id}")],
        [InlineKeyboardButton("🗑️ চ্যানেল সরিয়ে দিন", callback_data=f"ch:remove:{channel_id}")],
        [InlineKeyboardButton("🔙 চ্যানেল লিস্ট", callback_data="ch:list:0")],
    ])


def kb_channel_aa(channel_id: int) -> InlineKeyboardMarkup:
    ch = get_channel(channel_id)
    if not ch:
        return kb_main_menu()
    aa_txt = "⏸️ অটো-একসেপ্ট বন্ধ করুন" if ch.get("auto_accept", 1) else "▶️ অটো-একসেপ্ট চালু করুন"
    sl_txt = "🔔 সাইলেন্ট মোড বন্ধ করুন" if ch.get("silent_mode", 0) else "🔇 সাইলেন্ট মোড চালু করুন"
    delay_m = ch.get("delay_seconds", 0) // 60
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(aa_txt, callback_data=f"ch:toggle_aa:{channel_id}")],
        [InlineKeyboardButton(f"⏱️ ডিলে: {delay_m} মিনিট → পরিবর্তন", callback_data=f"ch:set_delay:{channel_id}")],
        [InlineKeyboardButton(sl_txt, callback_data=f"ch:toggle_silent:{channel_id}")],
        [InlineKeyboardButton("🔙 ব্যাক", callback_data=f"ch:manage:{channel_id}")]
    ])


def kb_channel_msg1(channel_id: int) -> InlineKeyboardMarkup:
    ch = get_channel(channel_id)
    if not ch:
        return kb_main_menu()
    m1_toggle_txt = "⏸️ তাৎক্ষণিক মেসেজ বন্ধ করুন" if ch.get("request_msg_enabled", 0) else "▶️ তাৎক্ষণিক মেসেজ চালু করুন"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(m1_toggle_txt, callback_data=f"ch:toggle_msg1:{channel_id}")],
        [InlineKeyboardButton("💬 মেসেজ টেক্সট সেট করুন", callback_data=f"ch:set_msg1_text:{channel_id}")],
        [InlineKeyboardButton("🖼️ মেসেজ ফটো সেট করুন", callback_data=f"ch:set_msg1_photo:{channel_id}")],
        [InlineKeyboardButton("➕ কাস্টম বাটন যোগ করুন", callback_data=f"ch:add_msg1_btn:{channel_id}")],
        [InlineKeyboardButton("🗑️ সব কাস্টম বাটন মুছুন", callback_data=f"ch:clear_msg1_btn:{channel_id}")],
        [InlineKeyboardButton("🔙 ব্যাক", callback_data=f"ch:manage:{channel_id}")]
    ])


def kb_channel_msg2(channel_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 স্বাগতম মেসেজ সেট করুন", callback_data=f"ch:set_msg2_text:{channel_id}")],
        [InlineKeyboardButton("🖼️ স্বাগতম ফটো সেট করুন", callback_data=f"ch:set_msg2_photo:{channel_id}")],
        [InlineKeyboardButton("🔗 ইনভাইট লিংক সেট করুন", callback_data=f"ch:set_link:{channel_id}")],
        [InlineKeyboardButton("➕ কাস্টম বাটন যোগ করুন", callback_data=f"ch:add_msg2_btn:{channel_id}")],
        [InlineKeyboardButton("🗑️ সব কাস্টম বাটন মুছুন", callback_data=f"ch:clear_msg2_btn:{channel_id}")],
        [InlineKeyboardButton("🔙 ব্যাক", callback_data=f"ch:manage:{channel_id}")]
    ])


def kb_broadcast_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 নতুন টেক্সট/মিডিয়া পোস্ট", callback_data="bc:type_post")],
        [InlineKeyboardButton("📊 নতুন পোল/ভোট পোস্ট", callback_data="bc:type_poll")],
        [InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")],
    ])


def kb_broadcast_targets(action_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 সব চ্যানেলে পাঠান", callback_data=f"bc:target_all:{action_type}")],
        [InlineKeyboardButton("📂 ক্যাটাগরি অনুযায়ী পাঠান", callback_data=f"bc:target_cat:{action_type}")],
        [InlineKeyboardButton("🔙 ব্যাক", callback_data="menu:broadcast")],
    ])


def kb_broadcast_categories(action_type: str) -> InlineKeyboardMarkup:
    channels = get_channels()
    categories = set()
    for ch in channels:
        cat = ch.get("category")
        if cat:
            categories.add(cat)
    if not categories:
        categories.add("Uncategorized")
    
    rows = []
    for cat in sorted(categories):
        rows.append([InlineKeyboardButton(f"📂 {cat}", callback_data=f"bc:choose_cat:{action_type}:{cat}")])
    rows.append([InlineKeyboardButton("🔙 ব্যাক", callback_data="menu:broadcast")])
    return InlineKeyboardMarkup(rows)


def kb_broadcast_confirm(action_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ পোস্ট পাঠান", callback_data=f"bc:send:{action_type}")],
        [InlineKeyboardButton("❌ বাতিল", callback_data="menu:broadcast")],
    ])


def kb_stats(channel_id=None) -> InlineKeyboardMarkup:
    rows = []
    if channel_id:
        rows.append([InlineKeyboardButton("🔄 রিফ্রেশ", callback_data=f"stats:channel:{channel_id}")])
        rows.append([InlineKeyboardButton("📤 এক্সপোর্ট CSV", callback_data=f"stats:export:{channel_id}")])
        rows.append([InlineKeyboardButton("🔙 চ্যানেল সেটিংস", callback_data=f"ch:manage:{channel_id}")])
    else:
        rows.append([InlineKeyboardButton("🔄 রিফ্রেশ", callback_data="menu:stats")])
        channels = get_channels()
        for ch in channels[:5]:
            rows.append([InlineKeyboardButton(
                f"📡 {ch['title'][:30]}",
                callback_data=f"stats:channel:{ch['channel_id']}"
            )])
        rows.append([InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def kb_admins_list() -> InlineKeyboardMarkup:
    admins = get_admins()
    rows = []
    for a in admins:
        name = a.get("username") or str(a["user_id"])
        lock = "🔒" if a["user_id"] in ADMIN_IDS else "👤"
        rows.append([
            InlineKeyboardButton(f"{lock} {name}", callback_data="noop"),
            InlineKeyboardButton("❌ সরান", callback_data=f"admin:remove:{a['user_id']}"),
        ])
    rows.append([InlineKeyboardButton("➕ নতুন অ্যাডমিন যোগ করুন", callback_data="admin:add_guide")])
    rows.append([InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")])
    return InlineKeyboardMarkup(rows)


def kb_backup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 ব্যাকআপ ডাউনলোড করুন", callback_data="backup:download")],
        [InlineKeyboardButton("📥 ব্যাকআপ রিস্টোর করুন",  callback_data="backup:restore_guide")],
        [InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")],
    ])


def kb_confirm(action: str, channel_id: int = None) -> InlineKeyboardMarkup:
    yes_data = f"confirm:{action}:{channel_id or 0}"
    back = f"ch:manage:{channel_id}" if channel_id else "menu:main"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ হ্যাঁ, নিশ্চিত", callback_data=yes_data),
         InlineKeyboardButton("❌ বাতিল", callback_data=back)],
    ])


def kb_back_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")]])


# ─── WELCOME MESSAGE FORMATTER ────────────────────────────────────────────────
DEFAULT_WELCOME = (
    "✅ <b>আপনার রিকুয়েস্ট একসেপ্ট করা হয়েছে!</b>\n\n"
    "🎉 আপনাকে স্বাগতম! চ্যানেলটি ঘুরে দেখুন এবং উপভোগ করুন।"
)

DEFAULT_REQUEST_MSG = (
    "⏳ <b>আপনার রিকুয়েস্টটি গ্রহণ করা হয়েছে!</b>\n\n"
    "অনুগ্রহ করে অপেক্ষা করুন, অ্যাডমিন খুব শীঘ্রই আপনার রিকুয়েস্টটি অনুমোদন করবেন।"
)


async def send_request_received_message(bot: Bot, channel: dict, user_id: int):
    """নতুন রিকোয়েস্ট আসার পর তাৎক্ষণিক মেসেজ (Message 1) পাঠান।"""
    msg = channel.get("request_msg") or DEFAULT_REQUEST_MSG
    photo = channel.get("request_photo")
    
    buttons = []
    custom_buttons = channel.get("request_buttons") or []
    if isinstance(custom_buttons, str):
        try:
            custom_buttons = json.loads(custom_buttons)
        except Exception:
            custom_buttons = []
            
    for btn in custom_buttons:
        buttons.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
    kb = InlineKeyboardMarkup(buttons) if buttons else None
    
    try:
        if photo:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    except Exception as e:
        logger.warning(f"Cannot send request received message to {user_id}: {e}")


async def send_welcome(bot: Bot, channel: dict, user_id: int):
    """ইউজার অনুমোদন পাওয়ার পর স্বাগতম মেসেজ (Message 2) পাঠান।"""
    if channel.get("silent_mode"):
        return
    msg = channel.get("welcome_msg") or DEFAULT_WELCOME
    photo = channel.get("welcome_photo")

    buttons = []
    # ১. ডিফল্ট চ্যানেল লিংক বাটন
    link = channel.get("invite_link") or channel.get("username")
    if link:
        if not link.startswith("http"):
            link = f"https://t.me/{link.lstrip('@')}"
        buttons.append([InlineKeyboardButton("📡 চ্যানেলে যান", url=link)])
        
    # ২. কাস্টম বাটনসমূহ
    custom_buttons = channel.get("welcome_buttons") or []
    if isinstance(custom_buttons, str):
        try:
            custom_buttons = json.loads(custom_buttons)
        except Exception:
            custom_buttons = []
            
    for btn in custom_buttons:
        buttons.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        
    kb = InlineKeyboardMarkup(buttons) if buttons else None

    try:
        if photo:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo,
                caption=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
    except Exception as e:
        logger.warning(f"Cannot send welcome to {user_id}: {e}")


# ─── AUTO-ACCEPT CORE ─────────────────────────────────────────────────────────
async def do_accept(bot: Bot, channel_id: int, user_id: int, full_name: str, username: str):
    """একটি join request accept করো।"""
    # ১. প্রথমে welcome message পাঠান (রিকোয়েস্ট পেন্ডিং থাকা অবস্থায়, যাতে চ্যাট ইনিশিয়েট করার পারমিশন পাওয়া যায়)
    ch = get_channel(channel_id)
    if ch:
        try:
            await send_welcome(bot, ch, user_id)
        except Exception as e:
            logger.warning(f"Failed to send welcome message before approval: {e}")

    # ২. তারপর রিকোয়েস্ট অনুমোদন করুন
    try:
        await bot.approve_chat_join_request(chat_id=channel_id, user_id=user_id)
        mark_accepted(channel_id, user_id)
        dequeue(channel_id, user_id)
        logger.info(f"Accepted: user={user_id} ({username}) → channel={channel_id}")
    except BadRequest as e:
        if "USER_ALREADY_PARTICIPANT" in str(e) or "HIDE_REQUESTER_MISSING" in str(e):
            mark_accepted(channel_id, user_id)
            dequeue(channel_id, user_id)
        else:
            logger.error(f"do_accept BadRequest: {e}")
    except TelegramError as e:
        logger.error(f"do_accept TelegramError: {e}")


async def process_due_queue(app: Application):
    """Scheduler দিয়ে due হওয়া pending request গুলো accept করো।"""
    due = get_due_queue()
    if not due:
        return
    logger.info(f"Processing {len(due)} due requests from queue")
    for item in due:
        await do_accept(
            app.bot, item["channel_id"], item["user_id"],
            item["full_name"], item["username"] or ""
        )


# ─── HANDLERS ─────────────────────────────────────────────────────────────────
async def handle_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """নতুন join request এলে এই handler কাজ করবে।"""
    req: ChatJoinRequest = update.chat_join_request
    channel_id = req.chat.id
    user_id = req.from_user.id
    username = req.from_user.username or ""
    full_name = req.from_user.full_name

    ch = get_channel(channel_id)
    if not ch:
        # চ্যানেল registered না, তবুও accept করো (safety)
        logger.warning(f"Unregistered channel {channel_id}, auto-accepting anyway")
        await do_accept(ctx.bot, channel_id, user_id, full_name, username)
        return

    # জয়েন রিকোয়েস্ট পাঠানোর সাথে সাথেই যদি প্রথম তাৎক্ষণিক মেসেজ (Message 1) এনাবল থাকে, তবে তা পাঠান
    if ch.get("request_msg_enabled"):
        await send_request_received_message(ctx.bot, ch, user_id)

    if not ch.get("auto_accept", 1):
        logger.info(f"Auto-accept paused for {channel_id}, skipping user {user_id}")
        return

    delay = ch.get("delay_seconds", 0) or 0

    if delay > 0:
        accept_after = datetime.utcnow() + timedelta(seconds=delay)
        enqueue(channel_id, user_id, username, full_name, accept_after)
        log_request(channel_id, user_id, username, full_name, accept_after)
        logger.info(f"Queued user {user_id} for channel {channel_id}, delay={delay}s")
    else:
        log_request(channel_id, user_id, username, full_name)
        await do_accept(ctx.bot, channel_id, user_id, full_name, username)


# ─── COMMAND HANDLERS ─────────────────────────────────────────────────────────
@admin_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 <b>Auto-Accept Bot — কন্ট্রোল প্যানেল</b>\n\n"
        "নিচের মেনু ব্যবহার করে সব কিছু পরিচালনা করুন।",
        parse_mode=ParseMode.HTML,
        reply_markup=kb_main_menu(),
    )


@admin_only
async def cmd_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


@admin_only
async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    channels = get_channels()
    queue = get_pending_queue()
    admins = get_admins()
    stats = get_stats()
    text = (
        "🏥 <b>হেলথ রিপোর্ট</b>\n\n"
        f"📡 চ্যানেল: <b>{len(channels)}</b>\n"
        f"👮 অ্যাডমিন: <b>{len(admins)}</b>\n"
        f"⏳ পেন্ডিং কিউ: <b>{len(queue)}</b>\n"
        f"📊 মোট রিকুয়েস্ট: <b>{stats['total']}</b>\n"
        f"✅ একসেপ্টেড: <b>{stats['accepted']}</b>\n"
        f"🗓️ আজ: <b>{stats['today']}</b>\n"
        f"📅 এই সপ্তাহ: <b>{stats['weekly']}</b>\n"
        f"🗃️ DB আকার: <b>{Path(DB_PATH).stat().st_size // 1024} KB</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_back_main())


# ─── CALLBACK QUERY HANDLER ───────────────────────────────────────────────────

# State management for multi-step input (in-memory, per-user)
USER_STATES: dict[int, dict] = {}


@admin_only
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # ── NOOP ──
    if data == "noop":
        return

    # ── MAIN MENU ──
    if data == "menu:main":
        await q.edit_message_text(
            "🤖 <b>Auto-Accept Bot — কন্ট্রোল প্যানেল</b>\n\nমেনু থেকে অপশন বেছে নিন:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_main_menu(),
        )
        return

    # ── BROADCAST ──
    if data == "menu:broadcast":
        await q.edit_message_text(
            "📢 <b>ব্রডকাস্ট সেন্টার</b>\n\n"
            "এখানে আপনি সমস্ত চ্যানেলে অথবা ক্যাটাগরি অনুযায়ী পোস্ট ও পোল ব্রডকাস্ট করতে পারবেন।\n"
            "একটি পোস্টের ধরন সিলেক্ট করুন:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_broadcast_main(),
        )
        return

    if data.startswith("bc:type_"):
        action_type = data.split("_")[-1]  # "post" or "poll"
        await q.edit_message_text(
            "🎯 <b>টার্গেট চ্যানেল সিলেক্ট করুন</b>\n\n"
            "আপনি কোথায় ব্রডকাস্ট করতে চান?",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_broadcast_targets(action_type)
        )
        return

    if data.startswith("bc:target_"):
        parts = data.split(":")
        target_mode = parts[0].split("_")[-1]  # "all" or "cat"
        action_type = parts[1]  # "post" or "poll"
        
        if target_mode == "all":
            USER_STATES[uid] = {"action": f"{action_type}_wait_content" if action_type == "post" else "poll_wait_question", "target_type": "all"}
            prompt = (
                "📝 **পোস্টের কনটেন্ট পাঠান**\n\nআপনি একটি টেক্সট মেসেজ বা ছবি (ক্যাপশন সহ) পাঠাতে পারেন।"
                if action_type == "post" else
                "📊 **পোলের প্রশ্নটি পাঠান**\n\nযেমন: <code>আজকের খেলা কেমন লাগলো?</code>"
            )
            await q.edit_message_text(
                prompt,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:broadcast")]])
            )
        else:
            await q.edit_message_text(
                "📂 **একটি ক্যাটাগরি নির্বাচন করুন**\n\nকোন ক্যাটাগরির চ্যানেলে ব্রডকাস্ট করতে চান:",
                reply_markup=kb_broadcast_categories(action_type)
            )
        return

    if data.startswith("bc:choose_cat:"):
        parts = data.split(":")
        action_type = parts[2]
        cat_name = parts[3]
        USER_STATES[uid] = {
            "action": f"{action_type}_wait_content" if action_type == "post" else "poll_wait_question",
            "target_type": "cat",
            "target_cat": cat_name
        }
        prompt = (
            f"📝 **পোস্টের কনটেন্ট পাঠান (ক্যাটাগরি: {cat_name})**\n\nআপনি একটি টেক্সট মেসেজ বা ছবি (ক্যাপশন সহ) পাঠাতে পারেন।"
            if action_type == "post" else
            f"📊 **পোলের প্রশ্নটি পাঠান (ক্যাটাগরি: {cat_name})**\n\nযেমন: <code>আজকের খেলা কেমন লাগলো?</code>"
        )
        await q.edit_message_text(
            prompt,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:broadcast")]])
        )
        return

    if data.startswith("bc:send:"):
        action_type = data.split(":")[-1]
        state = USER_STATES.get(uid)
        if not state:
            await q.edit_message_text("❌ কোনো ব্রডকাস্ট প্রসেস চলমান নেই।", reply_markup=kb_back_main())
            return
            
        target_type = state.get("target_type")
        target_cat = state.get("target_cat")
        
        # Get target channels
        all_ch = get_channels()
        targets = []
        if target_type == "all":
            targets = all_ch
        elif target_type == "cat":
            targets = [c for c in all_ch if c.get("category") == target_cat]
            
        if not targets:
            await q.edit_message_text("❌ কোনো টার্গেট চ্যানেল পাওয়া যায়নি।", reply_markup=kb_back_main())
            USER_STATES.pop(uid, None)
            return
            
        await q.edit_message_text(f"⏳ ব্রডকাস্ট পাঠানো হচ্ছে... (টার্গেট: {len(targets)} টি চ্যানেল)", reply_markup=None)
        
        success = 0
        failed = 0
        
        if action_type == "post":
            # Build keyboard markup
            btns = state.get("buttons") or []
            kb_list = []
            for btn in btns:
                kb_list.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
            kb = InlineKeyboardMarkup(kb_list) if kb_list else None
            
            for ch in targets:
                try:
                    if state.get("media_type") == "photo":
                        await ctx.bot.send_photo(
                            chat_id=ch["channel_id"],
                            photo=state["media_id"],
                            caption=state["text"],
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb
                        )
                    else:
                        await ctx.bot.send_message(
                            chat_id=ch["channel_id"],
                            text=state["text"],
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb
                        )
                    success += 1
                except Exception as e:
                    logger.error(f"Broadcast failed for channel {ch['channel_id']}: {e}")
                    failed += 1
                    
        elif action_type == "poll":
            question = state.get("question")
            options = state.get("options") or []
            for ch in targets:
                try:
                    await ctx.bot.send_poll(
                        chat_id=ch["channel_id"],
                        question=question,
                        options=options,
                        is_anonymous=True
                    )
                    success += 1
                except Exception as e:
                    logger.error(f"Broadcast poll failed for channel {ch['channel_id']}: {e}")
                    failed += 1
                    
        USER_STATES.pop(uid, None)
        await ctx.bot.send_message(
            chat_id=uid,
            text=f"📢 **ব্রডকাস্ট সম্পন্ন হয়েছে!**\n\n✅ সফল: {success} টি চ্যানেল\n❌ ব্যর্থ: {failed} টি চ্যানেল",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_back_main()
        )
        return

    if data.startswith("ch:set_cat:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        curr_cat = ch.get("category") or "Uncategorized"
        USER_STATES[uid] = {"action": "set_category", "channel_id": cid}
        await q.edit_message_text(
            f"📂 <b>চ্যানেলের ক্যাটাগরি সেট করুন</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"📂 বর্তমান ক্যাটাগরি: <code>{curr_cat}</code>\n\n"
            f"এই চ্যানেলের জন্য একটি নতুন ক্যাটাগরির নাম লিখে পাঠান। (যেমন: Movies, Sports, News)\n"
            f"ডিফল্ট করতে: <code>Uncategorized</code> লিখে পাঠান।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:manage:{cid}")]])
        )
        return

    # ── CHANNELS ──
    if data.startswith("ch:list:"):
        page = int(data.split(":")[-1])
        await q.edit_message_text(
            "📡 <b>চ্যানেল ম্যানেজমেন্ট</b>\n\nআপনার চ্যানেলগুলো:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_channels_list(page),
        )
        return

    if data == "menu:channels":
        await q.edit_message_text(
            "📡 <b>চ্যানেল ম্যানেজমেন্ট</b>\n\nআপনার চ্যানেলগুলো:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_channels_list(0),
        )
        return

    if data.startswith("ch:manage:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if not ch:
            await q.edit_message_text("❌ চ্যানেল পাওয়া যায়নি।", reply_markup=kb_back_main())
            return
        text = (
            f"📡 <b>{ch['title']}</b>\n\n"
            f"🆔 ID: <code>{cid}</code>\n"
            f"⚡ এখানে আপনার চ্যানেলের সমস্ত অটো-একসেপ্ট এবং স্বাগতম বার্তা বাটন সেটিংস নিয়ন্ত্রণ করুন।"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_manage(cid))
        return

    if data.startswith("ch:menu_aa:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if not ch:
            await q.edit_message_text("❌ চ্যানেল পাওয়া যায়নি।", reply_markup=kb_back_main())
            return
        status = "✅ চালু" if ch.get("auto_accept", 1) else "⏸️ বন্ধ"
        delay_m = ch.get("delay_seconds", 0) // 60
        silent_status = "🔇 হ্যাঁ" if ch.get("silent_mode", 0) else "🔔 না"
        text = (
            f"⚡ <b>অটো-একসেপ্ট সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"⚡ অটো-একসেপ্ট: {status}\n"
            f"⏱️ ডিলে: {delay_m} মিনিট\n"
            f"🔇 সাইলেন্ট মোড: {silent_status}\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_aa(cid))
        return

    if data.startswith("ch:menu_msg1:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if not ch:
            await q.edit_message_text("❌ চ্যানেল পাওয়া যায়নি।", reply_markup=kb_back_main())
            return
        status = "🟢 সচল" if ch.get("request_msg_enabled", 0) else "🔴 অচল"
        msg_text = ch.get("request_msg") or DEFAULT_REQUEST_MSG
        photo_status = "🖼️ ফটো সেট করা আছে" if ch.get("request_photo") else "❌ ফটো সেট করা নেই"
        
        btns = ch.get("request_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btn_count = len(btns)
        
        text = (
            f"💬 <b>তাৎক্ষণিক মেসেজ (Message 1) সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"📊 অবস্থা: {status}\n"
            f"📷 মিডিয়া: {photo_status}\n"
            f"🔗 কাস্টম বাটন সংখ্যা: {btn_count} টি\n\n"
            f"💬 <b>মেসেজ প্রিভিউ:</b>\n{msg_text}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_msg1(cid))
        return

    if data.startswith("ch:menu_msg2:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if not ch:
            await q.edit_message_text("❌ চ্যানেল পাওয়া যায়নি।", reply_markup=kb_back_main())
            return
        msg_text = ch.get("welcome_msg") or DEFAULT_WELCOME
        photo_status = "🖼️ ফটো সেট করা আছে" if ch.get("welcome_photo") else "❌ ফটো সেট করা নেই"
        link = ch.get("invite_link") or "সেট করা নেই"
        
        btns = ch.get("welcome_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btn_count = len(btns)
        
        text = (
            f"🎉 <b>অনুমোদন মেসেজ (Message 2) সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"🔗 ইনভাইট লিংক: {link}\n"
            f"📷 মিডিয়া: {photo_status}\n"
            f"🔗 কাস্টম বাটন সংখ্যা: {btn_count} টি\n\n"
            f"💬 <b>মেসেজ প্রিভিউ:</b>\n{msg_text}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_msg2(cid))
        return

    if data == "ch:add_guide":
        USER_STATES[uid] = {"action": "add_channel"}
        await q.edit_message_text(
            "➕ <b>নতুন চ্যানেল যোগ করুন</b>\n\n"
            "Bot-কে আপনার চ্যানেল/গ্রুপের <b>admin</b> করুন, তারপর এই চ্যানেলের ID পাঠান।\n\n"
            "উদাহরণ: <code>-1001234567890</code>\n\n"
            "📌 <i>চ্যানেল ID পেতে @userinfobot ব্যবহার করুন।</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:channels")]]),
        )
        return

    if data.startswith("ch:toggle_aa:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if ch:
            new_val = 0 if ch.get("auto_accept", 1) else 1
            update_channel_setting(cid, "auto_accept", new_val)
            status = "✅ চালু করা হয়েছে" if new_val else "⏸️ বন্ধ করা হয়েছে"
            await q.answer(f"অটো-একসেপ্ট {status}", show_alert=True)
        
        # Render sub-menu directly
        ch = get_channel(cid)
        status = "✅ চালু" if ch.get("auto_accept", 1) else "⏸️ বন্ধ"
        delay_m = ch.get("delay_seconds", 0) // 60
        silent_status = "🔇 হ্যাঁ" if ch.get("silent_mode", 0) else "🔔 না"
        text = (
            f"⚡ <b>অটো-একসেপ্ট সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"⚡ অটো-একসেপ্ট: {status}\n"
            f"⏱️ ডিলে: {delay_m} মিনিট\n"
            f"🔇 সাইলেন্ট মোড: {silent_status}\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_aa(cid))
        return

    if data.startswith("ch:toggle_silent:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if ch:
            new_val = 0 if ch.get("silent_mode", 0) else 1
            update_channel_setting(cid, "silent_mode", new_val)
            status = "🔇 চালু" if new_val else "🔔 বন্ধ"
            await q.answer(f"সাইলেন্ট মোড {status}", show_alert=True)
            
        # Render sub-menu directly
        ch = get_channel(cid)
        status = "✅ চালু" if ch.get("auto_accept", 1) else "⏸️ বন্ধ"
        delay_m = ch.get("delay_seconds", 0) // 60
        silent_status = "🔇 হ্যাঁ" if ch.get("silent_mode", 0) else "🔔 না"
        text = (
            f"⚡ <b>অটো-একসেপ্ট সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"⚡ অটো-একসেপ্ট: {status}\n"
            f"⏱️ ডিলে: {delay_m} মিনিট\n"
            f"🔇 সাইলেন্ট মোড: {silent_status}\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_aa(cid))
        return

    if data.startswith("ch:toggle_msg1:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if ch:
            new_val = 0 if ch.get("request_msg_enabled", 0) else 1
            update_channel_setting(cid, "request_msg_enabled", new_val)
            status = "🟢 চালু করা হয়েছে" if new_val else "🔴 বন্ধ করা হয়েছে"
            await q.answer(f"তাৎক্ষণিক মেসেজ ১ {status}", show_alert=True)
            
        # Render sub-menu directly
        ch = get_channel(cid)
        status = "🟢 সচল" if ch.get("request_msg_enabled", 0) else "🔴 অচল"
        msg_text = ch.get("request_msg") or DEFAULT_REQUEST_MSG
        photo_status = "🖼️ ফটো সেট করা আছে" if ch.get("request_photo") else "❌ ফটো সেট করা নেই"
        
        btns = ch.get("request_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btn_count = len(btns)
        
        text = (
            f"💬 <b>তাৎক্ষণিক মেসেজ (Message 1) সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"📊 অবস্থা: {status}\n"
            f"📷 মিডিয়া: {photo_status}\n"
            f"🔗 কাস্টম বাটন সংখ্যা: {btn_count} টি\n\n"
            f"💬 <b>মেসেজ প্রিভিউ:</b>\n{msg_text}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_msg1(cid))
        return

    if data.startswith("ch:set_delay:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_delay", "channel_id": cid}
        await q.edit_message_text(
            "⏱️ <b>ডিলে সেট করুন</b>\n\n"
            "মিনিটে সংখ্যা পাঠান (0 = তাৎক্ষণিক একসেপ্ট)\n"
            "উদাহরণ: <code>5</code> (৫ মিনিট পরে একসেপ্ট হবে)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_aa:{cid}")]])
        )
        return

    if data.startswith("ch:set_msg1_text:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_msg1_text", "channel_id": cid}
        await q.edit_message_text(
            "💬 <b>তাৎক্ষণিক মেসেজ ১ সেট করুন</b>\n\n"
            "HTML formatting সাপোর্টেড (<b>bold</b>, <i>italic</i>, <code>code</code>)\n\n"
            "ডিফল্টে ফিরতে: <code>reset</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    if data.startswith("ch:set_msg1_photo:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_msg1_photo", "channel_id": cid}
        await q.edit_message_text(
            "🖼️ <b>তাৎক্ষণিক মেসেজ ১ এর ফটো সেট করুন</b>\n\n"
            "একটি ছবি পাঠান। এই ছবিটি তাৎক্ষণিক মেসেজের সাথে দেখাবে।\n\n"
            "ফটো সরাতে: <code>remove</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    if data.startswith("ch:add_msg1_btn:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "add_msg1_btn_text", "channel_id": cid}
        await q.edit_message_text(
            "💬 <b>তাৎক্ষণিক মেসেজ ১ এ কাস্টম বাটন যোগ করুন</b>\n\n"
            "বাটনটিতে যে লেখা দেখাতে চান তা লিখে পাঠান।\n\n"
            "উদাহরণ: <code>📡 জয়েন করুন</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    if data.startswith("ch:clear_msg1_btn:"):
        cid = int(data.split(":")[-1])
        update_channel_setting(cid, "request_buttons", [])
        await q.answer("🗑️ তাৎক্ষণিক মেসেজ ১ এর সব কাস্টম বাটন মুছে ফেলা হয়েছে", show_alert=True)
        
        # Render sub-menu directly
        ch = get_channel(cid)
        status = "🟢 সচল" if ch.get("request_msg_enabled", 0) else "🔴 অচল"
        msg_text = ch.get("request_msg") or DEFAULT_REQUEST_MSG
        photo_status = "🖼️ ফটো সেট করা আছে" if ch.get("request_photo") else "❌ ফটো সেট করা নেই"
        
        btns = ch.get("request_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btn_count = len(btns)
        
        text = (
            f"💬 <b>তাৎক্ষণিক মেসেজ (Message 1) সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"📊 অবস্থা: {status}\n"
            f"📷 মিডিয়া: {photo_status}\n"
            f"🔗 কাস্টম বাটন সংখ্যা: {btn_count} টি\n\n"
            f"💬 <b>মেসেজ প্রিভিউ:</b>\n{msg_text}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_msg1(cid))
        return

    if data.startswith("ch:set_msg2_text:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_msg2_text", "channel_id": cid}
        await q.edit_message_text(
            "💬 <b>স্বাগতম মেসেজ ২ সেট করুন</b>\n\n"
            "HTML formatting সাপোর্টেড (<b>bold</b>, <i>italic</i>, <code>code</code>)\n\n"
            "ডিফল্টে ফিরতে: <code>reset</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    if data.startswith("ch:set_msg2_photo:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_msg2_photo", "channel_id": cid}
        await q.edit_message_text(
            "🖼️ <b>স্বাগতম মেসেজ ২ এর ফটো সেট করুন</b>\n\n"
            "একটি ছবি পাঠান। এই ছবিটি স্বাগতম মেসেজের সাথে দেখাবে।\n\n"
            "ফটো সরাতে: <code>remove</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    if data.startswith("ch:add_msg2_btn:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "add_msg2_btn_text", "channel_id": cid}
        await q.edit_message_text(
            "💬 <b>স্বাগতম মেসেজ ২ এ কাস্টম বাটন যোগ করুন</b>\n\n"
            "বাটনটিতে যে লেখা দেখাতে চান তা লিখে পাঠান।\n\n"
            "উদাহরণ: <code>📡 সাপোর্ট গ্রুপ</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    if data.startswith("ch:clear_msg2_btn:"):
        cid = int(data.split(":")[-1])
        update_channel_setting(cid, "welcome_buttons", [])
        await q.answer("🗑️ স্বাগতম মেসেজ ২ এর সব কাস্টম বাটন মুছে ফেলা হয়েছে", show_alert=True)
        
        # Render sub-menu directly
        ch = get_channel(cid)
        msg_text = ch.get("welcome_msg") or DEFAULT_WELCOME
        photo_status = "🖼️ ফটো সেট করা আছে" if ch.get("welcome_photo") else "❌ ফটো সেট করা নেই"
        link = ch.get("invite_link") or "সেট করা নেই"
        
        btns = ch.get("welcome_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btn_count = len(btns)
        
        text = (
            f"🎉 <b>অনুমোদন মেসেজ (Message 2) সেটিংস</b>\n\n"
            f"📡 চ্যানেল: <b>{ch['title']}</b>\n"
            f"🔗 ইনভাইট লিংক: {link}\n"
            f"📷 মিডিয়া: {photo_status}\n"
            f"🔗 কাস্টম বাটন সংখ্যা: {btn_count} টি\n\n"
            f"💬 <b>মেসেজ প্রিভিউ:</b>\n{msg_text}"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_msg2(cid))
        return

    if data.startswith("ch:set_link:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_link", "channel_id": cid}
        await q.edit_message_text(
            "🔗 <b>ইনভাইট লিংক সেট করুন</b>\n\n"
            "লিংক পাঠান (যেমন: <code>https://t.me/+xxxxx</code> বা <code>@username</code>)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    if data.startswith("ch:remove:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        title = ch["title"] if ch else str(cid)
        await q.edit_message_text(
            f"⚠️ আপনি কি নিশ্চিতভাবে <b>{title}</b> চ্যানেলটি সরাতে চান?\n\n"
            "এই চ্যানেলের সব পেন্ডিং কিউও মুছে যাবে।",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_confirm("remove_channel", cid),
        )
        return

    if data.startswith("confirm:remove_channel:"):
        cid = int(data.split(":")[-1])
        remove_channel(cid)
        await q.edit_message_text(
            "✅ চ্যানেল সফলভাবে সরানো হয়েছে।",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 চ্যানেল লিস্ট", callback_data="ch:list:0")]]),
        )
        return

    # ── STATS ──
    if data == "menu:stats":
        s = get_stats()
        text = (
            "📊 <b>সামগ্রিক স্ট্যাটিস্টিক্স</b>\n\n"
            f"📩 মোট রিকুয়েস্ট: <b>{s['total']}</b>\n"
            f"✅ একসেপ্টেড: <b>{s['accepted']}</b>\n"
            f"⏳ পেন্ডিং: <b>{s['pending']}</b>\n"
            f"─────────────────\n"
            f"🗓️ আজ: <b>{s['today']}</b>\n"
            f"📅 এই সপ্তাহ: <b>{s['weekly']}</b>\n"
            f"📆 এই মাস: <b>{s['monthly']}</b>\n\n"
            "নিচে নির্দিষ্ট চ্যানেলের স্ট্যাটস দেখুন:"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_stats())
        return

    if data.startswith("stats:channel:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        s = get_stats(cid)
        title = ch["title"] if ch else str(cid)
        text = (
            f"📊 <b>{title}</b> — স্ট্যাটস\n\n"
            f"📩 মোট রিকুয়েস্ট: <b>{s['total']}</b>\n"
            f"✅ একসেপ্টেড: <b>{s['accepted']}</b>\n"
            f"⏳ পেন্ডিং: <b>{s['pending']}</b>\n"
            f"─────────────────\n"
            f"🗓️ আজ: <b>{s['today']}</b>\n"
            f"📅 এই সপ্তাহ: <b>{s['weekly']}</b>\n"
            f"📆 এই মাস: <b>{s['monthly']}</b>"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_stats(cid))
        return

    if data.startswith("stats:export:"):
        cid = int(data.split(":")[-1])
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM join_requests WHERE channel_id=? ORDER BY requested_at DESC",
                (cid,),
            ).fetchall()
        lines = ["user_id,username,full_name,requested_at,accepted_at,status"]
        for r in rows:
            lines.append(f"{r['user_id']},{r['username'] or ''},{r['full_name'] or ''},"
                         f"{r['requested_at']},{r['accepted_at'] or ''},{r['status']}")
        csv_data = "\n".join(lines).encode("utf-8")
        bio = io.BytesIO(csv_data)
        bio.name = f"stats_{cid}.csv"
        await ctx.bot.send_document(chat_id=uid, document=bio, caption=f"📤 চ্যানেল {cid} এর স্ট্যাটস এক্সপোর্ট")
        await q.answer("✅ CSV পাঠানো হয়েছে")
        return

    # ── QUEUE ──
    if data == "menu:queue":
        queue = get_pending_queue()
        if not queue:
            text = "⏳ <b>পেন্ডিং কিউ</b>\n\nকোনো পেন্ডিং রিকুয়েস্ট নেই।"
        else:
            lines = [f"⏳ <b>পেন্ডিং কিউ ({len(queue)}টি)</b>\n"]
            for item in queue[:20]:
                dt = item["accept_after"].replace("T", " ")[:16]
                name = item["full_name"] or item["username"] or str(item["user_id"])
                lines.append(f"• {name} → ⏰ {dt}")
            if len(queue) > 20:
                lines.append(f"\n...এবং আরো {len(queue)-20}টি")
            text = "\n".join(lines)
        rows = []
        for ch in get_channels()[:5]:
            rows.append([InlineKeyboardButton(
                f"📡 {ch['title'][:30]}",
                callback_data=f"queue:channel:{ch['channel_id']}"
            )])
        rows.append([InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows))
        return

    if data.startswith("queue:channel:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        queue = get_pending_queue(cid)
        title = ch["title"] if ch else str(cid)
        if not queue:
            text = f"⏳ <b>{title}</b>\n\nকোনো পেন্ডিং কিউ নেই।"
        else:
            lines = [f"⏳ <b>{title}</b> — পেন্ডিং ({len(queue)}টি)\n"]
            for item in queue[:20]:
                dt = item["accept_after"].replace("T", " ")[:16]
                name = item["full_name"] or item["username"] or str(item["user_id"])
                lines.append(f"• {name} → {dt}")
            text = "\n".join(lines)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 কিউ", callback_data="menu:queue")]])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return

    # ── ADMINS ──
    if data == "menu:admins":
        await q.edit_message_text(
            "👮 <b>অ্যাডমিন ম্যানেজমেন্ট</b>\n\nবর্তমান অ্যাডমিনগুলো:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admins_list(),
        )
        return

    if data == "admin:add_guide":
        USER_STATES[uid] = {"action": "add_admin"}
        await q.edit_message_text(
            "➕ <b>নতুন অ্যাডমিন যোগ করুন</b>\n\n"
            "নতুন অ্যাডমিনের Telegram User ID পাঠান।\n"
            "উদাহরণ: <code>123456789</code>\n\n"
            "ID পেতে ওই ব্যক্তিকে @userinfobot তে message করতে বলুন।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:admins")]]),
        )
        return

    if data.startswith("admin:remove:"):
        target_id = int(data.split(":")[-1])
        if target_id in ADMIN_IDS:
            await q.answer("⛔ super-admin সরানো যাবে না!", show_alert=True)
            return
        remove_admin(target_id)
        await q.answer("✅ অ্যাডমিন সরানো হয়েছে", show_alert=True)
        await q.edit_message_text(
            "👮 <b>অ্যাডমিন ম্যানেজমেন্ট</b>\n\nবর্তমান অ্যাডমিনগুলো:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_admins_list(),
        )
        return

    # ── SETTINGS ──
    if data == "menu:settings":
        await q.edit_message_text(
            "⚙️ <b>সেটিংস</b>\n\nনির্দিষ্ট চ্যানেল সেটিংসের জন্য চ্যানেল ম্যানেজমেন্টে যান।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📡 চ্যানেল সেটিংস", callback_data="menu:channels")],
                [InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")],
            ]),
        )
        return

    # ── BACKUP ──
    if data == "menu:backup":
        await q.edit_message_text(
            "💾 <b>ব্যাকআপ ও রিস্টোর</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_backup(),
        )
        return

    if data == "backup:download":
        await q.answer("⏳ ব্যাকআপ তৈরি হচ্ছে...")
        path = make_backup()
        with open(path, "rb") as f:
            await ctx.bot.send_document(
                chat_id=uid,
                document=f,
                filename=os.path.basename(path),
                caption=f"💾 ব্যাকআপ তৈরি হয়েছে\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            )
        return

    if data == "backup:restore_guide":
        USER_STATES[uid] = {"action": "restore_backup"}
        await q.edit_message_text(
            "📥 <b>ব্যাকআপ রিস্টোর</b>\n\n"
            "⚠️ <b>সতর্কতা:</b> এটি বর্তমান সব ডেটা মুছে নতুন ডেটা দিয়ে প্রতিস্থাপন করবে!\n\n"
            "রিস্টোর করতে .db ব্যাকআপ ফাইলটি পাঠান।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:backup")]]),
        )
        return

    # ── HEALTH ──
    if data == "menu:health":
        channels = get_channels()
        queue = get_pending_queue()
        admins = get_admins()
        s = get_stats()
        text = (
            "🏥 <b>হেলথ রিপোর্ট</b>\n\n"
            f"📡 রেজিস্টার্ড চ্যানেল: <b>{len(channels)}</b>\n"
            f"👮 অ্যাডমিন সংখ্যা: <b>{len(admins)}</b>\n"
            f"⏳ পেন্ডিং কিউ: <b>{len(queue)}</b>\n"
            f"📊 মোট রিকুয়েস্ট: <b>{s['total']}</b>\n"
            f"✅ একসেপ্টেড: <b>{s['accepted']}</b>\n"
            f"🗃️ DB আকার: <b>{Path(DB_PATH).stat().st_size // 1024} KB</b>\n"
            f"🕐 সময়: <b>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</b>"
        )
        await q.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 রিফ্রেশ", callback_data="menu:health"),
                                               InlineKeyboardButton("🔙 মেনু", callback_data="menu:main")]]),
        )
        return


# ─── TEXT/DOCUMENT MESSAGE HANDLER (for multi-step state) ────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg: Message = update.message
    if not msg:
        return
    uid = msg.from_user.id
    if not is_admin(uid):
        return

    state = USER_STATES.get(uid)
    if not state:
        return

    action = state.get("action")

    # ── Set category ──
    if action == "set_category":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if not text:
            await msg.reply_text("❌ সঠিক ক্যাটাগরি নাম দিন।")
            return
        update_channel_setting(cid, "category", text)
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            f"✅ ক্যাটাগরি সফলভাবে সেট করা হয়েছে: <b>{text}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ চ্যানেল সেটিংস", callback_data=f"ch:manage:{cid}")]])
        )
        return

    # ── Post Broadcast Wait Content ──
    if action == "post_wait_content":
        media_id = None
        media_type = None
        text = ""
        
        if msg.photo:
            media_id = msg.photo[-1].file_id
            media_type = "photo"
            text = msg.caption or ""
        elif msg.text:
            text = msg.text
        else:
            await msg.reply_text("❌ দয়া করে শুধুমাত্র টেক্সট অথবা একটি ইমেজ পাঠান।")
            return
            
        state["media_id"] = media_id
        state["media_type"] = media_type
        state["text"] = text
        state["action"] = "post_wait_buttons"
        
        await msg.reply_text(
            "🔗 **বাটনসমূহ যোগ করুন (ঐচ্ছিক)**\n\n"
            "বাটন যোগ করতে নিচের ফরম্যাটে পাঠান (প্রতি লাইনে একটি বাটন):\n"
            "<code>বাটনের নাম | লিঙ্ক</code>\n\n"
            "যেমন:\n"
            "<code>📡 জয়েন চ্যানেল | https://t.me/mychannel\n"
            "💬 সাপোর্ট গ্রুপ | https://t.me/support</code>\n\n"
            "বাটন ছাড়া পোস্ট করতে <code>skip</code> লিখে পাঠান।",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:broadcast")]])
        )
        return

    # ── Post Broadcast Wait Buttons ──
    if action == "post_wait_buttons":
        text_input = msg.text.strip() if msg.text else ""
        btns = []
        
        if text_input.lower() != "skip":
            lines = text_input.split("\n")
            for line in lines:
                if "|" in line:
                    parts = line.split("|")
                    btn_text = parts[0].strip()
                    btn_url = parts[1].strip()
                    if btn_url.startswith("http://") or btn_url.startswith("https://") or btn_url.startswith("t.me/"):
                        btns.append({"text": btn_text, "url": btn_url})
            
            if not btns and text_input.lower() != "skip":
                await msg.reply_text(
                    "❌ কোনো বাটন পার্স করা যায়নি বা লিঙ্কের ফরম্যাট ভুল ছিল।\n"
                    "আবার সঠিক ফরম্যাটে বাটন পাঠান অথবা <code>skip</code> লিখুন।",
                    parse_mode=ParseMode.HTML
                )
                return
                
        state["buttons"] = btns
        state["action"] = "post_confirm"
        
        # Build preview inline markup
        preview_btns = []
        for btn in btns:
            preview_btns.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        kb = InlineKeyboardMarkup(preview_btns) if preview_btns else None
        
        # Show post preview
        if state.get("media_type") == "photo":
            await msg.reply_photo(
                photo=state["media_id"],
                caption=f"📝 **পোস্টের প্রিভিউ:**\n\n{state['text']}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
        else:
            await msg.reply_text(
                text=f"📝 **পোস্টের প্রিভিউ:**\n\n{state['text']}",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )
            
        target_info = "সব চ্যানেল" if state.get("target_type") == "all" else f"ক্যাটাগরি: {state.get('target_cat')}"
        await msg.reply_text(
            f"⚠️ **টার্গেট: {target_info}**\n\nআপনি কি নিশ্চিতভাবে এই ব্রডকাস্ট পোস্টটি পাঠাতে চান?",
            reply_markup=kb_broadcast_confirm("post")
        )
        return

    # ── Poll Broadcast Wait Question ──
    if action == "poll_wait_question":
        question = msg.text.strip() if msg.text else ""
        if not question:
            await msg.reply_text("❌ পোলের প্রশ্নটি অবশ্যই সঠিক টেক্সট হতে হবে। আবার পাঠান।")
            return
            
        state["question"] = question
        state["action"] = "poll_wait_options"
        
        await msg.reply_text(
            "📝 **পোলের অপশনগুলো পাঠান**\n\n"
            "প্রতি লাইনে একটি করে অপশন লিখুন। সর্বনিম্ন ২টি এবং সর্বোচ্চ ১০টি অপশন দেওয়া যাবে।\n\n"
            "যেমন:\n"
            "<code>অসাধারণ\n"
            "ভালো\n"
            "মোটামুটি\n"
            "খারাপ</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data="menu:broadcast")]])
        )
        return

    # ── Poll Broadcast Wait Options ──
    if action == "poll_wait_options":
        text_input = msg.text.strip() if msg.text else ""
        options = [line.strip() for line in text_input.split("\n") if line.strip()]
        
        if len(options) < 2 or len(options) > 10:
            await msg.reply_text("❌ অপশন সংখ্যা অবশ্যই ২ থেকে ১০ এর মধ্যে হতে হবে। দয়া করে আবার পাঠান।")
            return
            
        state["options"] = options
        state["action"] = "poll_confirm"
        
        opt_text = "\n".join([f"🔹 {opt}" for opt in options])
        target_info = "সব চ্যানেল" if state.get("target_type") == "all" else f"ক্যাটাগরি: {state.get('target_cat')}"
        
        await msg.reply_text(
            f"📊 **পোলের প্রিভিউ:**\n\n"
            f"❓ প্রশ্ন: <b>{state['question']}</b>\n\n"
            f"📝 অপশনসমূহ:\n{opt_text}\n\n"
            f"🎯 টার্গেট: <b>{target_info}</b>",
            parse_mode=ParseMode.HTML
        )
        await msg.reply_text(
            "⚠️ আপনি কি নিশ্চিতভাবে এই পোল পোস্টটি পাঠাতে চান?",
            reply_markup=kb_broadcast_confirm("poll")
        )
        return

    # ── Add channel ──
    if action == "add_channel":
        text = msg.text.strip() if msg.text else ""
        try:
            cid = int(text)
            chat: Chat = await ctx.bot.get_chat(cid)
            invite = chat.invite_link or ""
            if not invite:
                try:
                    invite = (await ctx.bot.export_chat_invite_link(cid)) or ""
                except Exception:
                    pass
            upsert_channel(cid, chat.title or str(cid), chat.username or "", invite, uid)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                f"✅ <b>{chat.title}</b> চ্যানেল সফলভাবে যোগ করা হয়েছে!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⚙️ সেটিংস দেখুন", callback_data=f"ch:manage:{cid}"),
                    InlineKeyboardButton("📡 চ্যানেল লিস্ট", callback_data="ch:list:0"),
                ]]),
            )
        except ValueError:
            await msg.reply_text("❌ সঠিক Channel ID দিন। উদাহরণ: <code>-1001234567890</code>",
                                 parse_mode=ParseMode.HTML)
        except Exception as e:
            await msg.reply_text(f"❌ ত্রুটি: {e}\n\nBot-কে চ্যানেলের admin করুন।")
        return

    # ── Set delay ──
    if action == "set_delay":
        cid = state.get("channel_id")
        try:
            minutes = int(msg.text.strip())
            if minutes < 0:
                raise ValueError
            update_channel_setting(cid, "delay_seconds", minutes * 60)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                f"✅ ডিলে সেট হয়েছে: <b>{minutes} মিনিট</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ অটো-একসেপ্ট সেটিংস", callback_data=f"ch:menu_aa:{cid}")]]),
            )
        except (ValueError, TypeError):
            await msg.reply_text("❌ সঠিক সংখ্যা দিন (মিনিটে)। উদাহরণ: <code>5</code>", parse_mode=ParseMode.HTML)
        return

    # ── Set Message 1 text ──
    if action == "set_msg1_text":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if text.lower() == "reset":
            update_channel_setting(cid, "request_msg", None)
            resp = "✅ তাৎক্ষণিক মেসেজ ১ ডিফল্টে রিসেট করা হয়েছে।"
        else:
            update_channel_setting(cid, "request_msg", text)
            resp = "✅ তাৎক্ষণিক মেসেজ ১ আপডেট হয়েছে!"
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            resp,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ তাৎক্ষণিক মেসেজ সেটিংস", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    # ── Set Message 1 photo ──
    if action == "set_msg1_photo":
        cid = state.get("channel_id")
        if msg.text and msg.text.strip().lower() == "remove":
            update_channel_setting(cid, "request_photo", None)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ তাৎক্ষণিক মেসেজ ১ এর ফটো সরানো হয়েছে।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️  তাৎক্ষণিক মেসেজ সেটিংস", callback_data=f"ch:menu_msg1:{cid}")]])
            )
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            update_channel_setting(cid, "request_photo", file_id)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ তাৎক্ষণিক মেসেজ ১ এর ফটো সেট হয়েছে!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️  তাৎক্ষণিক মেসেজ সেটিংস", callback_data=f"ch:menu_msg1:{cid}")]])
            )
        else:
            await msg.reply_text("📷 একটি ছবি পাঠান অথবা 'remove' লিখুন।")
        return

    # ── Add Message 1 Button Text ──
    if action == "add_msg1_btn_text":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if not text:
            await msg.reply_text("❌ বাটনের নাম সঠিক নয়। দয়া করে আবার পাঠান।")
            return
        state["btn_text"] = text
        state["action"] = "add_msg1_btn_url"
        await msg.reply_text(
            f"🔗 বাটনের নাম: <b>{text}</b>\n\nএবার বাটনটির লিংক (URL) পাঠান।\n"
            "উদাহরণ: <code>https://t.me/example</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    # ── Add Message 1 Button URL ──
    if action == "add_msg1_btn_url":
        cid = state.get("channel_id")
        url = msg.text.strip() if msg.text else ""
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("t.me/")):
            await msg.reply_text("❌ লিংকটি অবশ্যই http://, https:// অথবা t.me/ দিয়ে শুরু হতে হবে। আবার পাঠান।")
            return
        
        ch = get_channel(cid)
        btns = ch.get("request_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btns.append({"text": state["btn_text"], "url": url})
        
        update_channel_setting(cid, "request_buttons", btns)
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            "✅ তাৎক্ষণিক মেসেজের কাস্টম বাটন যোগ করা হয়েছে!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️  তাৎক্ষণিক মেসেজ সেটিংস", callback_data=f"ch:menu_msg1:{cid}")]])
        )
        return

    # ── Set Message 2 text ──
    if action == "set_msg2_text":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if text.lower() == "reset":
            update_channel_setting(cid, "welcome_msg", None)
            resp = "✅ স্বাগতম মেসেজ ২ ডিফল্টে রিসেট করা হয়েছে।"
        else:
            update_channel_setting(cid, "welcome_msg", text)
            resp = "✅ স্বাগতম মেসেজ ২ আপডেট হয়েছে!"
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            resp,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ স্বাগতম মেসেজ সেটিংস", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    # ── Set Message 2 photo ──
    if action == "set_msg2_photo":
        cid = state.get("channel_id")
        if msg.text and msg.text.strip().lower() == "remove":
            update_channel_setting(cid, "welcome_photo", None)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ স্বাগতম মেসেজ ২ এর ফটো সরানো হয়েছে।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ স্বাগতম মেসেজ সেটিংস", callback_data=f"ch:menu_msg2:{cid}")]])
            )
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            update_channel_setting(cid, "welcome_photo", file_id)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ স্বাগতম মেসেজ ২ এর ফটো সেট হয়েছে!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ স্বাগতম মেসেজ সেটিংস", callback_data=f"ch:menu_msg2:{cid}")]])
            )
        else:
            await msg.reply_text("📷 একটি ছবি পাঠান অথবা 'remove' লিখুন।")
        return

    # ── Add Message 2 Button Text ──
    if action == "add_msg2_btn_text":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if not text:
            await msg.reply_text("❌ বাটনের নাম সঠিক নয়। দয়া করে আবার পাঠান।")
            return
        state["btn_text"] = text
        state["action"] = "add_msg2_btn_url"
        await msg.reply_text(
            f"🔗 বাটনের নাম: <b>{text}</b>\n\nএবার বাটনটির লিংক (URL) পাঠান।\n"
            "উদাহরণ: <code>https://t.me/example</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    # ── Add Message 2 Button URL ──
    if action == "add_msg2_btn_url":
        cid = state.get("channel_id")
        url = msg.text.strip() if msg.text else ""
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("t.me/")):
            await msg.reply_text("❌ লিংকটি অবশ্যই http://, https:// অথবা t.me/ দিয়ে শুরু হতে হবে। আবার পাঠান।")
            return
        
        ch = get_channel(cid)
        btns = ch.get("welcome_buttons") or []
        if isinstance(btns, str):
            try:
                btns = json.loads(btns)
            except Exception:
                btns = []
        btns.append({"text": state["btn_text"], "url": url})
        
        update_channel_setting(cid, "welcome_buttons", btns)
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            "✅ স্বাগতম মেসেজের কাস্টম বাটন যোগ করা হয়েছে!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ স্বাগতম মেসেজ সেটিংস", callback_data=f"ch:menu_msg2:{cid}")]])
        )
        return

    # ── Set invite link ──
    if action == "set_link":
        cid = state.get("channel_id")
        link = msg.text.strip() if msg.text else ""
        update_channel_setting(cid, "invite_link", link)
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            f"✅ লিংক সেট হয়েছে: {link}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ স্বাগতম মেসেজ সেটিংস", callback_data=f"ch:menu_msg2:{cid}")]]),
        )
        return

    # ── Add admin ──
    if action == "add_admin":
        try:
            target_id = int(msg.text.strip())
            try:
                user = await ctx.bot.get_chat(target_id)
                uname = user.username or user.first_name or str(target_id)
            except Exception:
                uname = str(target_id)
            add_admin(target_id, uname, uid)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                f"✅ <b>{uname}</b> (<code>{target_id}</code>) কে অ্যাডমিন করা হয়েছে!",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👮 অ্যাডমিন লিস্ট", callback_data="menu:admins")]]),
            )
        except ValueError:
            await msg.reply_text("❌ সঠিক User ID দিন (শুধু সংখ্যা)।")
        return

    # ── Restore backup ──
    if action == "restore_backup":
        if msg.document and (msg.document.file_name.endswith(".db") or msg.document.file_name.endswith(".json")):
            await msg.reply_text(
                "⚠️ আপনি কি নিশ্চিতভাবে রিস্টোর করতে চান? বর্তমান ডেটা মুছে যাবে!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ হ্যাঁ, রিস্টোর করুন", callback_data="confirm:restore:0"),
                     InlineKeyboardButton("❌ বাতিল", callback_data="menu:backup")],
                ]),
            )
            USER_STATES[uid] = {"action": "restore_confirm", "file_id": msg.document.file_id}
        else:
            await msg.reply_text("❌ .db অথবা .json ফাইল পাঠান।")
        return

    if action == "restore_confirm":
        # এটা callback থেকে handle হবে
        pass


# ─── CONFIRM RESTORE CALLBACK ─────────────────────────────────────────────────
async def handle_restore_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        return
    state = USER_STATES.get(uid, {})
    file_id = state.get("file_id")
    if not file_id:
        await q.edit_message_text("❌ কোনো ফাইল পাওয়া যায়নি। আবার চেষ্টা করুন।", reply_markup=kb_back_main())
        return
    try:
        file_obj = await ctx.bot.get_file(file_id)
        file_path_on_telegram = file_obj.file_path or ""
        ext = ".json" if file_path_on_telegram.endswith(".json") else ".db"
        tmp = f"restore_tmp{ext}"
        
        await file_obj.download_to_drive(tmp)
        if restore_from_file(tmp):
            USER_STATES.pop(uid, None)
            
            try:
                os.remove(tmp)
            except Exception:
                pass
                
            await q.edit_message_text(
                "✅ <b>রিস্টোর সফল হয়েছে!</b>\n\nপরিবর্তনগুলো কার্যকর হয়েছে।",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_back_main(),
            )
        else:
            await q.edit_message_text("❌ রিস্টোর ব্যর্থ হয়েছে। ফাইলটি বৈধ কিনা পরীক্ষা করুন।",
                                      reply_markup=kb_back_main())
    except Exception as e:
        logger.error(f"restore error: {e}")
        await q.edit_message_text(f"❌ ত্রুটি: {e}", reply_markup=kb_back_main())


# ─── ERROR HANDLER ────────────────────────────────────────────────────────────
async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception:", exc_info=ctx.error)
    tb = "".join(traceback.format_exception(type(ctx.error), ctx.error, ctx.error.__traceback__))
    if len(tb) > 3000:
        tb = tb[-3000:]
    for admin_id in ADMIN_IDS:
        try:
            await ctx.bot.send_message(
                admin_id,
                f"⚠️ <b>Bot Error:</b>\n<pre>{tb}</pre>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ─── AUTO-BACKUP JOB ──────────────────────────────────────────────────────────
async def auto_backup_job(bot: Bot):
    """প্রতিদিন স্বয়ংক্রিয় ব্যাকআপ।"""
    try:
        path = make_backup()
        for admin_id in ADMIN_IDS:
            with open(path, "rb") as f:
                await bot.send_document(
                    chat_id=admin_id,
                    document=f,
                    filename=os.path.basename(path),
                    caption=f"🔄 <b>স্বয়ংক্রিয় ব্যাকআপ</b>\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    parse_mode=ParseMode.HTML,
                )
    except Exception as e:
        logger.error(f"auto_backup_job: {e}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("❌ APPROVE_BOT_TOKEN, TELEGRAM_TOKEN, or BOT_TOKEN is not set in environment variables! Approve Bot cannot start.")
        return
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("panel",  cmd_panel))
    app.add_handler(CommandHandler("health", cmd_health))

    app.add_handler(ChatJoinRequestHandler(handle_join_request))

    # callback: restore confirm আলাদা (state-based)
    app.add_handler(CallbackQueryHandler(handle_restore_confirm, pattern="^confirm:restore:"))
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Text + photo + document messages
    app.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.Document.ALL,
        handle_message,
    ))

    app.add_error_handler(error_handler)

    # Scheduler
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        process_due_queue,
        "interval",
        seconds=30,
        args=[app],
        id="process_queue",
    )
    scheduler.add_job(
        auto_backup_job,
        "interval",
        hours=AUTO_BACKUP_HOURS,
        args=[app.bot],
        id="auto_backup",
    )
    scheduler.start()
    logger.info("Scheduler started ✓")

    logger.info("Bot starting... 🚀")
    app.run_polling(drop_pending_updates=True, close_loop=False, stop_signals=None)


def run_bot():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    main()


if __name__ == "__main__":
    run_bot()
