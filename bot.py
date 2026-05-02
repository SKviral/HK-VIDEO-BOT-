"""
╔══════════════════════════════════════════════════════════════╗
║           🚀 PREMIUM FILE SHARE BOT v4.0                     ║
║           Advanced Telegram File Management Bot              ║
║   Features: Auto-Post, Batch, Analytics, Force Subscribe,    ║
║   Post Settings, Link Filter, Text Filter, Protect Content   ║
╚══════════════════════════════════════════════════════════════╝
"""

import os
import re
import time
import json
import uuid
import threading
import requests
import telebot
import logging
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from flask import Flask
from urllib.parse import quote

# ═══════════════════════════════════════════════════════════
#                    লগিং সিস্টেম
# ═══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#                  কনফিগারেশন
# ═══════════════════════════════════════════════════════════
BOT_TOKEN     = os.environ.get("BOT_TOKEN",     "আপনার_বট_টোকেন")
BOT_USERNAME  = os.environ.get("BOT_USERNAME",  "YourBotUsername")
MAIN_ADMIN_ID = os.environ.get("MAIN_ADMIN_ID", "5991854507")
TERABOX_TOKEN = os.environ.get("TERABOX_TOKEN", "71b16be6b48d01937bfe7d2c3043cbc0b6363c82")
MONGO_URL     = os.environ.get("MONGO_URL",     "আপনার_MongoDB_URL")
BOT_VERSION   = "4.0.0"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════
#                   MongoDB সেটআপ
# ═══════════════════════════════════════════════════════════
client = MongoClient(MONGO_URL)
db     = client['telegram_bot_db']

users_col         = db['users']
files_col         = db['files']
queue_col         = db['queue']
admins_col        = db['admins']
channels_col      = db['update_channels']
tutorials_col     = db['tutorials']
auto_channels_col = db['auto_channels']
stats_col         = db['bot_stats']
banned_col        = db['banned_users']
force_sub_col     = db['force_subscribe']
settings_col      = db['bot_settings']

# ইন্ডেক্স
users_col.create_index("chat_id", unique=True, background=True)
files_col.create_index("file_key", background=True)
files_col.create_index("batch_id", background=True)
queue_col.create_index("delete_at", background=True)

# মূল এডমিন
if not admins_col.find_one({"chat_id": str(MAIN_ADMIN_ID)}):
    admins_col.insert_one({
        "chat_id": str(MAIN_ADMIN_ID),
        "role": "super_admin",
        "added_at": datetime.now().isoformat()
    })

# ═══════════════════════════════════════════════════════════
#              গ্লোবাল সেটিংস হেল্পার
# ═══════════════════════════════════════════════════════════
def get_setting(key, default=0):
    doc = settings_col.find_one({"key": key})
    return doc["value"] if doc else default

def set_setting(key, value):
    settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def toggle_setting(key):
    cur = get_setting(key, 0)
    new = 0 if cur else 1
    set_setting(key, new)
    return new

# ═══════════════════════════════════════════════════════════
#              লিংক / টেক্সট ফিল্টার ইউটিলিটি
# ═══════════════════════════════════════════════════════════
URL_PATTERN = re.compile(
    r'(https?://[^\s]+|t\.me/[^\s]+|@[A-Za-z0-9_]{5,})',
    re.IGNORECASE
)

