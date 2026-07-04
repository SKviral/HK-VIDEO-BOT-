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
# টোকেন প্রাইভেসি: প্রথমে এনভায়রনমেন্ট ভেরিয়েবল চেক করবে, না পেলে হার্ডকোডেড টোকেন ব্যবহার করবে
BOT_TOKEN = os.getenv("APPROVE_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or ""

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
def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """সব টেবিল তৈরি করো।"""
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

        # প্রথম super-admin insert
        for uid in ADMIN_IDS:
            conn.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by) VALUES (?, ?)",
                (uid, uid),
            )
    logger.info("Database initialized ✓")


# ─── HELPERS ─────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (user_id,)
        ).fetchone()
        return row is not None


def get_admins() -> list[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM admins ORDER BY added_at").fetchall()]


def add_admin(user_id: int, username: str, added_by: int) -> bool:
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
    with get_db() as conn:
        conn.execute("DELETE FROM admins WHERE user_id=?", (user_id,))
    return True


def get_channels() -> list[dict]:
    with get_db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM channels ORDER BY added_at").fetchall()]


def get_channel(channel_id: int) -> Optional[dict]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM channels WHERE channel_id=?", (channel_id,)).fetchone()
        return dict(row) if row else None


def upsert_channel(channel_id: int, title: str, username: str, invite_link: str, added_by: int):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO channels (channel_id, title, username, invite_link, added_by)
            VALUES (?,?,?,?,?)
            ON CONFLICT(channel_id) DO UPDATE SET title=excluded.title,
                username=excluded.username, invite_link=excluded.invite_link
        """, (channel_id, title, username, invite_link, added_by))


def update_channel_setting(channel_id: int, key: str, value):
    allowed = {
        "auto_accept", "delay_seconds", "silent_mode",
        "welcome_msg", "welcome_photo", "invite_link"
    }
    if key not in allowed:
        return
    with get_db() as conn:
        conn.execute(f"UPDATE channels SET {key}=? WHERE channel_id=?", (value, channel_id))


def remove_channel(channel_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM channels WHERE channel_id=?", (channel_id,))
        conn.execute("DELETE FROM pending_queue WHERE channel_id=?", (channel_id,))


def log_request(channel_id, user_id, username, full_name, accept_after_dt=None):
    accept_at = accept_after_dt.isoformat() if accept_after_dt else None
    try:
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO join_requests
                    (channel_id, user_id, username, full_name, accept_at, status)
                VALUES (?,?,?,?,?,?)
            """, (channel_id, user_id, username, full_name, accept_at,
                  "pending" if accept_at else "accepted"))
    except Exception as e:
        logger.warning(f"log_request: {e}")


def mark_accepted(channel_id, user_id):
    with get_db() as conn:
        conn.execute("""
            UPDATE join_requests
            SET status='accepted', accepted_at=datetime('now')
            WHERE channel_id=? AND user_id=? AND status='pending'
        """, (channel_id, user_id))


