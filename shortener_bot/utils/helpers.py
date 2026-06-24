import re
import time
import uuid
import requests
import logging
from urllib.parse import quote
from datetime import datetime, timedelta
from functools import wraps
from shortener_bot.config.settings import settings
from shortener_bot.models.user import UserModel, SettingsModel

logger = logging.getLogger(__name__)


URL_RE = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@[A-Za-z0-9_]{5,})', re.IGNORECASE)


def filter_links(text: str) -> str:
    if not text:
        return text
    return re.sub(r'\n{3,}', '\n\n', URL_RE.sub('', text)).strip()


def apply_filters(text: str, uploader_id: int) -> str:
    if not text:
        return text
    user = UserModel.get(uploader_id)
    if user.get("text_filter"):
        return ""
    if user.get("link_filter"):
        return filter_links(text)
    return text


def get_short_link(url: str) -> str:
    try:
        r = requests.get(
            f"{settings.external_api.shortener_api_url}?api={settings.external_api.terabox_token}&url={quote(url)}",
            timeout=8,
        ).json()
        if r and r.get("status") != "error" and r.get("shortenedUrl"):
            return r["shortenedUrl"]
    except Exception as e:
        logger.warning(f"ShortLink API error: {e}")
    return url


def generate_batch_id() -> str:
    return uuid.uuid4().hex[:10]


def ico(val: int) -> str:
    return "🟢" if val else "🔴"


def format_time_ago(iso_time: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        now = datetime.now()
        diff = now - dt
        seconds = int(diff.total_seconds())

        if seconds < 60:
            return "কয়েক সেকেন্ড আগে"
        elif seconds < 3600:
            return f"{seconds // 60} মিনিট আগে"
        elif seconds < 86400:
            return f"{seconds // 3600} ঘণ্টা আগে"
        elif seconds < 2592000:
            return f"{seconds // 86400} দিন আগে"
        elif seconds < 31536000:
            return f"{seconds // 2592000} মাস আগে"
        else:
            return f"{seconds // 31536000} বছর আগে"
    except Exception:
        return "—"


def get_file_count_from_link(link: str) -> int:
    try:
        m = re.search(r'[?&]start=([A-Za-z0-9]+)', link)
        if not m:
            return 0
        fk = m.group(1)
        from shortener_bot.models.file import FileModel
        cnt = FileModel.count_by_batch(fk)
        if cnt > 0:
            return cnt
        return FileModel.count_by_key(fk)
    except Exception:
        return 0


def admin_required(func):
    @wraps(func)
    def wrapper(call, bot, *args, **kwargs):
        cid = str(call.message.chat.id)
        if UserModel.is_banned(cid):
            bot.answer_callback_query(call.id, "🚫 আপনি ব্যান করা হয়েছেন!", show_alert=True)
            return
        if not UserModel.is_admin(cid):
            bot.answer_callback_query(call.id, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!", show_alert=True)
            return
        return func(call, bot, *args, **kwargs)
    return wrapper


def build_post_markup(user: dict, dl_link: str, share_text: str):
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    mk = InlineKeyboardMarkup()

    if user.get("btn_tutorial", 1):
        from shortener_bot.models.database import db
        for tut in db.tutorials.find():
            mk.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))

    for btn in user.get("custom_buttons", []):
        if btn.get("status") == "on":
            mk.add(InlineKeyboardButton(btn['name'], url=btn['url']))

    row = []
    if user.get("btn_download", 1):
        row.append(InlineKeyboardButton("📥 ডাউনলোড", url=dl_link))
    if user.get("btn_share", 1):
        encoded = quote(share_text, safe='')
        share_url = f"https://t.me/share/url?url={encoded}&text="
        row.append(InlineKeyboardButton("🔗 শেয়ার করুন", url=share_url))
    if row:
        mk.row(*row)

    return mk