def filter_links(text):
    if not text:
        return text
    cleaned = URL_PATTERN.sub('', text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned

def apply_caption_filters(text, admin_chat_id):
    """
    এডমিনের ফিল্টার সেটিং অনুযায়ী ক্যাপশন প্রসেস করে।
    text_filter চালু → পুরো ক্যাপশন খালি
    link_filter চালু → URL / @username রিমুভ
    """
    user = get_user(admin_chat_id)
    if not text:
        return text
    if user.get("text_filter", 0) == 1:
        return ""
    if user.get("link_filter", 0) == 1:
        return filter_links(text)
    return text

# ═══════════════════════════════════════════════════════════
#                  ডাটাবেস হেল্পার
# ═══════════════════════════════════════════════════════════
_DEFAULT_USER = {
    "header": "", "footer": "",
    "post_header": "", "post_footer": "",
    "auto_delete": 0,
    "pending_link": "", "pending_short_link": "",
    "step": "none",
    "batch_id": "",
    "post_link_toggle": 1,
    "post_tutorial_toggle": 1,
    "link_repeat_count": 1,
    "custom_buttons": [],
    "temp_media_id": "", "temp_media_type": "",
    "joined_at": "",
    "last_active": "",
    "total_downloads": 0,
    "total_uploads": 0,
    "link_filter": 0,
    "text_filter": 0,
}

def get_user(chat_id):
    chat_id = str(chat_id)
    user = users_col.find_one({"chat_id": chat_id})
    now_iso = datetime.now().isoformat()
    if not user:
        user = dict(_DEFAULT_USER)
        user["chat_id"] = chat_id
        user["joined_at"] = now_iso
        user["last_active"] = now_iso
        users_col.insert_one(user)
        _inc_stat("new_users")
    else:
        updates = {k: v for k, v in _DEFAULT_USER.items() if k not in user}
        updates["last_active"] = now_iso
        users_col.update_one({"chat_id": chat_id}, {"$set": updates})
        user.update(updates)
    return user

def update_user(chat_id, updates):
    users_col.update_one({"chat_id": str(chat_id)}, {"$set": updates})

def update_step(chat_id, step):
    update_user(chat_id, {"step": step})

def is_admin(chat_id):
    return bool(admins_col.find_one({"chat_id": str(chat_id)}))

def is_banned(chat_id):
    return bool(banned_col.find_one({"chat_id": str(chat_id)}))

# ═══════════════════════════════════════════════════════════
#                   স্ট্যাটিস্টিক্স
# ═══════════════════════════════════════════════════════════
def _inc_stat(field, n=1):
    today = datetime.now().strftime("%Y-%m-%d")
    stats_col.update_one({"date": today}, {"$inc": {field: n}}, upsert=True)

def get_bot_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    today_doc = stats_col.find_one({"date": today}) or {}
    active_today = users_col.count_documents({"last_active": {"$regex": f"^{today}"}})
    return {
        "total_users":     users_col.count_documents({}),
        "total_files":     files_col.count_documents({}),
        "total_admins":    admins_col.count_documents({}),
        "total_banned":    banned_col.count_documents({}),
        "active_today":    active_today,
        "downloads_today": today_doc.get("downloads", 0),
        "uploads_today":   today_doc.get("uploads", 0),
    }

# ═══════════════════════════════════════════════════════════
#              ফোর্স সাবস্ক্রাইব
# ═══════════════════════════════════════════════════════════
def check_force_subscribe(chat_id):
    force_channels = list(force_sub_col.find({"status": "on"}))
    if not force_channels:
        return True, []
    not_joined = []
    for ch in force_channels:
        try:
            member = bot.get_chat_member(ch['channel_id'], int(chat_id))
            if member.status in ['left', 'kicked']:
                not_joined.append(ch)
        except:
            not_joined.append(ch)
    return len(not_joined) == 0, not_joined

def send_force_sub_msg(chat_id, not_joined, file_key=None):
    markup = InlineKeyboardMarkup()
    for ch in not_joined:
        markup.add(InlineKeyboardButton(f"📢 {ch['name']} — Join করুন", url=ch['url']))
    cb = f"check_sub_{file_key}" if file_key else "check_sub_none"
    markup.add(InlineKeyboardButton("✅ Join করেছি — Check করুন", callback_data=cb))
    bot.send_message(
        chat_id,
        "🔒 <b>ফাইল পেতে নিচের চ্যানেলগুলোতে Join করুন!</b>\n\n"
        "Join করার পর <b>✅ Join করেছি</b> বাটনে ক্লিক করুন।",
        reply_markup=markup
    )

# ═══════════════════════════════════════════════════════════
#                   অটো-ডিলিট ওয়ার্কার
# ═══════════════════════════════════════════════════════════
def auto_delete_worker():
    while True:
        try:
            now = int(time.time())
            expired = list(queue_col.find({"delete_at": {"$lte": now}}))
            ch_list = list(channels_col.find())
            for item in expired:
                try:
                    bot.delete_message(item['chat_id'], item['message_id'])
                    markup = InlineKeyboardMarkup()
                    for ch in ch_list:
                        markup.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                    bot.send_message(
                        item['chat_id'],
                        "⚠️ <b>সময় শেষ! ফাইলটি মুছে গেছে।</b>\n🔁 আবার পেতে লিংকে ক্লিক করুন।",
                        reply_markup=markup if ch_list else None
                    )
                except Exception as e:
                    logger.warning(f"Auto-delete: {e}")
                finally:
                    queue_col.delete_one({"_id": item["_id"]})
        except Exception as e:
            logger.error(f"Auto-delete worker: {e}")
        time.sleep(10)

threading.Thread(target=auto_delete_worker, daemon=True).start()

# ═══════════════════════════════════════════════════════════
#              ব্রডকাস্ট ওয়ার্কার
# ═══════════════════════════════════════════════════════════
def broadcast_worker(admin_id, from_chat, msg_id, target="all"):
    query = {}
    if target == "active":
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        query = {"last_active": {"$gte": yesterday}}
    all_users = list(users_col.find(query, {"chat_id": 1}))
    total = len(all_users)
    ok = fail = 0
    try:
        bot.send_message(admin_id, f"📡 ব্রডকাস্ট শুরু! মোট: <b>{total}</b> জন")
    except: pass
    for i, u in enumerate(all_users):
        try:
            bot.copy_message(u['chat_id'], from_chat, msg_id)
            ok += 1
        except:
            fail += 1
        time.sleep(0.05)
        if (i + 1) % 100 == 0:
            try:
                bot.send_message(admin_id, f"📊 {i+1}/{total} | ✅{ok} ❌{fail}")
            except: pass
    try:
        bot.send_message(
            admin_id,
            f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n"
            f"📨 মোট: <b>{total}</b>\n✅ সফল: <b>{ok}</b>\n❌ ব্যর্থ: <b>{fail}</b>"
        )
    except: pass

# ═══════════════════════════════════════════════════════════
#              Terabox শর্ট লিংক
# ═══════════════════════════════════════════════════════════
def get_short_link(long_url):
    try:
        res = requests.get(
            f"https://teraboxlinks.com/api?api={TERABOX_TOKEN}&url={quote(long_url)}",
            timeout=8
        ).json()
        if res and res.get("status") != "error":
            s = res.get("shortenedUrl")
            if s:
                return s
    except Exception as e:
        logger.warning(f"Short link: {e}")
    return long_url

# ═══════════════════════════════════════════════════════════
#          চ্যানেলে পোস্ট (ফিল্টার + Protect সহ)
# ═══════════════════════════════════════════════════════════
def _send_media(channel_id, media_type, media_id, caption, markup, protect):
    kw = {"caption": caption, "reply_markup": markup, "protect_content": protect}
    if media_type == 'photo':
        bot.send_photo(channel_id, media_id, **kw)
    elif media_type == 'video':
        bot.send_video(channel_id, media_id, **kw)

def execute_channel_post(chat_id, user, media_type, media_id):
    d_link = user.get("pending_link", "")
    s_link = user.get("pending_short_link", "")

    raw_ph = user.get('post_header', '')
    raw_pf = user.get('post_footer', '')

    # ফিল্টার প্রয়োগ পোস্ট header/footer এ
    p_head = apply_caption_filters(raw_ph, chat_id)
    p_foot = apply_caption_filters(raw_pf, chat_id)
    ph_txt = f"{p_head}\n\n" if p_head else ""
    pf_txt = f"\n\n{p_foot}" if p_foot else ""

    ad_markup = InlineKeyboardMarkup()
    prem_markup = InlineKeyboardMarkup()
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")

    if user.get("post_link_toggle", 1) == 1:
        repeats = max(1, min(user.get("link_repeat_count", 1), 5))
        ad_links = "\n".join([s_link] * repeats)
        prem_links = "\n".join([d_link] * repeats)
        ad_caption = f"{ph_txt}🔗 <b>Download Link:</b>\n{ad_links}\n\n<i>🕐 {now_str}</i>{pf_txt}".strip()
        prem_caption = f"{ph_txt}🔗 <b>Direct Download:</b>\n{prem_links}\n\n<i>🕐 {now_str}</i>{pf_txt}".strip()

        if user.get("post_tutorial_toggle", 1) == 1:
            for tut in tutorials_col.find():
                ad_markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))

        for btn in user.get("custom_buttons", []):
            if btn.get("status") == "on":
                ad_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))
                prem_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))

        ad_markup.add(InlineKeyboardButton("📥 ডাউনলোড করুন", url=s_link))
        prem_markup.add(InlineKeyboardButton("💎 ডাইরেক্ট ডাউনলোড", url=d_link))
    else:
        ad_caption = f"{ph_txt}{pf_txt}".strip()
        prem_caption = ad_caption

    protect = bool(get_setting("protect_content", 0))

    post_count = 0
    for ch in auto_channels_col.find({"type": "ad", "status": "on"}):
        try:
            _send_media(ch['channel_id'], media_type, media_id, ad_caption, ad_markup, protect)
            post_count += 1
        except Exception as e:
            logger.warning(f"Ad post ({ch.get('name')}): {e}")

    for ch in auto_channels_col.find({"type": "premium", "status": "on"}):
        try:
            _send_media(ch['channel_id'], media_type, media_id, prem_caption, prem_markup, protect)
            post_count += 1
        except Exception as e:
            logger.warning(f"Premium post ({ch.get('name')}): {e}")

    for ch in auto_channels_col.find({"type": "log", "status": "on"}):
        try:
            log_cap = f"💾 <b>Media Backup</b>\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            _send_media(ch['channel_id'], media_type, media_id, log_cap, None, False)
        except Exception as e:
            logger.warning(f"Log post: {e}")

    _inc_stat("uploads")
    lf_st = "🟢 চালু" if user.get("link_filter") else "🔴 বন্ধ"
    tf_st = "🟢 চালু" if user.get("text_filter") else "🔴 বন্ধ"
    pc_st = "🟢 চালু" if protect else "🔴 বন্ধ"
    bot.send_message(
        chat_id,
        f"✅ <b>পোস্ট সম্পন্ন!</b>\n"
        f"📤 <b>{post_count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n\n"
        f"🔒 Protect Content: {pc_st}\n"
        f"🔗 Link Filter: {lf_st}\n"
        f"📝 Text Filter: {tf_st}"
    )
    update_user(chat_id, {
        "step": "none", "pending_link": "", "pending_short_link": "",
        "temp_media_id": "", "temp_media_type": ""
    })

# ═══════════════════════════════════════════════════════════
#              মেনু হেল্পারস
# ═══════════════════════════════════════════════════════════
def _main_menu_markup():
    m = InlineKeyboardMarkup()
    m.add(InlineKeyboardButton("📦 ব্যাচ আপলোড", callback_data="start_batch"))
    m.row(
        InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings"),
        InlineKeyboardButton("📊 স্ট্যাটস", callback_data="show_stats")
    )
    m.row(
        InlineKeyboardButton("📢 ব্রডকাস্ট", callback_data="broadcast"),
        InlineKeyboardButton("ℹ️ হেল্প", callback_data="help_menu")
    )
    return m

def _back(cb):
    return InlineKeyboardButton("🔙 ব্যাক", callback_data=cb)