def enqueue(channel_id, user_id, username, full_name, accept_after: datetime):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO pending_queue
                (channel_id, user_id, username, full_name, accept_after)
            VALUES (?,?,?,?,?)
        """, (channel_id, user_id, username, full_name, accept_after.isoformat()))


def dequeue(channel_id, user_id):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM pending_queue WHERE channel_id=? AND user_id=?",
            (channel_id, user_id),
        )


def get_due_queue() -> list[dict]:
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_queue WHERE accept_after <= ?", (now,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_pending_queue(channel_id: int = None) -> list[dict]:
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


def get_stats(channel_id: int = None) -> dict:
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


def make_backup() -> str:
    """DB backup তৈরি করো, path return করো।"""
    Path(BACKUP_DIR).mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = f"{BACKUP_DIR}/backup_{ts}.db"
    shutil.copy2(DB_PATH, dest)
    logger.info(f"Backup created: {dest}")
    return dest


def restore_from_file(file_path: str) -> bool:
    try:
        # validate SQLite file
        conn = sqlite3.connect(file_path)
        conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        conn.close()
        shutil.copy2(file_path, DB_PATH)
        return True
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
    aa_txt = "⏸️ অটো-একসেপ্ট বন্ধ করুন" if ch["auto_accept"] else "▶️ অটো-একসেপ্ট চালু করুন"
    sl_txt = "🔇 সাইলেন্ট মোড বন্ধ করুন" if ch["silent_mode"] else "🔔 সাইলেন্ট মোড চালু করুন"
    delay_m = ch["delay_seconds"] // 60
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(aa_txt, callback_data=f"ch:toggle_aa:{channel_id}")],
        [InlineKeyboardButton(f"⏱️ ডিলে: {delay_m} মিনিট → পরিবর্তন", callback_data=f"ch:set_delay:{channel_id}")],
        [InlineKeyboardButton(sl_txt, callback_data=f"ch:toggle_silent:{channel_id}")],
        [InlineKeyboardButton("💬 ওয়েলকাম মেসেজ সেট", callback_data=f"ch:set_msg:{channel_id}")],
        [InlineKeyboardButton("🖼️ ওয়েলকাম ফটো সেট",  callback_data=f"ch:set_photo:{channel_id}")],
        [InlineKeyboardButton("📊 এই চ্যানেলের স্ট্যাটস", callback_data=f"stats:channel:{channel_id}")],
        [InlineKeyboardButton("⏳ পেন্ডিং কিউ",         callback_data=f"queue:channel:{channel_id}")],
        [InlineKeyboardButton("🔗 ইনভাইট লিংক সেট",    callback_data=f"ch:set_link:{channel_id}")],
        [InlineKeyboardButton("🗑️ চ্যানেল সরিয়ে দিন",  callback_data=f"ch:remove:{channel_id}")],
        [InlineKeyboardButton("🔙 চ্যানেল লিস্ট",       callback_data="ch:list:0")],
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
    "🎉 আপনাকে স্বাগতম! চ্যানেলটি ঘুরে দেখুন এবং উপভোগ করুন।\n\n"
    "📌 <i>যেকোনো সমস্যায় অ্যাডমিনের সাথে যোগাযোগ করুন।</i>"
)


async def send_welcome(bot: Bot, channel: dict, user_id: int):
    """User কে welcome message পাঠাও।"""
    if channel.get("silent_mode"):
        return
    msg = channel.get("welcome_msg") or DEFAULT_WELCOME
    photo = channel.get("welcome_photo")

    buttons = []
    link = channel.get("invite_link") or channel.get("username")
    if link:
        if not link.startswith("http"):
            link = f"https://t.me/{link.lstrip('@')}"
        buttons.append([InlineKeyboardButton("📡 চ্যানেলে যান", url=link)])
    buttons.append([InlineKeyboardButton("💬 সাপোর্ট", url="https://t.me/your_support")])
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
    except (Forbidden, BadRequest) as e:
        logger.warning(f"Cannot send welcome to {user_id}: {e}")
    except TelegramError as e:
        logger.error(f"send_welcome error: {e}")


# ─── AUTO-ACCEPT CORE ─────────────────────────────────────────────────────────
async def do_accept(bot: Bot, channel_id: int, user_id: int, full_name: str, username: str):
    """একটি join request accept করো।"""
    try:
        await bot.approve_chat_join_request(chat_id=channel_id, user_id=user_id)
        mark_accepted(channel_id, user_id)
        dequeue(channel_id, user_id)
        logger.info(f"Accepted: user={user_id} ({username}) → channel={channel_id}")
        ch = get_channel(channel_id)
        if ch:
            await send_welcome(bot, ch, user_id)
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

    if not ch["auto_accept"]:
        logger.info(f"Auto-accept paused for {channel_id}, skipping user {user_id}")
        return

    delay = ch["delay_seconds"] or 0

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
        status = "✅ চালু" if ch["auto_accept"] else "⏸️ বন্ধ"
        delay_m = ch["delay_seconds"] // 60
        text = (
            f"📡 <b>{ch['title']}</b>\n\n"
            f"🆔 ID: <code>{cid}</code>\n"
            f"⚡ অটো-একসেপ্ট: {status}\n"
            f"⏱️ ডিলে: {delay_m} মিনিট\n"
            f"🔇 সাইলেন্ট: {'হ্যাঁ' if ch['silent_mode'] else 'না'}\n"
        )
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb_channel_manage(cid))
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
            new_val = 0 if ch["auto_accept"] else 1
            update_channel_setting(cid, "auto_accept", new_val)
            status = "✅ চালু করা হয়েছে" if new_val else "⏸️ বন্ধ করা হয়েছে"
            await q.answer(f"অটো-একসেপ্ট {status}", show_alert=True)
        ch2 = get_channel(cid)
        status2 = "✅ চালু" if ch2["auto_accept"] else "⏸️ বন্ধ"
        delay_m = ch2["delay_seconds"] // 60
        await q.edit_message_text(
            f"📡 <b>{ch2['title']}</b>\n\n"
            f"⚡ অটো-একসেপ্ট: {status2}\n"
            f"⏱️ ডিলে: {delay_m} মিনিট",
            parse_mode=ParseMode.HTML,
            reply_markup=kb_channel_manage(cid),
        )
        return

    if data.startswith("ch:toggle_silent:"):
        cid = int(data.split(":")[-1])
        ch = get_channel(cid)
        if ch:
            new_val = 0 if ch["silent_mode"] else 1
            update_channel_setting(cid, "silent_mode", new_val)
            status = "🔇 চালু" if new_val else "🔔 বন্ধ"
            await q.answer(f"সাইলেন্ট মোড {status}", show_alert=True)
        await handle_callback(update, ctx)  # re-render
        return

    if data.startswith("ch:set_delay:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_delay", "channel_id": cid}
        await q.edit_message_text(
            "⏱️ <b>ডিলে সেট করুন</b>\n\n"
            "মিনিটে সংখ্যা পাঠান (0 = তাৎক্ষণিক একসেপ্ট)\n"
            "উদাহরণ: <code>5</code> (৫ মিনিট পরে একসেপ্ট হবে)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:manage:{cid}")]]),
        )
        return

    if data.startswith("ch:set_msg:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_msg", "channel_id": cid}
        await q.edit_message_text(
            "💬 <b>ওয়েলকাম মেসেজ সেট করুন</b>\n\n"
            "HTML formatting সাপোর্টেড (<b>bold</b>, <i>italic</i>, <code>code</code>)\n\n"
            "ডিফল্টে ফিরতে: <code>reset</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:manage:{cid}")]]),
        )
        return

    if data.startswith("ch:set_photo:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_photo", "channel_id": cid}
        await q.edit_message_text(
            "🖼️ <b>ওয়েলকাম ফটো সেট করুন</b>\n\n"
            "একটি ছবি পাঠান। এই ছবিটি welcome message এর সাথে দেখাবে।\n\n"
            "ফটো সরাতে: <code>remove</code> পাঠান",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:manage:{cid}")]]),
        )
        return

    if data.startswith("ch:set_link:"):
        cid = int(data.split(":")[-1])
        USER_STATES[uid] = {"action": "set_link", "channel_id": cid}
        await q.edit_message_text(
            "🔗 <b>ইনভাইট লিংক সেট করুন</b>\n\n"
            "লিংক পাঠান (যেমন: <code>https://t.me/+xxxxx</code> বা <code>@username</code>)",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ বাতিল", callback_data=f"ch:manage:{cid}")]]),
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
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ চ্যানেল সেটিংস", callback_data=f"ch:manage:{cid}")]]),
            )
        except (ValueError, TypeError):
            await msg.reply_text("❌ সঠিক সংখ্যা দিন (মিনিটে)। উদাহরণ: <code>5</code>", parse_mode=ParseMode.HTML)
        return

    # ── Set welcome message ──
    if action == "set_msg":
        cid = state.get("channel_id")
        text = msg.text.strip() if msg.text else ""
        if text.lower() == "reset":
            update_channel_setting(cid, "welcome_msg", None)
            resp = "✅ ওয়েলকাম মেসেজ ডিফল্টে রিসেট করা হয়েছে।"
        else:
            update_channel_setting(cid, "welcome_msg", text)
            resp = "✅ ওয়েলকাম মেসেজ আপডেট হয়েছে!"
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            resp,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ চ্যানেল সেটিংস", callback_data=f"ch:manage:{cid}")]]),
        )
        return

    # ── Set welcome photo ──
    if action == "set_photo":
        cid = state.get("channel_id")
        if msg.text and msg.text.strip().lower() == "remove":
            update_channel_setting(cid, "welcome_photo", None)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ ওয়েলকাম ফটো সরানো হয়েছে।",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ সেটিংস", callback_data=f"ch:manage:{cid}")]]),
            )
        elif msg.photo:
            file_id = msg.photo[-1].file_id
            update_channel_setting(cid, "welcome_photo", file_id)
            USER_STATES.pop(uid, None)
            await msg.reply_text(
                "✅ ওয়েলকাম ফটো সেট হয়েছে!",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ সেটিংস", callback_data=f"ch:manage:{cid}")]]),
            )
        else:
            await msg.reply_text("📷 একটি ছবি পাঠান অথবা 'remove' লিখুন।")
        return

    # ── Set invite link ──
    if action == "set_link":
        cid = state.get("channel_id")
        link = msg.text.strip() if msg.text else ""
        update_channel_setting(cid, "invite_link", link)
        USER_STATES.pop(uid, None)
        await msg.reply_text(
            f"✅ লিংক সেট হয়েছে: {link}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ সেটিংস", callback_data=f"ch:manage:{cid}")]]),
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
        if msg.document and msg.document.file_name.endswith(".db"):
            await msg.reply_text(
                "⚠️ আপনি কি নিশ্চিতভাবে রিস্টোর করতে চান? বর্তমান ডেটা মুছে যাবে!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ হ্যাঁ, রিস্টোর করুন", callback_data="confirm:restore:0"),
                     InlineKeyboardButton("❌ বাতিল", callback_data="menu:backup")],
                ]),
            )
            # file_id store করো
            USER_STATES[uid] = {"action": "restore_confirm", "file_id": msg.document.file_id}
        else:
            await msg.reply_text("❌ .db ফাইল পাঠান।")
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
        tmp = "/tmp/restore_tmp.db"
        file = await ctx.bot.get_file(file_id)
        await file.download_to_drive(tmp)
        if restore_from_file(tmp):
            USER_STATES.pop(uid, None)
            await q.edit_message_text(
                "✅ <b>রিস্টোর সফল হয়েছে!</b>\n\nBot restart করুন পরিবর্তনগুলো কার্যকর করতে।",
                parse_mode=ParseMode.HTML,
                reply_markup=kb_back_main(),
            )
        else:
            await q.edit_message_text("❌ রিস্টোর ব্যর্থ হয়েছে। ফাইলটি বৈধ .db ফাইল কিনা পরীক্ষা করুন।",
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
    app.run_polling(drop_pending_updates=True)


def run_bot():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    main()


if __name__ == "__main__":
    run_bot()