# ═══════════════════════════════════════════════════════════
#              ফাইল ডেলিভারি
# ═══════════════════════════════════════════════════════════
def _deliver_files(chat_id, file_key, user):
    files = list(files_col.find({"$or": [{"file_key": file_key}, {"batch_id": file_key}]}))
    if not files:
        bot.send_message(chat_id, "❌ <b>ফাইল পাওয়া যায়নি!</b>\nলিংকটি মেয়াদোত্তীর্ণ হতে পারে।")
        return

    bot.send_message(chat_id, f"⏳ আপনার {'ফাইলগুলো' if len(files)>1 else 'ফাইলটি'} পাঠানো হচ্ছে...")
    uploader = get_user(files[0]['uploader'])
    h = uploader.get('header', '')
    f_ = uploader.get('footer', '')
    caption = f"{h}\n\n{f_}".strip() if (h or f_) else ""
    caption = apply_caption_filters(caption, files[0]['uploader'])

    markup = InlineKeyboardMarkup()
    for tut in tutorials_col.find():
        markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
    for ch in channels_col.find():
        markup.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))

    protect = bool(get_setting("protect_content", 0))

    delivered = 0
    for f in files:
        sent_id = None
        try:
            kw = {
                "caption": caption,
                "reply_markup": markup if markup.keyboard else None,
                "protect_content": protect
            }
            res = None
            if f['type'] == 'document': res = bot.send_document(chat_id, f['file_id'], **kw)
            elif f['type'] == 'video':  res = bot.send_video(chat_id,    f['file_id'], **kw)
            elif f['type'] == 'photo':  res = bot.send_photo(chat_id,    f['file_id'], **kw)
            elif f['type'] == 'audio':  res = bot.send_audio(chat_id,    f['file_id'],
                                            caption=caption,
                                            reply_markup=kw['reply_markup'],
                                            protect_content=protect)
            if res:
                sent_id = res.message_id
                delivered += 1
        except:
            if f.get('log_chat_id') and f.get('log_msg_id'):
                try:
                    res = bot.copy_message(
                        chat_id, f['log_chat_id'], f['log_msg_id'],
                        caption=caption,
                        reply_markup=markup if markup.keyboard else None,
                        protect_content=protect
                    )
                    sent_id = res.message_id
                    delivered += 1
                except: pass

        if sent_id and uploader.get("auto_delete", 0) > 0:
            delete_at = int(time.time()) + uploader["auto_delete"] * 60
            queue_col.insert_one({"chat_id": chat_id, "message_id": sent_id, "delete_at": delete_at})
        time.sleep(0.3)

    if delivered:
        _inc_stat("downloads", delivered)
        update_user(chat_id, {"total_downloads": user.get("total_downloads", 0) + delivered})
        if uploader.get("auto_delete", 0) > 0:
            bot.send_message(
                chat_id,
                f"⚠️ <i>ফাইল{'গুলো' if delivered>1 else 'টি'} "
                f"<b>{uploader['auto_delete']} মিনিট</b> পর মুছে যাবে।</i>"
            )
    else:
        bot.send_message(chat_id, "❌ ফাইল পাঠানো সম্ভব হয়নি।")

# ═══════════════════════════════════════════════════════════
#            কলব্যাক হ্যান্ডলার
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = str(call.message.chat.id)
    msg_id  = call.message.message_id
    data    = call.data
    user    = get_user(chat_id)

    if is_banned(chat_id):
        bot.answer_callback_query(call.id, "🚫 আপনি ব্যান করা হয়েছেন!", show_alert=True)
        return

    # ── Force Sub Check ──
    if data.startswith("check_sub_"):
        file_key = data.replace("check_sub_", "")
        joined, not_joined = check_force_subscribe(chat_id)
        if joined:
            bot.answer_callback_query(call.id, "✅ Join নিশ্চিত হয়েছে!", show_alert=True)
            try: bot.delete_message(chat_id, msg_id)
            except: pass
            if file_key and file_key != "none":
                _deliver_files(chat_id, file_key, user)
        else:
            bot.answer_callback_query(call.id, "❌ এখনো সব চ্যানেলে Join করেননি!", show_alert=True)
        return

    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!", show_alert=True)
        return

    # ── Thumbnail Confirm/Cancel ──
    if data == "confirm_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        execute_channel_post(chat_id, user, user.get("temp_media_type"), user.get("temp_media_id"))
        return
    elif data == "cancel_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        update_user(chat_id, {"step": "wait_thumbnail", "temp_media_id": "", "temp_media_type": ""})
        bot.send_message(chat_id, "❌ বাতিল। নতুন থাম্বনেইল দিন।")
        return

    # ════════════════════════════
    #        MAIN MENU
    # ════════════════════════════
    if data == "main_menu":
        update_step(chat_id, "none")
        s = get_bot_stats()
        bot.edit_message_text(
            f"👋 <b>এডমিন প্যানেলে স্বাগতম!</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>",
            chat_id, msg_id, reply_markup=_main_menu_markup()
        )

    # ════════════════════════════
    #           STATS
    # ════════════════════════════
    elif data == "show_stats":
        s = get_bot_stats()
        m = InlineKeyboardMarkup()
        m.add(_back("main_menu"))
        bot.edit_message_text(
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"📥 আজ ডাউনলোড: <b>{s['downloads_today']}</b>\n"
            f"📤 আজ আপলোড: <b>{s['uploads_today']}</b>\n"
            f"👑 এডমিন: <b>{s['total_admins']}</b>\n"
            f"🚫 ব্যানড: <b>{s['total_banned']}</b>\n\n"
            f"🕐 {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            chat_id, msg_id, reply_markup=m
        )

    # ════════════════════════════
    #         BROADCAST
    # ════════════════════════════
    elif data == "broadcast":
        m = InlineKeyboardMarkup()
        m.row(
            InlineKeyboardButton("📡 সবাইকে",     callback_data="bc_all"),
            InlineKeyboardButton("🟢 সক্রিয়দের",  callback_data="bc_active")
        )
        m.add(_back("main_menu"))
        bot.edit_message_text("📢 <b>ব্রডকাস্ট:</b>\nকাদের কাছে পাঠাবেন?", chat_id, msg_id, reply_markup=m)

    elif data in ["bc_all", "bc_active"]:
        tgt = "all" if data == "bc_all" else "active"
        update_user(chat_id, {"step": f"wait_broadcast_{tgt}"})
        bot.send_message(chat_id, "📢 ব্রডকাস্টের মেসেজ/ছবি/ভিডিও পাঠান:")

    # ════════════════════════════
    #        BATCH UPLOAD
    # ════════════════════════════
    elif data == "start_batch":
        batch_id = str(uuid.uuid4().hex)[:10]
        update_user(chat_id, {"batch_id": batch_id, "step": "wait_batch"})
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)", callback_data="finish_batch"))
        bot.edit_message_text(
            "📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\nফাইলগুলো একে একে দিন। শেষে Finish ক্লিক করুন।",
            chat_id, msg_id, reply_markup=m
        )

    elif data == "finish_batch":
        batch_id = user.get("batch_id")
        if not batch_id:
            bot.answer_callback_query(call.id, "⚠️ ব্যাচ আপলোড আগেই শেষ হয়েছে!", show_alert=True)
            return
        count = files_col.count_documents({"batch_id": batch_id})
        if count == 0:
            bot.answer_callback_query(call.id, "⚠️ কোনো ফাইল যোগ করা হয়নি!", show_alert=True)
            return
        bot.edit_message_text("⏳ লিংক তৈরি হচ্ছে...", chat_id, msg_id)
        dl = f"https://t.me/{BOT_USERNAME}?start={batch_id}"
        sl = get_short_link(dl)
        update_user(chat_id, {"step": "wait_thumbnail", "pending_link": dl, "pending_short_link": sl, "batch_id": ""})
        bot.edit_message_text(
            f"✅ <b>{count}টি ফাইল সেভ হয়েছে!</b>\n\n"
            f"💎 ডাইরেক্ট:\n<code>{dl}</code>\n\n"
            f"📺 শর্ট:\n<code>{sl}</code>\n\n"
            f"🖼️ থাম্বনেইল দিন বা /skip লিখুন।",
            chat_id, msg_id, disable_web_page_preview=True
        )

    # ════════════════════════════
    #         SETTINGS
    # ════════════════════════════
    elif data == "settings":
        update_step(chat_id, "none")
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("📝 পোস্ট সেটিংস",         callback_data="menu_post_settings"))
        m.add(InlineKeyboardButton("📁 ফাইল সেটিংস",          callback_data="menu_file_settings"))
        m.add(InlineKeyboardButton("🔗 আপডেট চ্যানেল",        callback_data="menu_channels"))
        m.add(InlineKeyboardButton("🎥 টিউটোরিয়াল",           callback_data="menu_tutorials"))
        m.add(InlineKeyboardButton("📤 অটো পোস্ট চ্যানেল",    callback_data="menu_auto_post"))
        m.add(InlineKeyboardButton("🔒 ফোর্স সাবস্ক্রাইব",    callback_data="menu_force_sub"))
        m.add(InlineKeyboardButton("⚙️ অ্যাডভান্সড সেটিংস",   callback_data="menu_advanced"))
        m.add(_back("main_menu"))
        bot.edit_message_text("⚙️ <b>বট সেটিংস:</b>", chat_id, msg_id, reply_markup=m)

    # ════════════════════════════
    #  📝 পোস্ট সেটিংস (নতুন)
    # ════════════════════════════
    elif data == "menu_post_settings":
        u  = get_user(chat_id)
        ph = u.get("post_header","") or "<i>সেট করা হয়নি</i>"
        pf = u.get("post_footer","") or "<i>সেট করা হয়নি</i>"
        lf = "🟢 চালু" if u.get("link_filter",0) else "🔴 বন্ধ"
        tf = "🟢 চালু" if u.get("text_filter",0) else "🔴 বন্ধ"

        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("✏️ Post Header সেট করুন",  callback_data="set_post_header"))
        m.add(InlineKeyboardButton("✏️ Post Footer সেট করুন",  callback_data="set_post_footer"))
        m.row(
            InlineKeyboardButton("🗑️ Header মুছুন", callback_data="del_post_header"),
            InlineKeyboardButton("🗑️ Footer মুছুন", callback_data="del_post_footer")
        )
        m.add(InlineKeyboardButton(f"🔗 লিংক ফিল্টার: {lf}",  callback_data="toggle_link_filter"))
        m.add(InlineKeyboardButton(f"📝 টেক্সট ফিল্টার: {tf}", callback_data="toggle_text_filter"))
        m.add(_back("settings"))
        bot.edit_message_text(
            f"📝 <b>পোস্ট সেটিংস:</b>\n\n"
            f"<b>Post Header:</b>\n{ph}\n\n"
            f"<b>Post Footer:</b>\n{pf}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>🔗 লিংক ফিল্টার:</b> {lf}\n"
            f"<i>চালু → পোস্টের ক্যাপশন থেকে সব লিংক ও @username সরে যাবে, বাকি টেক্সট থাকবে।</i>\n\n"
            f"<b>📝 টেক্সট ফিল্টার:</b> {tf}\n"
            f"<i>চালু → পোস্টের পুরো ক্যাপশন সরে যাবে (শুধু মিডিয়া থাকবে)।</i>",
            chat_id, msg_id, reply_markup=m
        )

    elif data == "del_post_header":
        update_user(chat_id, {"post_header": ""})
        bot.answer_callback_query(call.id, "✅ Post Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_post_settings"; callback_handler(call)

    elif data == "del_post_footer":
        update_user(chat_id, {"post_footer": ""})
        bot.answer_callback_query(call.id, "✅ Post Footer মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_post_settings"; callback_handler(call)

    elif data == "toggle_link_filter":
        new = 1 - user.get("link_filter", 0)
        # দুটো একসাথে চালু থাকবে না
        update_user(chat_id, {"link_filter": new, "text_filter": 0 if new else user.get("text_filter",0)})
        st = "🟢 চালু" if new else "🔴 বন্ধ"
        bot.answer_callback_query(call.id, f"🔗 লিংক ফিল্টার: {st}", show_alert=True)
        call.data = "menu_post_settings"; callback_handler(call)

    elif data == "toggle_text_filter":
        new = 1 - user.get("text_filter", 0)
        update_user(chat_id, {"text_filter": new, "link_filter": 0 if new else user.get("link_filter",0)})
        st = "🟢 চালু" if new else "🔴 বন্ধ"
        bot.answer_callback_query(call.id, f"📝 টেক্সট ফিল্টার: {st}", show_alert=True)
        call.data = "menu_post_settings"; callback_handler(call)

    # ════════════════════════════
    #  📁 ফাইল সেটিংস (নতুন)
    # ════════════════════════════
    elif data == "menu_file_settings":
        u  = get_user(chat_id)
        fh = u.get("header","") or "<i>সেট করা হয়নি</i>"
        ff = u.get("footer","") or "<i>সেট করা হয়নি</i>"
        ad = u.get("auto_delete",0)
        ad_label = f"{ad} মিনিট" if ad>0 else "বন্ধ"

        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("✏️ File Header সেট করুন",  callback_data="set_file_header"))
        m.add(InlineKeyboardButton("✏️ File Footer সেট করুন",  callback_data="set_file_footer"))
        m.row(
            InlineKeyboardButton("🗑️ Header মুছুন", callback_data="del_file_header"),
            InlineKeyboardButton("🗑️ Footer মুছুন", callback_data="del_file_footer")
        )
        m.add(InlineKeyboardButton(f"⏳ Auto-Delete: {ad_label}", callback_data="set_autodelete"))
        m.add(_back("settings"))
        bot.edit_message_text(
            f"📁 <b>ফাইল সেটিংস:</b>\n\n"
            f"<b>File Header:</b>\n{fh}\n\n"
            f"<b>File Footer:</b>\n{ff}\n\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"<b>⏳ Auto-Delete:</b> {ad_label}\n"
            f"<i>ইউজারকে ফাইল পাঠানোর নির্দিষ্ট সময় পর ডিলিট হবে।</i>",
            chat_id, msg_id, reply_markup=m
        )

    elif data == "del_file_header":
        update_user(chat_id, {"header": ""})
        bot.answer_callback_query(call.id, "✅ File Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_file_settings"; callback_handler(call)

    elif data == "del_file_footer":
        update_user(chat_id, {"footer": ""})
        bot.answer_callback_query(call.id, "✅ File Footer মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_file_settings"; callback_handler(call)

    # ════════════════════════════
    #  🔒 Protect Content (নতুন)
    # ════════════════════════════
    elif data == "toggle_protect_content":
        new_val = toggle_setting("protect_content")
        st = "🟢 চালু" if new_val else "🔴 বন্ধ"
        bot.answer_callback_query(
            call.id,
            f"🔒 Protect Content: {st}\n"
            f"{'ইউজার ফাইল ফরোয়ার্ড/সেভ করতে পারবে না।' if new_val else 'ইউজার ফাইল ফরোয়ার্ড/সেভ করতে পারবে।'}",
            show_alert=True
        )
        call.data = "menu_advanced"; callback_handler(call)

    # ════════════════════════════
    #     FORCE SUBSCRIBE
    # ════════════════════════════
    elif data == "menu_force_sub":
        fcs = list(force_sub_col.find())
        m   = InlineKeyboardMarkup()
        for fc in fcs:
            ico = "🟢" if fc.get("status")=="on" else "🔴"
            m.row(
                InlineKeyboardButton(f"{ico} {fc['name']}", callback_data=f"tog_fs_{fc['fs_id']}"),
                InlineKeyboardButton("🗑️", callback_data=f"del_fs_{fc['fs_id']}")
            )
        m.add(InlineKeyboardButton("➕ চ্যানেল যোগ করুন", callback_data="add_force_sub"))
        m.add(_back("settings"))
        status = "চালু 🟢" if fcs else "কোনো চ্যানেল নেই 🔴"
        bot.edit_message_text(
            f"🔒 <b>ফোর্স সাবস্ক্রাইব</b>\nস্ট্যাটাস: {status}\n\n"
            "ইউজার এই চ্যানেলে Join না করলে ফাইল পাবে না।",
            chat_id, msg_id, reply_markup=m
        )

    elif data == "add_force_sub":
        update_step(chat_id, "wait_add_force_sub")
        bot.send_message(chat_id,
            "📢 ফরম্যাট:\n<code>নাম | চ্যানেল_আইডি | লিংক</code>\n\n"
            "উদাহরণ:\n<code>My Channel | -1001234567890 | https://t.me/mychannel</code>"
        )

    elif data.startswith("tog_fs_"):
        fs_id = data[7:]
        fc = force_sub_col.find_one({"fs_id": fs_id})
        if fc:
            force_sub_col.update_one({"fs_id": fs_id}, {"$set": {"status": "off" if fc.get("status")=="on" else "on"}})
            call.data = "menu_force_sub"; callback_handler(call)

    elif data.startswith("del_fs_"):
        force_sub_col.delete_one({"fs_id": data[7:]})
        bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_force_sub"; callback_handler(call)

    # ════════════════════════════
    #   AUTO POST CHANNELS
    # ════════════════════════════
    elif data == "menu_auto_post":
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton(f"📺 Ad ({auto_channels_col.count_documents({'type':'ad'})})",          callback_data="list_ch_ad"))
        m.add(InlineKeyboardButton(f"💎 Premium ({auto_channels_col.count_documents({'type':'premium'})})", callback_data="list_ch_premium"))
        m.add(InlineKeyboardButton(f"💾 Log ({auto_channels_col.count_documents({'type':'log'})})",         callback_data="list_ch_log"))
        m.add(_back("settings"))
        bot.edit_message_text("📤 <b>অটো পোস্ট চ্যানেল:</b>", chat_id, msg_id, reply_markup=m)

    elif data.startswith("list_ch_"):
        c_type = data[8:]
        chs    = list(auto_channels_col.find({"type": c_type}))
        m      = InlineKeyboardMarkup()
        for ch in chs:
            if not ch.get("ch_id"):
                cid = str(uuid.uuid4().hex)[:8]
                auto_channels_col.update_one({"_id": ch["_id"]}, {"$set": {"ch_id": cid, "status": "on"}})
                ch["ch_id"] = cid; ch["status"] = "on"
            ico = "🟢" if ch.get("status","on")=="on" else "🔴"
            m.row(
                InlineKeyboardButton(f"{ico} {ch.get('name','Unknown')}", callback_data=f"togch_{ch['ch_id']}"),
                InlineKeyboardButton("🗑️", callback_data=f"delch_{ch['ch_id']}")
            )
        m.add(InlineKeyboardButton("➕ নতুন চ্যানেল", callback_data=f"add_ch_{c_type}"))
        m.add(_back("menu_auto_post"))
        names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        bot.edit_message_text(f"<b>{names.get(c_type)} Channels</b>", chat_id, msg_id, reply_markup=m)

    elif data.startswith("togch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            auto_channels_col.update_one({"ch_id": ch['ch_id']}, {"$set": {"status": "off" if ch.get("status","on")=="on" else "on"}})
            call.data = f"list_ch_{ch['type']}"; callback_handler(call)

    elif data.startswith("delch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            auto_channels_col.delete_one({"ch_id": ch['ch_id']})
            bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)
            call.data = f"list_ch_{ch['type']}"; callback_handler(call)

    elif data.startswith("add_ch_"):
        update_step(chat_id, f"wait_add_{data[7:]}")
        bot.send_message(chat_id, "📝 ফরম্যাট:\n<code>নাম | চ্যানেল_আইডি</code>")

    # ════════════════════════════
    #  UPDATE CHANNELS & TUTORIALS
    # ════════════════════════════
    elif data == "menu_channels":
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("➕ নতুন চ্যানেল", callback_data="add_channel"))
        m.add(InlineKeyboardButton("🗑️ সব মুছুন",     callback_data="clear_channels"))
        m.add(_back("settings"))
        bot.edit_message_text("📢 <b>আপডেট চ্যানেল:</b>", chat_id, msg_id, reply_markup=m)

    elif data == "menu_tutorials":
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton("➕ নতুন ভিডিও",   callback_data="add_tutorial"))
        m.add(InlineKeyboardButton("🗑️ সব মুছুন",     callback_data="clear_tutorials"))
        m.add(_back("settings"))
        bot.edit_message_text("🎥 <b>টিউটোরিয়াল ভিডিও:</b>", chat_id, msg_id, reply_markup=m)

    elif data.startswith("clear_"):
        if data == "clear_channels":    channels_col.delete_many({})
        elif data == "clear_tutorials": tutorials_col.delete_many({})
        bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)

    # ════════════════════════════
    #      CUSTOM BUTTONS
    # ════════════════════════════
    elif data == "menu_custom_buttons":
        btns = user.get("custom_buttons",[])
        m    = InlineKeyboardMarkup()
        for i, btn in enumerate(btns):
            ico = "🟢" if btn.get("status")=="on" else "🔴"
            m.row(
                InlineKeyboardButton(f"{ico} {btn['name']}", callback_data=f"togbtn_{i}"),
                InlineKeyboardButton("🗑️", callback_data=f"delbtn_{i}")
            )
        m.add(InlineKeyboardButton("➕ নতুন বাটন", callback_data="add_custom_btn"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(f"🔘 <b>কাস্টম বাটন ({len(btns)}টি):</b>", chat_id, msg_id, reply_markup=m)

    elif data.startswith("togbtn_"):
        idx = int(data[7:]); btns = user.get("custom_buttons",[])
        if idx < len(btns):
            btns[idx]["status"] = "off" if btns[idx].get("status")=="on" else "on"
            update_user(chat_id, {"custom_buttons": btns})
            call.data = "menu_custom_buttons"; callback_handler(call)

    elif data.startswith("delbtn_"):
        idx = int(data[7:]); btns = user.get("custom_buttons",[])
        if idx < len(btns):
            btns.pop(idx); update_user(chat_id, {"custom_buttons": btns})
            bot.answer_callback_query(call.id, "✅ বাটন মুছে ফেলা হয়েছে!")
            call.data = "menu_custom_buttons"; callback_handler(call)

    elif data == "add_custom_btn":
        update_step(chat_id, "wait_custom_btn")
        bot.send_message(chat_id, "ফরম্যাট: <code>নাম | লিংক</code>")

    # ════════════════════════════
    #     ADVANCED SETTINGS
    # ════════════════════════════
    elif data == "menu_advanced":
        u   = get_user(chat_id)
        lk  = "🔗 লিংক: 🟢 ON"  if u.get("post_link_toggle",1)     else "🔗 লিংক: 🔴 OFF"
        tt  = "📽️ Tutorial: 🟢 ON" if u.get("post_tutorial_toggle",1) else "📽️ Tutorial: 🔴 OFF"
        rc  = u.get("link_repeat_count",1)
        pc  = get_setting("protect_content",0)
        pc_label = "🔒 Protect Content: 🟢 চালু" if pc else "🔒 Protect Content: 🔴 বন্ধ"

        m = InlineKeyboardMarkup()
        m.row(InlineKeyboardButton(lk, callback_data="toggle_post_link"),
              InlineKeyboardButton(tt, callback_data="toggle_tutorial_btn"))
        m.add(InlineKeyboardButton(f"🔄 লিংক রিপিট: {rc}x",  callback_data="set_link_repeat"))
        m.add(InlineKeyboardButton(pc_label,                  callback_data="toggle_protect_content"))
        m.add(InlineKeyboardButton("🔘 কাস্টম বাটন",          callback_data="menu_custom_buttons"))
        m.add(InlineKeyboardButton("👥 এডমিন ম্যানেজ",        callback_data="manage_admins"))
        m.add(InlineKeyboardButton("🚫 ব্যান ম্যানেজ",         callback_data="manage_bans"))
        m.row(
            InlineKeyboardButton("💾 ব্যাকআপ",  callback_data="cmd_backup"),
            InlineKeyboardButton("🔄 রিস্টোর",  callback_data="cmd_restore")
        )
        m.add(_back("settings"))
        bot.edit_message_text(
            f"⚙️ <b>অ্যাডভান্সড সেটিংস:</b>\n\n"
            f"🔒 <b>Protect Content:</b> {'🟢 চালু' if pc else '🔴 বন্ধ'}\n"
            f"<i>চালু → ইউজাররা ফাইল ফরোয়ার্ড ও ডাউনলোড/সেভ করতে পারবে না।</i>",
            chat_id, msg_id, reply_markup=m
        )

    elif data in ["toggle_post_link","toggle_tutorial_btn"]:
        k = "post_link_toggle" if data=="toggle_post_link" else "post_tutorial_toggle"
        new_val = 0 if user.get(k,1)==1 else 1
        update_user(chat_id, {k: new_val})
        call.data = "menu_advanced"; callback_handler(call)

    # ════════════════════════════
    #     ADMIN MANAGEMENT
    # ════════════════════════════
    elif data == "manage_admins":
        all_admins = list(admins_col.find())
        m = InlineKeyboardMarkup()
        for adm in all_admins:
            if adm['chat_id'] != str(MAIN_ADMIN_ID):
                m.add(InlineKeyboardButton(f"👤 {adm['chat_id']} [{adm.get('role','admin')}]", callback_data=f"rem_adm_{adm['chat_id']}"))
        m.add(InlineKeyboardButton("➕ এডমিন যোগ", callback_data="add_admin"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(f"👥 <b>এডমিন ({len(all_admins)} জন):</b>", chat_id, msg_id, reply_markup=m)

    elif data == "add_admin":
        update_step(chat_id, "wait_add_admin")
        bot.send_message(chat_id, "➕ নতুন এডমিনের Telegram ID দিন:")

    elif data.startswith("rem_adm_"):
        tid = data[8:]
        if tid == str(MAIN_ADMIN_ID):
            bot.answer_callback_query(call.id, "⛔ সুপার এডমিন সরানো যাবে না!", show_alert=True)
            return
        admins_col.delete_one({"chat_id": tid})
        bot.answer_callback_query(call.id, f"✅ {tid} সরানো হয়েছে!", show_alert=True)
        call.data = "manage_admins"; callback_handler(call)

    # ════════════════════════════
    #      BAN MANAGEMENT
    # ════════════════════════════
    elif data == "manage_bans":
        bans = list(banned_col.find())
        m    = InlineKeyboardMarkup()
        for bu in bans[:10]:
            m.add(InlineKeyboardButton(f"🚫 {bu['chat_id']}", callback_data=f"unban_{bu['chat_id']}"))
        m.add(InlineKeyboardButton("➕ ব্যান করুন", callback_data="add_ban"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(
            f"🚫 <b>ব্যান লিস্ট ({len(bans)} জন):</b>\nআনব্যান করতে আইডিতে ক্লিক করুন।",
            chat_id, msg_id, reply_markup=m
        )

    elif data == "add_ban":
        update_step(chat_id, "wait_ban_user")
        bot.send_message(chat_id, "🚫 ব্যান করতে Telegram ID দিন (কারণও লিখতে পারেন):\n<code>1234567890 কারণ</code>")

    elif data.startswith("unban_"):
        tid = data[6:]
        banned_col.delete_one({"chat_id": tid})
        bot.answer_callback_query(call.id, f"✅ {tid} আনব্যান হয়েছে!", show_alert=True)
        call.data = "manage_bans"; callback_handler(call)

    # ════════════════════════════
    #     BACKUP & RESTORE
    # ════════════════════════════
    elif data == "cmd_backup":
        bot.answer_callback_query(call.id, "⏳ ব্যাকআপ তৈরি হচ্ছে...")
        bot.send_message(chat_id, "⏳ ডাটাবেস ব্যাকআপ তৈরি করা হচ্ছে...")
        backup = {
            "version": BOT_VERSION,
            "backup_date": datetime.now().isoformat(),
            "users":         list(users_col.find({},         {"_id":0})),
            "files":         list(files_col.find({},         {"_id":0})),
            "tutorials":     list(tutorials_col.find({},     {"_id":0})),
            "channels":      list(channels_col.find({},      {"_id":0})),
            "auto_channels": list(auto_channels_col.find({}, {"_id":0})),
            "force_sub":     list(force_sub_col.find({},     {"_id":0})),
            "settings":      list(settings_col.find({},      {"_id":0})),
        }
        try:
            with open("backup.json","w",encoding="utf-8") as f:
                json.dump(backup, f, ensure_ascii=False, indent=2, default=str)
            with open("backup.json","rb") as f:
                bot.send_document(
                    chat_id, f,
                    caption=f"✅ <b>ডাটাবেস ব্যাকআপ সম্পন্ন!</b>\n"
                            f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}\n"
                            f"👥 ইউজার: {len(backup['users'])}\n"
                            f"📁 ফাইল: {len(backup['files'])}\n"
                            f"🔖 v{BOT_VERSION}"
                )
        except Exception as e:
            bot.send_message(chat_id, f"❌ ব্যাকআপ ব্যর্থ: <code>{e}</code>")
        finally:
            if os.path.exists("backup.json"): os.remove("backup.json")

    elif data == "cmd_restore":
        update_step(chat_id, "wait_restore")
        bot.send_message(chat_id, "🔄 <code>backup.json</code> ফাইলটি দিন।")

    # ════════════════════════════
    #      STEP TRIGGERS
    # ════════════════════════════
    _step_map = {
        "set_post_header": ("wait_post_header", "📝 পোস্টের <b>Header</b> লিখে পাঠান:"),
        "set_post_footer": ("wait_post_footer", "📝 পোস্টের <b>Footer</b> লিখে পাঠান:"),
        "set_file_header": ("wait_file_header", "📝 ফাইলের <b>Header</b> লিখে পাঠান:"),
        "set_file_footer": ("wait_file_footer", "📝 ফাইলের <b>Footer</b> লিখে পাঠান:"),
        "add_channel":     ("wait_add_channel", "📢 ফরম্যাট: <code>নাম | লিংক</code>"),
        "add_tutorial":    ("wait_add_tutorial","📽️ ফরম্যাট: <code>নাম | লিংক</code>"),
        "set_autodelete":  ("wait_autodelete",  "⏳ Auto-Delete সময় লিখুন (মিনিটে)। বন্ধ করতে 0।"),
        "set_link_repeat": ("wait_link_repeat", "🔄 লিংক কতবার রিপিট হবে? (১–৫)"),
    }
    if data in _step_map:
        step_val, prompt = _step_map[data]
        update_step(chat_id, step_val)
        bot.send_message(chat_id, prompt, parse_mode="HTML")

    elif data == "help_menu":
        m = InlineKeyboardMarkup()
        m.add(_back("main_menu"))
        bot.edit_message_text(
            f"ℹ️ <b>সাহায্য — Bot v{BOT_VERSION}</b>\n\n"
            "📝 <b>পোস্ট সেটিংস:</b>\n"
            "  • Header/Footer সেট, এডিট, ডিলিট\n"
            "  • লিংক ফিল্টার: লিংক সরিয়ে টেক্সট রাখে\n"
            "  • টেক্সট ফিল্টার: পুরো ক্যাপশন সরায়\n\n"
            "📁 <b>ফাইল সেটিংস:</b>\n"
            "  • Header/Footer সেট, এডিট, ডিলিট\n"
            "  • Auto-Delete সময় নির্ধারণ\n\n"
            "🔒 <b>Protect Content:</b> ফরোয়ার্ড/সেভ বন্ধ\n"
            "🔒 <b>Force Subscribe:</b> চ্যানেল Join বাধ্যতামূলক\n"
            "📦 <b>Batch Upload:</b> একাধিক ফাইল একসাথে\n"
            "📡 <b>Broadcast:</b> সবাইকে বা সক্রিয়দের মেসেজ\n"
            "💾 <b>Backup/Restore:</b> ডাটাবেস সুরক্ষিত রাখুন\n\n"
            "<b>Commands:</b>\n"
            "/stats — বট স্ট্যাটস\n"
            "/ban ID কারণ — ব্যান করুন\n"
            "/unban ID — আনব্যান করুন\n"
            "/reply ID মেসেজ — ইউজারকে উত্তর দিন",
            chat_id, msg_id, reply_markup=m
        )

# ═══════════════════════════════════════════════════════════
#                  মেসেজ হ্যান্ডলার
# ═══════════════════════════════════════════════════════════
@bot.message_handler(content_types=['text','photo','document','video','audio'])
def handle_message(message):
    chat_id      = str(message.chat.id)
    text         = message.text or message.caption or ""
    user         = get_user(chat_id)
    admin_status = is_admin(chat_id)

    if is_banned(chat_id) and not admin_status:
        try: bot.send_message(chat_id, "🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        except: pass
        return

    # ══ /start ══
    if text.startswith("/start"):
        parts = text.split(" ")
        if len(parts) > 1:
            file_key = parts[1]
            joined, not_joined = check_force_subscribe(chat_id)
            if not joined:
                send_force_sub_msg(chat_id, not_joined, file_key)
                return
            _deliver_files(chat_id, file_key, user)
        else:
            if admin_status:
                s = get_bot_stats()
                bot.send_message(
                    chat_id,
                    f"👋 <b>এডমিন প্যানেলে স্বাগতম!</b>\n\n"
                    f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
                    f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
                    f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>",
                    reply_markup=_main_menu_markup()
                )
            else:
                m = InlineKeyboardMarkup()
                for tut in tutorials_col.find():
                    m.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
                for ch in channels_col.find():
                    m.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                bot.send_message(chat_id, "👋 <b>স্বাগতম!</b>", reply_markup=m if m.keyboard else None)
        return

    # ══ /stats ══
    if text == "/stats" and admin_status:
        s = get_bot_stats()
        bot.send_message(
            chat_id,
            f"📊 <b>স্ট্যাটিস্টিক্স</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"📥 আজ ডাউনলোড: <b>{s['downloads_today']}</b>\n"
            f"📤 আজ আপলোড: <b>{s['uploads_today']}</b>"
        )
        return

    # ══ /ban ══
    if text.startswith("/ban ") and admin_status:
        pts  = text.split(" ", 2)
        tid  = pts[1]
        rsn  = pts[2] if len(pts)>2 else "কারণ উল্লেখ নেই"
        if tid == str(MAIN_ADMIN_ID):
            bot.send_message(chat_id, "⛔ সুপার এডমিন ব্যান করা যাবে না!"); return
        if not banned_col.find_one({"chat_id": tid}):
            banned_col.insert_one({"chat_id": tid, "reason": rsn, "banned_at": datetime.now().isoformat()})
            bot.send_message(chat_id, f"🚫 <code>{tid}</code> ব্যান হয়েছে।\nকারণ: {rsn}")
        else:
            bot.send_message(chat_id, f"⚠️ <code>{tid}</code> আগেই ব্যান।")
        return

    # ══ /unban ══
    if text.startswith("/unban ") and admin_status:
        tid = text.split()[1]
        r   = banned_col.delete_one({"chat_id": tid})
        bot.send_message(chat_id, f"✅ <code>{tid}</code> আনব্যান হয়েছে!" if r.deleted_count else "⚠️ ব্যান লিস্টে নেই।")
        return

    # ══ /reply ══
    if text.startswith("/reply ") and admin_status:
        pts = text.split(" ", 2)
        if len(pts)==3:
            _, uid, msg_txt = pts
            try:
                bot.send_message(uid, f"👨‍💻 <b>এডমিনের উত্তর:</b>\n\n{msg_txt}")
                bot.send_message(chat_id, "✅ মেসেজ পাঠানো হয়েছে!")
            except:
                bot.send_message(chat_id, "❌ মেসেজ পাঠানো যায়নি।")
        return

    # ══ /cancel ══
    if text == "/cancel":
        update_step(chat_id, "none")
        bot.send_message(chat_id, "❌ কাজ বাতিল করা হয়েছে।")
        return

    # ══ নন-এডমিন মেসেজ ফরোয়ার্ড ══
    if not admin_status:
        try:
            bot.forward_message(MAIN_ADMIN_ID, chat_id, message.message_id)
            bot.send_message(MAIN_ADMIN_ID,
                f"📩 নতুন মেসেজ\n👤 ID: <code>{chat_id}</code>\n"
                f"💬 রিপ্লাই: <code>/reply {chat_id} মেসেজ</code>"
            )
            bot.send_message(chat_id, "✅ এডমিনের কাছে পাঠানো হয়েছে।")
        except: pass
        return

    step = user.get("step","none")

    # ══ Broadcast ══
    if step.startswith("wait_broadcast"):
        tgt = step.replace("wait_broadcast_","") if "_" in step else "all"
        update_step(chat_id, "none")
        bot.send_message(chat_id, "⏳ ব্রডকাস্ট background-এ শুরু হচ্ছে...")
        threading.Thread(target=broadcast_worker, daemon=True,
                         args=(chat_id, chat_id, message.message_id, tgt)).start()
        return

    # ══ Force Sub যোগ ══
    if step == "wait_add_force_sub":
        if "|" in text:
            pts = [p.strip() for p in text.split("|")]
            if len(pts) >= 3:
                force_sub_col.insert_one({
                    "fs_id": str(uuid.uuid4().hex)[:8],
                    "name": pts[0], "channel_id": pts[1], "url": pts[2], "status": "on"
                })
                update_step(chat_id, "none")
                bot.send_message(chat_id, f"✅ Force Subscribe চ্যানেল যোগ: <b>{pts[0]}</b>")
            else:
                bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        return

    # ══ Auto Channels ══
    if step in ["wait_add_ad","wait_add_premium","wait_add_log"]:
        if "|" in text:
            cn, ci = [p.strip() for p in text.split("|",1)]
            ct = step.split("_")[2]
            auto_channels_col.insert_one({
                "ch_id": str(uuid.uuid4().hex)[:8], "type": ct,
                "name": cn, "channel_id": ci, "status": "on"
            })
            update_step(chat_id,"none")
            bot.send_message(chat_id, f"✅ চ্যানেল যোগ: <b>{cn}</b>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>")
        return

    # ══ Custom Button ══
    if step == "wait_custom_btn":
        if "|" in text:
            bn, bl = [p.strip() for p in text.split("|",1)]
            btns = user.get("custom_buttons",[])
            btns.append({"name":bn,"url":bl,"status":"on"})
            update_user(chat_id,{"custom_buttons":btns,"step":"none"})
            bot.send_message(chat_id, f"✅ বাটন যোগ: <b>{bn}</b>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | লিংক</code>")
        return

    # ══ Ban (step) ══
    if step == "wait_ban_user":
        pts  = text.strip().split(" ",1)
        tid  = pts[0]
        rsn  = pts[1] if len(pts)>1 else "এডমিন কর্তৃক ব্যান"
        if tid == str(MAIN_ADMIN_ID):
            bot.send_message(chat_id,"⛔ সুপার এডমিন ব্যান করা যাবে না!")
        elif not banned_col.find_one({"chat_id":tid}):
            banned_col.insert_one({"chat_id":tid,"reason":rsn,"banned_at":datetime.now().isoformat()})
            bot.send_message(chat_id,f"🚫 <code>{tid}</code> ব্যান হয়েছে!")
        else:
            bot.send_message(chat_id,"⚠️ আগেই ব্যান।")
        update_step(chat_id,"none")
        return

    # ══ Admin Add ══
    if step == "wait_add_admin":
        tid = text.strip()
        if not admins_col.find_one({"chat_id":tid}):
            admins_col.insert_one({"chat_id":tid,"role":"admin","added_at":datetime.now().isoformat()})
            bot.send_message(chat_id,f"✅ <code>{tid}</code> এডমিন হয়েছে!")
            try: bot.send_message(tid,"🎉 আপনাকে এডমিন করা হয়েছে!")
            except: pass
        else:
            bot.send_message(chat_id,"⚠️ এই আইডি আগেই এডমিন।")
        update_step(chat_id,"none")
        return

    # ══ Text Settings ══
    txt_steps = {
        "wait_post_header": ("post_header","📝 Post Header"),
        "wait_post_footer": ("post_footer","📝 Post Footer"),
        "wait_file_header": ("header",     "📁 File Header"),
        "wait_file_footer": ("footer",     "📁 File Footer"),
    }
    if step in txt_steps and message.text:
        k, label = txt_steps[step]
        update_user(chat_id,{k:text,"step":"none"})
        bot.send_message(chat_id,f"✅ <b>{label}</b> সেট হয়েছে!")
        return

    if step=="wait_autodelete" and text.isdigit():
        v = int(text)
        update_user(chat_id,{"auto_delete":v,"step":"none"})
        bot.send_message(chat_id, f"✅ Auto-Delete <b>{v} মিনিট</b> সেট!" if v>0 else "✅ Auto-Delete <b>বন্ধ</b>!")
        return

    if step=="wait_link_repeat" and text.isdigit():
        v = max(1, min(int(text),5))
        update_user(chat_id,{"link_repeat_count":v,"step":"none"})
        bot.send_message(chat_id,f"✅ লিংক রিপিট <b>{v}x</b> সেট!")
        return

    if step=="wait_add_channel" and "|" in text:
        n,l = [p.strip() for p in text.split("|",1)]
        channels_col.insert_one({"name":n,"url":l})
        update_step(chat_id,"none")
        bot.send_message(chat_id,f"✅ চ্যানেল যোগ: <b>{n}</b>")
        return

    if step=="wait_add_tutorial" and "|" in text:
        n,l = [p.strip() for p in text.split("|",1)]
        tutorials_col.insert_one({"name":n,"url":l})
        update_step(chat_id,"none")
        bot.send_message(chat_id,f"✅ টিউটোরিয়াল যোগ: <b>{n}</b>")
        return

    # ══ Restore ══
    if step=="wait_restore" and message.document:
        try:
            bot.send_message(chat_id,"⏳ রিস্টোর হচ্ছে...")
            fi   = bot.get_file(message.document.file_id)
            data = json.loads(bot.download_file(fi.file_path))
            if "users" in data:
                for u in data["users"]:
                    if not users_col.find_one({"chat_id":u.get("chat_id")}):
                        users_col.insert_one(u)
            if "files" in data:
                for f_ in data["files"]:
                    if not files_col.find_one({"file_key":f_.get("file_key")}):
                        files_col.insert_one(f_)
            if "auto_channels" in data and data["auto_channels"]:
                auto_channels_col.insert_many(data["auto_channels"])
            if "force_sub" in data and data["force_sub"]:
                force_sub_col.insert_many(data["force_sub"])
            if "settings" in data and data["settings"]:
                for s_ in data["settings"]:
                    settings_col.update_one({"key":s_.get("key")},{"$set":s_},upsert=True)
            update_step(chat_id,"none")
            bot.send_message(chat_id,"✅ <b>ডাটাবেস সফলভাবে রিস্টোর হয়েছে!</b>")
        except Exception as e:
            bot.send_message(chat_id,f"❌ রিস্টোর ব্যর্থ!\n<code>{e}</code>")
        return

    # ══ Thumbnail ══
    if step=="wait_thumbnail":
        if text=="/skip":
            update_user(chat_id,{"step":"none","pending_link":"","pending_short_link":""})
            bot.send_message(chat_id,"✅ থাম্বনেইল স্কিপ করা হয়েছে।")
            return
        if message.video:
            update_user(chat_id,{"temp_media_id":message.video.file_id,"temp_media_type":"video"})
            m = InlineKeyboardMarkup()
            m.row(
                InlineKeyboardButton("✅ Confirm",callback_data="confirm_vid_thumb"),
                InlineKeyboardButton("❌ বাতিল",  callback_data="cancel_vid_thumb")
            )
            bot.send_message(chat_id,"🎥 এই ভিডিওটি পোস্ট করবেন?",reply_markup=m)
            return
        elif message.photo:
            execute_channel_post(chat_id,user,"photo",message.photo[-1].file_id)
            return
        else:
            bot.send_message(chat_id,"⚠️ ছবি বা ভিডিও দিন অথবা /skip লিখুন।")
            return

    # ══ File Upload ══
    file_id, f_type = None, None
    if message.document:                            file_id,f_type = message.document.file_id,"document"
    elif message.video and step!="wait_thumbnail":  file_id,f_type = message.video.file_id,"video"
    elif message.audio:                             file_id,f_type = message.audio.file_id,"audio"
    elif message.photo and step!="wait_thumbnail":  file_id,f_type = message.photo[-1].file_id,"photo"

    if file_id:
        uid = str(uuid.uuid4().hex)[:10]
        log_chat = ""; log_msg_id = ""

        log_ch = auto_channels_col.find_one({"type":"log","status":"on"})
        if log_ch:
            try:
                cap_log = f"💾 <b>Backup</b>\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n🔑 <code>{uid}</code>"
                res = None
                if f_type=="document": res = bot.send_document(log_ch['channel_id'],file_id,caption=cap_log)
                elif f_type=="video":  res = bot.send_video(log_ch['channel_id'],file_id,caption=cap_log)
                elif f_type=="photo":  res = bot.send_photo(log_ch['channel_id'],file_id,caption=cap_log)
                elif f_type=="audio":  res = bot.send_audio(log_ch['channel_id'],file_id,caption=cap_log)
                if res: log_chat,log_msg_id = log_ch['channel_id'],res.message_id
            except Exception as e: logger.warning(f"Log backup: {e}")

        if step=="wait_batch":
            batch_id = user.get("batch_id")
            files_col.insert_one({
                "file_key":uid,"file_id":file_id,"type":f_type,
                "uploader":chat_id,"batch_id":batch_id,
                "log_chat_id":log_chat,"log_msg_id":log_msg_id,
                "uploaded_at":datetime.now().isoformat()
            })
            cnt = files_col.count_documents({"batch_id":batch_id})
            m   = InlineKeyboardMarkup()
            m.add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)",callback_data="finish_batch"))
            bot.send_message(chat_id,f"✅ <b>#{cnt} ফাইল ব্যাচে যোগ হয়েছে!</b>",reply_markup=m)
        else:
            files_col.insert_one({
                "file_key":uid,"file_id":file_id,"type":f_type,
                "uploader":chat_id,"batch_id":"",
                "log_chat_id":log_chat,"log_msg_id":log_msg_id,
                "uploaded_at":datetime.now().isoformat()
            })
            dl = f"https://t.me/{BOT_USERNAME}?start={uid}"
            sl = get_short_link(dl)
            update_user(chat_id,{
                "step":"wait_thumbnail","pending_link":dl,"pending_short_link":sl,
                "total_uploads": user.get("total_uploads",0)+1
            })
            _inc_stat("uploads")
            bot.send_message(
                chat_id,
                f"✅ <b>ফাইল সেভ হয়েছে!</b>\n\n"
                f"💎 ডাইরেক্ট:\n<code>{dl}</code>\n\n"
                f"📺 শর্ট:\n<code>{sl}</code>\n\n"
                f"🖼️ থাম্বনেইল দিন বা /skip লিখুন।",
                disable_web_page_preview=True
            )

# ═══════════════════════════════════════════════════════════
#                Render / Flask Server
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/')
def home():
    s = get_bot_stats()
    return {"status":"running","version":BOT_VERSION,"total_users":s['total_users'],"total_files":s['total_files']}

@app.route('/health')
def health():
    return {"status":"ok","timestamp":datetime.now().isoformat()}

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

# ═══════════════════════════════════════════════════════════
#                        মেইন
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info(f"🚀 Premium Bot v{BOT_VERSION} Starting...")
    threading.Thread(target=run_server, daemon=True).start()
    logger.info("✅ Web server started")
    while True:
        try:
            logger.info("🤖 Polling started...")
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
