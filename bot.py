"""
╔══════════════════════════════════════════════════════════╗
║          🚀 PREMIUM FILE SHARE BOT v3.0                  ║
║          Advanced Telegram Bot with MongoDB              ║
║          Features: Auto-Post, Batch, Analytics,          ║
║          Force Subscribe, Watermark, Stats & More        ║
╚══════════════════════════════════════════════════════════╝
"""

import os
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
#                  কনফিগারেশন (Environment Variables)
# ═══════════════════════════════════════════════════════════
BOT_TOKEN        = os.environ.get("BOT_TOKEN", "আপনার_বট_টোকেন_এখানে_দিন")
BOT_USERNAME     = os.environ.get("BOT_USERNAME", "YourBotUsername")
MAIN_ADMIN_ID    = os.environ.get("MAIN_ADMIN_ID", "5991854507")
TERABOX_TOKEN    = os.environ.get("TERABOX_TOKEN", "71b16be6b48d01937bfe7d2c3043cbc0b6363c82")
MONGO_URL        = os.environ.get("MONGO_URL", "আপনার_MongoDB_URL_এখানে_দিন")
BOT_VERSION      = "3.0.0"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ═══════════════════════════════════════════════════════════
#                   MongoDB ডাটাবেস সেটআপ
# ═══════════════════════════════════════════════════════════
client = MongoClient(MONGO_URL)
db = client['telegram_bot_db']

users_col         = db['users']
files_col         = db['files']
queue_col         = db['queue']
admins_col        = db['admins']
channels_col      = db['update_channels']
tutorials_col     = db['tutorials']
auto_channels_col = db['auto_channels']
stats_col         = db['bot_stats']         # নতুন: বট স্ট্যাটিস্টিক্স
banned_col        = db['banned_users']      # নতুন: ব্যান সিস্টেম
force_sub_col     = db['force_subscribe']   # নতুন: ফোর্স সাবস্ক্রাইব
captions_col      = db['saved_captions']    # নতুন: ক্যাপশন টেমপ্লেট

# ইন্ডেক্স তৈরি (পারফরম্যান্স বুস্ট)
users_col.create_index("chat_id", unique=True, background=True)
files_col.create_index("file_key", background=True)
files_col.create_index("batch_id", background=True)
queue_col.create_index("delete_at", background=True)

# মূল এডমিন ডাটাবেসে যুক্ত করা
if not admins_col.find_one({"chat_id": str(MAIN_ADMIN_ID)}):
    admins_col.insert_one({"chat_id": str(MAIN_ADMIN_ID), "role": "super_admin", "added_at": datetime.now().isoformat()})

# ═══════════════════════════════════════════════════════════
#                  ডাটাবেস হেল্পার ফাংশন
# ═══════════════════════════════════════════════════════════
def get_user(chat_id):
    chat_id = str(chat_id)
    user = users_col.find_one({"chat_id": chat_id})
    if not user:
        user = {
            "chat_id": chat_id,
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
            "joined_at": datetime.now().isoformat(),
            "last_active": datetime.now().isoformat(),
            "total_downloads": 0,
            "total_uploads": 0,
            "language": "bn",
        }
        users_col.insert_one(user)
        _update_daily_stats("new_users", 1)
    else:
        # নতুন ফিল্ড যোগ করা (backward compatibility)
        updates = {}
        defaults = {
            "custom_buttons": [], "temp_media_id": "", "temp_media_type": "",
            "total_downloads": 0, "total_uploads": 0, "language": "bn",
            "last_active": datetime.now().isoformat(),
        }
        for k, v in defaults.items():
            if k not in user:
                updates[k] = v
        if updates:
            users_col.update_one({"chat_id": chat_id}, {"$set": updates})
            user.update(updates)

    # last_active আপডেট
    users_col.update_one({"chat_id": chat_id}, {"$set": {"last_active": datetime.now().isoformat()}})
    return user

def update_user(chat_id, updates):
    users_col.update_one({"chat_id": str(chat_id)}, {"$set": updates})

def update_step(chat_id, step):
    update_user(chat_id, {"step": step})

def is_admin(chat_id):
    return bool(admins_col.find_one({"chat_id": str(chat_id)}))

def is_banned(chat_id):
    return bool(banned_col.find_one({"chat_id": str(chat_id)}))

def get_total_users():
    return users_col.count_documents({})

def get_active_users_today():
    today = datetime.now().strftime("%Y-%m-%d")
    return users_col.count_documents({"last_active": {"$regex": f"^{today}"}})

# ═══════════════════════════════════════════════════════════
#                   স্ট্যাটিস্টিক্স সিস্টেম (নতুন)
# ═══════════════════════════════════════════════════════════
def _update_daily_stats(field, increment=1):
    today = datetime.now().strftime("%Y-%m-%d")
    stats_col.update_one(
        {"date": today},
        {"$inc": {field: increment}},
        upsert=True
    )

def get_bot_stats():
    total_users   = users_col.count_documents({})
    total_files   = files_col.count_documents({})
    total_admins  = admins_col.count_documents({})
    total_banned  = banned_col.count_documents({})
    active_today  = get_active_users_today()

    today = datetime.now().strftime("%Y-%m-%d")
    today_stats = stats_col.find_one({"date": today}) or {}
    dl_today = today_stats.get("downloads", 0)
    ul_today = today_stats.get("uploads", 0)

    return {
        "total_users": total_users,
        "total_files": total_files,
        "total_admins": total_admins,
        "total_banned": total_banned,
        "active_today": active_today,
        "downloads_today": dl_today,
        "uploads_today": ul_today,
    }

# ═══════════════════════════════════════════════════════════
#                ফোর্স সাবস্ক্রাইব সিস্টেম (নতুন)
# ═══════════════════════════════════════════════════════════
def check_force_subscribe(chat_id):
    """ইউজার সকল ফোর্স চ্যানেলে জয়েন করেছে কিনা চেক করে"""
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

def send_force_subscribe_message(chat_id, not_joined_channels, file_key=None):
    markup = InlineKeyboardMarkup()
    for ch in not_joined_channels:
        markup.add(InlineKeyboardButton(f"📢 {ch['name']} - Join করুন", url=ch['url']))

    check_btn_data = f"check_sub_{file_key}" if file_key else "check_sub_none"
    markup.add(InlineKeyboardButton("✅ Join করেছি - Check করুন", callback_data=check_btn_data))

    bot.send_message(
        chat_id,
        "🔒 <b>ফাইল পেতে নিচের চ্যানেলগুলোতে Join করুন!</b>\n\n"
        "Join করার পর <b>✅ Join করেছি</b> বাটনে ক্লিক করুন।",
        reply_markup=markup
    )

# ═══════════════════════════════════════════════════════════
#                  অটো-ডিলিট সিস্টেম (উন্নত)
# ═══════════════════════════════════════════════════════════
def auto_delete_worker():
    while True:
        try:
            now = int(time.time())
            expired_items = list(queue_col.find({"delete_at": {"$lte": now}}))
            if expired_items:
                ch_list = list(channels_col.find())
                for item in expired_items:
                    try:
                        bot.delete_message(item['chat_id'], item['message_id'])
                        markup = InlineKeyboardMarkup()
                        for ch in ch_list:
                            markup.add(InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch['url']))
                        bot.send_message(
                            item['chat_id'],
                            "⚠️ <b>সময় শেষ! ফাইল মুছে গেছে।</b>\n\n"
                            "🔁 আবার পেতে লিংকে ক্লিক করুন অথবা আমাদের চ্যানেলে যোগ দিন।",
                            reply_markup=markup if ch_list else None
                        )
                    except Exception as e:
                        logger.warning(f"Auto-delete error for msg {item.get('message_id')}: {e}")
                    finally:
                        queue_col.delete_one({"_id": item["_id"]})
        except Exception as e:
            logger.error(f"Auto delete worker error: {e}")
        time.sleep(10)

threading.Thread(target=auto_delete_worker, daemon=True).start()

# ═══════════════════════════════════════════════════════════
#               ব্রডকাস্ট ওয়ার্কার (নতুন - থ্রেডেড)
# ═══════════════════════════════════════════════════════════
def broadcast_worker(admin_chat_id, from_chat_id, message_id, target="all"):
    """Background-এ broadcast চালায়, বট ব্লক হয় না"""
    count_success = 0
    count_fail = 0
    query = {}
    if target == "active":
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        query = {"last_active": {"$gte": yesterday}}

    all_users = list(users_col.find(query, {"chat_id": 1}))
    total = len(all_users)

    try:
        bot.send_message(admin_chat_id, f"📡 <b>ব্রডকাস্ট শুরু হয়েছে!</b>\nমোট: <b>{total}</b> জন")
    except: pass

    for i, u in enumerate(all_users):
        try:
            bot.copy_message(u['chat_id'], from_chat_id, message_id)
            count_success += 1
            time.sleep(0.05)
        except:
            count_fail += 1

        # প্রতি ১০০ জনে একটি প্রগ্রেস আপডেট
        if (i + 1) % 100 == 0:
            try:
                bot.send_message(
                    admin_chat_id,
                    f"📊 প্রগ্রেস: {i+1}/{total} | ✅ {count_success} | ❌ {count_fail}"
                )
            except: pass

    try:
        bot.send_message(
            admin_chat_id,
            f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n\n"
            f"📨 মোট: <b>{total}</b>\n"
            f"✅ সফল: <b>{count_success}</b>\n"
            f"❌ ব্যর্থ: <b>{count_fail}</b>"
        )
    except: pass

# ═══════════════════════════════════════════════════════════
#               Terabox Link Shortener (উন্নত)
# ═══════════════════════════════════════════════════════════
def get_terabox_short_link(long_url):
    try:
        api_url = f"https://teraboxlinks.com/api?api={TERABOX_TOKEN}&url={quote(long_url)}"
        res = requests.get(api_url, timeout=8).json()
        if res and res.get("status") != "error":
            short = res.get("shortenedUrl")
            if short:
                return short
    except Exception as e:
        logger.warning(f"Short link error: {e}")
    return long_url

# ═══════════════════════════════════════════════════════════
#          চ্যানেলে পোস্ট করার মেইন ফাংশন (উন্নত)
# ═══════════════════════════════════════════════════════════
def execute_channel_post(chat_id, user, media_type, media_id):
    d_link = user.get("pending_link", "")
    s_link = user.get("pending_short_link", "")

    p_head = f"{user.get('post_header', '')}\n\n" if user.get('post_header') else ""
    p_foot = f"\n\n{user.get('post_footer', '')}" if user.get('post_footer') else ""

    ad_caption = prem_caption = f"{p_head}{p_foot}".strip()
    ad_markup, prem_markup = InlineKeyboardMarkup(), InlineKeyboardMarkup()

    if user.get("post_link_toggle", 1) == 1:
        repeats = max(1, min(user.get("link_repeat_count", 1), 5))  # সর্বোচ্চ ৫ বার

        ad_links_text   = "\n".join([s_link] * repeats)
        prem_links_text = "\n".join([d_link] * repeats)

        now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
        ad_text   = f"🔗 <b>Download Link:</b>\n{ad_links_text}\n\n<i>🕐 Posted: {now_str}</i>"
        prem_text = f"🔗 <b>Direct Download (No Ads):</b>\n{prem_links_text}\n\n<i>🕐 Posted: {now_str}</i>"

        ad_caption   = f"{p_head}{ad_text}{p_foot}".strip()
        prem_caption = f"{p_head}{prem_text}{p_foot}".strip()

        if user.get("post_tutorial_toggle", 1) == 1:
            for tut in tutorials_col.find():
                ad_markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))

        custom_buttons = user.get("custom_buttons", [])
        for btn in custom_buttons:
            if btn.get("status") == "on":
                ad_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))
                prem_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))

        ad_markup.add(InlineKeyboardButton("📥 ডাউনলোড করুন", url=s_link))
        prem_markup.add(InlineKeyboardButton("💎 ডাইরেক্ট ডাউনলোড", url=d_link))

    post_count = 0
    for ch in auto_channels_col.find({"type": "ad"}):
        if ch.get("status", "on") == "on":
            try:
                if media_type == 'photo':
                    bot.send_photo(ch['channel_id'], media_id, caption=ad_caption, reply_markup=ad_markup)
                elif media_type == 'video':
                    bot.send_video(ch['channel_id'], media_id, caption=ad_caption, reply_markup=ad_markup)
                post_count += 1
            except Exception as e:
                logger.warning(f"Ad channel post error ({ch.get('name')}): {e}")

    for ch in auto_channels_col.find({"type": "premium"}):
        if ch.get("status", "on") == "on":
            try:
                if media_type == 'photo':
                    bot.send_photo(ch['channel_id'], media_id, caption=prem_caption, reply_markup=prem_markup)
                elif media_type == 'video':
                    bot.send_video(ch['channel_id'], media_id, caption=prem_caption, reply_markup=prem_markup)
                post_count += 1
            except Exception as e:
                logger.warning(f"Premium channel post error ({ch.get('name')}): {e}")

    for ch in auto_channels_col.find({"type": "log"}):
        if ch.get("status", "on") == "on":
            try:
                caption_log = f"💾 <b>Media Backup</b>\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                if media_type == 'photo':
                    bot.send_photo(ch['channel_id'], media_id, caption=caption_log)
                elif media_type == 'video':
                    bot.send_video(ch['channel_id'], media_id, caption=caption_log)
            except Exception as e:
                logger.warning(f"Log channel post error: {e}")

    _update_daily_stats("uploads", 1)

    status_msg = (
        f"✅ <b>পোস্ট সম্পন্ন!</b>\n"
        f"📤 <b>{post_count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n"
        f"🕐 {datetime.now().strftime('%I:%M %p')}"
    )
    bot.send_message(chat_id, status_msg)
    update_user(chat_id, {
        "step": "none", "pending_link": "", "pending_short_link": "",
        "temp_media_id": "", "temp_media_type": ""
    })

# ═══════════════════════════════════════════════════════════
#                কলব্যাক কোয়েরি হ্যান্ডলার
# ═══════════════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = str(call.message.chat.id)
    msg_id  = call.message.message_id
    data    = call.data
    user    = get_user(chat_id)

    # ব্যান চেক
    if is_banned(chat_id):
        bot.answer_callback_query(call.id, "🚫 আপনি ব্যান করা হয়েছেন!", show_alert=True)
        return

    # ফোর্স সাবস্ক্রাইব চেক (check_sub_ কলব্যাক)
    if data.startswith("check_sub_"):
        file_key = data.replace("check_sub_", "")
        joined, not_joined = check_force_subscribe(chat_id)
        if joined:
            bot.answer_callback_query(call.id, "✅ ধন্যবাদ! Join নিশ্চিত হয়েছে।", show_alert=True)
            try: bot.delete_message(chat_id, msg_id)
            except: pass
            if file_key and file_key != "none":
                _deliver_files(chat_id, file_key, user)
        else:
            bot.answer_callback_query(call.id, "❌ এখনো সব চ্যানেলে Join করেননি!", show_alert=True)
        return

    # শুধু এডমিন কলব্যাক (এরপর থেকে)
    if not is_admin(chat_id):
        bot.answer_callback_query(call.id, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!", show_alert=True)
        return

    # ── Video Thumbnail Confirm / Cancel ──
    if data == "confirm_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        execute_channel_post(chat_id, user, user.get("temp_media_type"), user.get("temp_media_id"))
        return
    elif data == "cancel_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        update_user(chat_id, {"step": "wait_thumbnail", "temp_media_id": "", "temp_media_type": ""})
        bot.send_message(chat_id, "❌ বাতিল করা হয়েছে। নতুন থাম্বনেইল (ছবি/ভিডিও) দিন।")
        return

    # ── Main Menu ──
    if data == "main_menu":
        update_step(chat_id, "none")
        s = get_bot_stats()
        markup = _main_menu_markup()
        bot.edit_message_text(
            f"👋 <b>এডমিন প্যানেলে স্বাগতম!</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>",
            chat_id, msg_id, reply_markup=markup
        )

    # ── Broadcast ──
    elif data == "broadcast":
        markup = InlineKeyboardMarkup()
        markup.row(
            InlineKeyboardButton("📡 সবাইকে", callback_data="bc_all"),
            InlineKeyboardButton("🟢 সক্রিয়দের", callback_data="bc_active")
        )
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu"))
        bot.edit_message_text(
            "📢 <b>ব্রডকাস্ট অপশন:</b>\n\nকাদের কাছে পাঠাবেন?",
            chat_id, msg_id, reply_markup=markup
        )

    elif data in ["bc_all", "bc_active"]:
        target = "all" if data == "bc_all" else "active"
        update_user(chat_id, {"step": f"wait_broadcast_{target}"})
        bot.send_message(chat_id, "📢 ব্রডকাস্টের মেসেজ/ছবি/ভিডিও পাঠান:")

    # ── Stats (নতুন) ──
    elif data == "show_stats":
        s = get_bot_stats()
        text = (
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"📥 আজ ডাউনলোড: <b>{s['downloads_today']}</b>\n"
            f"📤 আজ আপলোড: <b>{s['uploads_today']}</b>\n"
            f"👑 এডমিন: <b>{s['total_admins']}</b>\n"
            f"🚫 ব্যানড: <b>{s['total_banned']}</b>\n\n"
            f"🕐 {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
        )
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu"))
        bot.edit_message_text(text, chat_id, msg_id, reply_markup=markup)

    # ── Batch Upload ──
    elif data == "start_batch":
        batch_id = str(uuid.uuid4().hex)[:10]
        update_user(chat_id, {"batch_id": batch_id, "step": "wait_batch"})
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)", callback_data="finish_batch"))
        bot.edit_message_text(
            "📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n\nএকটি একটি করে ফাইলগুলো দিন।\nশেষ হলে <b>Finish</b> বাটনে ক্লিক করুন।",
            chat_id, msg_id, reply_markup=markup
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
        bot.edit_message_text("⏳ <b>লিংক তৈরি হচ্ছে...</b>", chat_id, msg_id)
        bot_deep_link = f"https://t.me/{BOT_USERNAME}?start={batch_id}"
        short_link    = get_terabox_short_link(bot_deep_link)
        update_user(chat_id, {
            "step": "wait_thumbnail", "pending_link": bot_deep_link,
            "pending_short_link": short_link, "batch_id": ""
        })
        reply = (
            f"✅ <b>{count}টি ফাইল সেভ হয়েছে!</b>\n\n"
            f"💎 ডাইরেক্ট লিংক:\n<code>{bot_deep_link}</code>\n\n"
            f"📺 শর্ট লিংক:\n<code>{short_link}</code>\n\n"
            f"🖼️ এখন চ্যানেলে পোস্টের জন্য একটি থাম্বনেইল (ছবি/ভিডিও) দিন।\n"
            f"<i>স্কিপ করতে /skip লিখুন</i>"
        )
        bot.edit_message_text(reply, chat_id, msg_id, disable_web_page_preview=True)

    # ── Settings ──
    elif data == "settings":
        update_step(chat_id, "none")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📝 Header / Footer", callback_data="menu_texts"))
        markup.add(InlineKeyboardButton("🔗 আপডেট চ্যানেল", callback_data="menu_channels"))
        markup.add(InlineKeyboardButton("🎥 টিউটোরিয়াল ভিডিও", callback_data="menu_tutorials"))
        markup.add(InlineKeyboardButton("📤 অটো পোস্ট চ্যানেল", callback_data="menu_auto_post"))
        markup.add(InlineKeyboardButton("🔒 ফোর্স সাবস্ক্রাইব", callback_data="menu_force_sub"))  # নতুন
        markup.add(InlineKeyboardButton("⚙️ অ্যাডভান্সড সেটিংস", callback_data="menu_advanced"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu"))
        bot.edit_message_text("⚙️ <b>বট সেটিংস:</b>", chat_id, msg_id, reply_markup=markup)

    # ── Force Subscribe (নতুন) ──
    elif data == "menu_force_sub":
        force_channels = list(force_sub_col.find())
        markup = InlineKeyboardMarkup()
        for fc in force_channels:
            st_ico = "🟢" if fc.get("status") == "on" else "🔴"
            markup.row(
                InlineKeyboardButton(f"{st_ico} {fc['name']}", callback_data=f"tog_fs_{fc['fs_id']}"),
                InlineKeyboardButton("🗑️", callback_data=f"del_fs_{fc['fs_id']}")
            )
        markup.add(InlineKeyboardButton("➕ চ্যানেল যোগ করুন", callback_data="add_force_sub"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="settings"))
        status = "চালু 🟢" if force_channels else "বন্ধ (কোনো চ্যানেল নেই)"
        bot.edit_message_text(
            f"🔒 <b>ফোর্স সাবস্ক্রাইব</b>\nস্ট্যাটাস: {status}\n\n"
            "ইউজার এই চ্যানেলগুলোতে জয়েন না করলে ফাইল পাবে না।",
            chat_id, msg_id, reply_markup=markup
        )

    elif data == "add_force_sub":
        update_step(chat_id, "wait_add_force_sub")
        bot.send_message(chat_id,
            "📢 ফোর্স সাবস্ক্রাইব চ্যানেল যোগ করুন:\n"
            "ফরম্যাট: <code>নাম | চ্যানেল_আইডি | চ্যানেল_লিংক</code>\n"
            "উদাহরণ: <code>My Channel | -1001234567890 | https://t.me/mychannel</code>"
        )

    elif data.startswith("tog_fs_"):
        fs_id = data.replace("tog_fs_", "")
        fc = force_sub_col.find_one({"fs_id": fs_id})
        if fc:
            new_st = "off" if fc.get("status") == "on" else "on"
            force_sub_col.update_one({"fs_id": fs_id}, {"$set": {"status": new_st}})
            call.data = "menu_force_sub"
            callback_handler(call)

    elif data.startswith("del_fs_"):
        fs_id = data.replace("del_fs_", "")
        force_sub_col.delete_one({"fs_id": fs_id})
        bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)
        call.data = "menu_force_sub"
        callback_handler(call)

    # ── Auto Post Channels ──
    elif data == "menu_auto_post":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"📺 Ad Channel ({auto_channels_col.count_documents({'type':'ad'})})", callback_data="list_ch_ad"))
        markup.add(InlineKeyboardButton(f"💎 Premium Channel ({auto_channels_col.count_documents({'type':'premium'})})", callback_data="list_ch_premium"))
        markup.add(InlineKeyboardButton(f"💾 Log Channel ({auto_channels_col.count_documents({'type':'log'})})", callback_data="list_ch_log"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="settings"))
        bot.edit_message_text("📤 <b>অটো পোস্ট চ্যানেল ম্যানেজমেন্ট:</b>", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("list_ch_"):
        c_type   = data.split("_")[2]
        channels = list(auto_channels_col.find({"type": c_type}))
        markup   = InlineKeyboardMarkup()
        for ch in channels:
            ch_id = ch.get("ch_id")
            if not ch_id:
                ch_id = str(uuid.uuid4().hex)[:8]
                auto_channels_col.update_one({"_id": ch["_id"]}, {"$set": {"ch_id": ch_id, "name": ch.get("channel_id"), "status": "on"}})
                ch["ch_id"], ch["name"], ch["status"] = ch_id, ch.get("channel_id"), "on"
            st_ico = "🟢 ON" if ch.get("status", "on") == "on" else "🔴 OFF"
            markup.row(
                InlineKeyboardButton(f"{ch.get('name', 'Unknown')} [{st_ico}]", callback_data=f"togch_{ch['ch_id']}"),
                InlineKeyboardButton("🗑️", callback_data=f"delch_{ch['ch_id']}")
            )
        markup.add(InlineKeyboardButton("➕ নতুন চ্যানেল", callback_data=f"add_ch_{c_type}"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_auto_post"))
        type_names = {"ad": "📺 Ad", "premium": "💎 Premium", "log": "💾 Log"}
        bot.edit_message_text(
            f"<b>{type_names.get(c_type, c_type)} Channels:</b>\nTON/OFF করতে নামে ক্লিক করুন।",
            chat_id, msg_id, reply_markup=markup
        )

    elif data.startswith("togch_"):
        ch_id = data.split("_")[1]
        ch    = auto_channels_col.find_one({"ch_id": ch_id})
        if ch:
            new_st = "off" if ch.get("status", "on") == "on" else "on"
            auto_channels_col.update_one({"ch_id": ch_id}, {"$set": {"status": new_st}})
            call.data = f"list_ch_{ch['type']}"
            callback_handler(call)

    elif data.startswith("delch_"):
        ch_id = data.split("_")[1]
        ch    = auto_channels_col.find_one({"ch_id": ch_id})
        if ch:
            auto_channels_col.delete_one({"ch_id": ch_id})
            bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)
            call.data = f"list_ch_{ch['type']}"
            callback_handler(call)

    elif data.startswith("add_ch_"):
        c_type = data.split("_")[2]
        update_step(chat_id, f"wait_add_{c_type}")
        bot.send_message(chat_id,
            f"📝 <b>নতুন চ্যানেল যোগ করুন:</b>\n"
            f"ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>\n"
            f"উদাহরণ: <code>My Channel | -1001234567890</code>"
        )

    # ── Custom Buttons ──
    elif data == "menu_custom_buttons":
        btns   = user.get("custom_buttons", [])
        markup = InlineKeyboardMarkup()
        for i, btn in enumerate(btns):
            st_ico = "🟢" if btn.get("status") == "on" else "🔴"
            markup.row(
                InlineKeyboardButton(f"{st_ico} {btn['name']}", callback_data=f"togbtn_{i}"),
                InlineKeyboardButton("🗑️", callback_data=f"delbtn_{i}")
            )
        markup.add(InlineKeyboardButton("➕ নতুন বাটন যোগ করুন", callback_data="add_custom_btn"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_advanced"))
        bot.edit_message_text(
            f"🔘 <b>কাস্টম বাটন ম্যানেজমেন্ট:</b>\n"
            f"মোট: {len(btns)}টি বাটন",
            chat_id, msg_id, reply_markup=markup
        )

    elif data.startswith("togbtn_"):
        idx  = int(data.split("_")[1])
        btns = user.get("custom_buttons", [])
        if idx < len(btns):
            btns[idx]["status"] = "off" if btns[idx].get("status") == "on" else "on"
            update_user(chat_id, {"custom_buttons": btns})
            call.data = "menu_custom_buttons"
            callback_handler(call)

    elif data.startswith("delbtn_"):
        idx  = int(data.split("_")[1])
        btns = user.get("custom_buttons", [])
        if idx < len(btns):
            btns.pop(idx)
            update_user(chat_id, {"custom_buttons": btns})
            bot.answer_callback_query(call.id, "✅ বাটন মুছে ফেলা হয়েছে!")
            call.data = "menu_custom_buttons"
            callback_handler(call)

    elif data == "add_custom_btn":
        update_step(chat_id, "wait_custom_btn")
        bot.send_message(chat_id,
            "🔘 বাটনের নাম এবং লিংক | দিয়ে আলাদা করে দিন।\n"
            "উদাহরণ: <code>Direct Link | https://example.com</code>"
        )

    # ── Advanced Settings ──
    elif data == "menu_advanced":
        link_btn = "🔗 লিংক: ON 🟢" if user.get("post_link_toggle", 1) == 1 else "🔗 লিংক: OFF 🔴"
        tut_btn  = "📽️ Tutorial: ON 🟢" if user.get("post_tutorial_toggle", 1) == 1 else "📽️ Tutorial: OFF 🔴"
        rep_count = user.get("link_repeat_count", 1)
        ad_sec    = user.get("auto_delete", 0)
        ad_label  = f"⏳ Auto-Delete: {ad_sec} মিনিট" if ad_sec > 0 else "⏳ Auto-Delete: OFF"

        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton(link_btn, callback_data="toggle_post_link"),
                   InlineKeyboardButton(tut_btn, callback_data="toggle_tutorial_btn"))
        markup.add(InlineKeyboardButton(f"🔄 লিংক রিপিট: {rep_count}x", callback_data="set_link_repeat"))
        markup.add(InlineKeyboardButton(ad_label, callback_data="set_autodelete"))
        markup.add(InlineKeyboardButton("🔘 কাস্টম বাটন", callback_data="menu_custom_buttons"))
        markup.add(InlineKeyboardButton("👥 এডমিন ম্যানেজমেন্ট", callback_data="manage_admins"))
        markup.add(InlineKeyboardButton("🚫 ব্যান ম্যানেজমেন্ট", callback_data="manage_bans"))  # নতুন
        markup.row(InlineKeyboardButton("💾 ব্যাকআপ", callback_data="cmd_backup"),
                   InlineKeyboardButton("🔄 রিস্টোর", callback_data="cmd_restore"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="settings"))
        bot.edit_message_text("⚙️ <b>অ্যাডভান্সড সেটিংস:</b>", chat_id, msg_id, reply_markup=markup)

    elif data in ["toggle_post_link", "toggle_tutorial_btn"]:
        key     = "post_link_toggle" if data == "toggle_post_link" else "post_tutorial_toggle"
        new_val = 0 if user.get(key, 1) == 1 else 1
        update_user(chat_id, {key: new_val})
        user[key] = new_val
        call.data = "menu_advanced"
        callback_handler(call)

    # ── Ban Management (নতুন) ──
    elif data == "manage_bans":
        banned_users = list(banned_col.find({}, {"chat_id": 1, "reason": 1}))
        markup = InlineKeyboardMarkup()
        for bu in banned_users[:10]:  # সর্বোচ্চ ১০টি দেখাবে
            markup.add(InlineKeyboardButton(f"🚫 {bu['chat_id']}", callback_data=f"unban_{bu['chat_id']}"))
        markup.add(InlineKeyboardButton("➕ নতুন ব্যান", callback_data="add_ban"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_advanced"))
        bot.edit_message_text(
            f"🚫 <b>ব্যান ম্যানেজমেন্ট</b>\nমোট ব্যানড: {len(banned_users)} জন\n\n"
            "আনব্যান করতে আইডিতে ক্লিক করুন।",
            chat_id, msg_id, reply_markup=markup
        )

    elif data == "add_ban":
        update_step(chat_id, "wait_ban_user")
        bot.send_message(chat_id, "🚫 যাকে ব্যান করবেন তার Telegram ID দিন:\nউদাহরণ: <code>1234567890</code>")

    elif data.startswith("unban_"):
        target_id = data.replace("unban_", "")
        banned_col.delete_one({"chat_id": target_id})
        bot.answer_callback_query(call.id, f"✅ {target_id} আনব্যান হয়েছে!", show_alert=True)
        call.data = "manage_bans"
        callback_handler(call)

    # ── Admin Management ──
    elif data == "manage_admins":
        all_admins = list(admins_col.find())
        markup     = InlineKeyboardMarkup()
        for adm in all_admins:
            if adm['chat_id'] != str(MAIN_ADMIN_ID):
                markup.add(InlineKeyboardButton(f"👤 {adm['chat_id']}", callback_data=f"remove_admin_{adm['chat_id']}"))
        markup.add(InlineKeyboardButton("➕ এডমিন যোগ", callback_data="add_admin"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_advanced"))
        bot.edit_message_text(
            f"👥 <b>এডমিন ম্যানেজমেন্ট</b>\nমোট: {len(all_admins)} জন এডমিন",
            chat_id, msg_id, reply_markup=markup
        )

    elif data.startswith("remove_admin_"):
        target_id = data.replace("remove_admin_", "")
        if target_id == str(MAIN_ADMIN_ID):
            bot.answer_callback_query(call.id, "⛔ সুপার এডমিন রিমুভ করা যাবে না!", show_alert=True)
            return
        admins_col.delete_one({"chat_id": target_id})
        bot.answer_callback_query(call.id, f"✅ {target_id} এডমিন থেকে সরানো হয়েছে!", show_alert=True)
        call.data = "manage_admins"
        callback_handler(call)

    elif data == "add_admin":
        update_step(chat_id, "wait_add_admin")
        bot.send_message(chat_id, "➕ নতুন এডমিনের Telegram ID দিন:")

    # ── Backup & Restore ──
    elif data == "cmd_backup":
        bot.answer_callback_query(call.id, "⏳ ব্যাকআপ তৈরি হচ্ছে...")
        bot.send_message(chat_id, "⏳ ডাটাবেস ব্যাকআপ তৈরি করা হচ্ছে...")
        backup_data = {
            "version": BOT_VERSION,
            "backup_date": datetime.now().isoformat(),
            "users": list(users_col.find({}, {"_id": 0})),
            "files": list(files_col.find({}, {"_id": 0})),
            "tutorials": list(tutorials_col.find({}, {"_id": 0})),
            "channels": list(channels_col.find({}, {"_id": 0})),
            "auto_channels": list(auto_channels_col.find({}, {"_id": 0})),
            "force_sub": list(force_sub_col.find({}, {"_id": 0})),
        }
        try:
            with open("backup.json", "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)
            with open("backup.json", "rb") as f:
                total = len(backup_data['users'])
                bot.send_document(
                    chat_id, f,
                    caption=f"✅ <b>ডাটাবেস ব্যাকআপ সম্পন্ন!</b>\n"
                            f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}\n"
                            f"👥 ইউজার: {total}\n"
                            f"📁 ফাইল: {len(backup_data['files'])}\n"
                            f"🔖 ভার্সন: v{BOT_VERSION}"
                )
        except Exception as e:
            bot.send_message(chat_id, f"❌ ব্যাকআপ ব্যর্থ: {e}")
        finally:
            if os.path.exists("backup.json"):
                os.remove("backup.json")

    elif data == "cmd_restore":
        update_step(chat_id, "wait_restore")
        bot.send_message(chat_id, "🔄 <b>ডাটাবেস রিস্টোর:</b>\nআপনার <code>backup.json</code> ফাইলটি দিন।")

    # ── Text/Channel/Tutorial Menus ──
    actions = {
        "menu_texts": ("📝 টেক্সট সেটিংস", [
            ("📁 File Header", "set_file_header"), ("📁 File Footer", "set_file_footer"),
            ("📤 Post Header", "set_post_header"), ("📤 Post Footer", "set_post_footer"),
            ("🔙 ব্যাক", "settings")
        ]),
        "menu_channels": ("📢 আপডেট চ্যানেল ম্যানেজ", [
            ("➕ নতুন চ্যানেল", "add_channel"), ("🗑️ সব চ্যানেল মুছুন", "clear_channels"),
            ("🔙 ব্যাক", "settings")
        ]),
        "menu_tutorials": ("🎥 টিউটোরিয়াল ভিডিও ম্যানেজ", [
            ("➕ নতুন ভিডিও", "add_tutorial"), ("🗑️ সব ভিডিও মুছুন", "clear_tutorials"),
            ("🔙 ব্যাক", "settings")
        ]),
    }
    if data in actions:
        text, btns = actions[data]
        markup = InlineKeyboardMarkup()
        for btn in btns:
            markup.add(InlineKeyboardButton(btn[0], callback_data=btn[1]))
        bot.edit_message_text(f"<b>{text}</b>", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("clear_"):
        if data == "clear_channels":
            channels_col.delete_many({})
        elif data == "clear_tutorials":
            tutorials_col.delete_many({})
        bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)

    # ── Step Triggers ──
    step_triggers = {
        "set_file_header":  ("wait_file_header",  "📝 ফাইলের <b>Header</b> লিখে পাঠান:"),
        "set_file_footer":  ("wait_file_footer",  "📝 ফাইলের <b>Footer</b> লিখে পাঠান:"),
        "set_post_header":  ("wait_post_header",  "📝 পোস্টের <b>Header</b> লিখে পাঠান:"),
        "set_post_footer":  ("wait_post_footer",  "📝 পোস্টের <b>Footer</b> লিখে পাঠান:"),
        "add_channel":      ("wait_add_channel",  "📢 ফরম্যাট: <code>নাম | লিংক</code>"),
        "add_tutorial":     ("wait_add_tutorial", "📽️ ফরম্যাট: <code>নাম | লিংক</code>"),
        "set_autodelete":   ("wait_autodelete",   "⏳ অটো-ডিলিট সময় লিখুন (মিনিটে)। বন্ধ করতে 0।"),
        "set_link_repeat":  ("wait_link_repeat",  "🔄 লিংক কতবার রিপিট হবে? (১-৫)"),
    }
    if data in step_triggers:
        update_step(chat_id, step_triggers[data][0])
        bot.send_message(chat_id, step_triggers[data][1], parse_mode="HTML")

    elif data == "help_menu":
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu"))
        bot.edit_message_text(
            "ℹ️ <b>সাহায্য ও তথ্য:</b>\n\n"
            "• <b>সিঙ্গেল আপলোড:</b> সরাসরি ফাইল দিন\n"
            "• <b>ব্যাচ আপলোড:</b> একাধিক ফাইল একসাথে\n"
            "• <b>ফোর্স সাবস্ক্রাইব:</b> চ্যানেল জয়েন বাধ্যতামূলক\n"
            "• <b>অটো-ডিলিট:</b> নির্দিষ্ট সময় পর ফাইল মুছে যায়\n"
            "• <b>লগ চ্যানেল:</b> সব ফাইলের ব্যাকআপ\n"
            "• <b>ব্যাকআপ/রিস্টোর:</b> ডাটাবেস সুরক্ষিত রাখুন\n\n"
            f"🤖 Bot v{BOT_VERSION}",
            chat_id, msg_id, reply_markup=markup
        )

# ═══════════════════════════════════════════════════════════
#                 মেসেজ হ্যান্ডলার (উন্নত)
# ═══════════════════════════════════════════════════════════
@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'audio'])
def handle_message(message):
    chat_id = str(message.chat.id)
    text    = message.text or message.caption or ""
    user    = get_user(chat_id)
    admin_status = is_admin(chat_id)

    # ব্যান চেক
    if is_banned(chat_id) and not admin_status:
        try:
            bot.send_message(chat_id, "🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        except: pass
        return

    # ══ /start কমান্ড ══
    if text.startswith("/start"):
        parts = text.split(" ")
        if len(parts) > 1:
            file_key = parts[1]
            # ফোর্স সাবস্ক্রাইব চেক
            joined, not_joined = check_force_subscribe(chat_id)
            if not joined:
                send_force_subscribe_message(chat_id, not_joined, file_key)
                return
            _deliver_files(chat_id, file_key, user)
        else:
            if admin_status:
                s = get_bot_stats()
                markup = _main_menu_markup()
                bot.send_message(
                    chat_id,
                    f"👋 <b>এডমিন প্যানেলে স্বাগতম!</b>\n\n"
                    f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
                    f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
                    f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>\n\n"
                    f"সিঙ্গেল ফাইল আপলোড করতে সরাসরি ফাইল দিন।",
                    reply_markup=markup
                )
            else:
                markup = InlineKeyboardMarkup()
                for tut in tutorials_col.find():
                    markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
                for ch in channels_col.find():
                    markup.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                bot.send_message(
                    chat_id,
                    "👋 <b>স্বাগতম!</b>\n\nলিংক শেয়ার করলে এখানে ফাইল পাবেন।",
                    reply_markup=markup if markup.keyboard else None
                )
        return

    # ══ /stats কমান্ড (এডমিন) ══
    if text == "/stats" and admin_status:
        s = get_bot_stats()
        bot.send_message(
            chat_id,
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n\n"
            f"👥 মোট ইউজার: <b>{s['total_users']}</b>\n"
            f"🟢 আজ সক্রিয়: <b>{s['active_today']}</b>\n"
            f"📁 মোট ফাইল: <b>{s['total_files']}</b>\n"
            f"📥 আজ ডাউনলোড: <b>{s['downloads_today']}</b>\n"
            f"📤 আজ আপলোড: <b>{s['uploads_today']}</b>"
        )
        return

    # ══ /ban কমান্ড ══
    if text.startswith("/ban ") and admin_status:
        parts = text.split(" ", 2)
        target_id = parts[1]
        reason    = parts[2] if len(parts) > 2 else "কোনো কারণ উল্লেখ নেই"
        if not banned_col.find_one({"chat_id": target_id}):
            banned_col.insert_one({"chat_id": target_id, "reason": reason, "banned_at": datetime.now().isoformat(), "banned_by": chat_id})
            bot.send_message(chat_id, f"🚫 ইউজার <code>{target_id}</code> ব্যান হয়েছে।\nকারণ: {reason}")
        else:
            bot.send_message(chat_id, f"⚠️ ইউজার <code>{target_id}</code> আগেই ব্যান করা আছে।")
        return

    # ══ /unban কমান্ড ══
    if text.startswith("/unban ") and admin_status:
        target_id = text.split(" ")[1]
        result    = banned_col.delete_one({"chat_id": target_id})
        if result.deleted_count:
            bot.send_message(chat_id, f"✅ ইউজার <code>{target_id}</code> আনব্যান হয়েছে।")
        else:
            bot.send_message(chat_id, "⚠️ এই আইডি ব্যান লিস্টে নেই।")
        return

    # ══ /reply কমান্ড ══
    if text.startswith("/reply ") and admin_status:
        parts = text.split(" ", 2)
        if len(parts) == 3:
            _, uid, msg = parts
            try:
                bot.send_message(uid, f"👨‍💻 <b>এডমিনের উত্তর:</b>\n\n{msg}")
                bot.send_message(chat_id, "✅ মেসেজ পাঠানো হয়েছে!")
            except:
                bot.send_message(chat_id, "❌ মেসেজ পাঠানো যায়নি।")
        return

    # ══ /cancel কমান্ড ══
    if text == "/cancel":
        update_step(chat_id, "none")
        bot.send_message(chat_id, "❌ কাজ বাতিল করা হয়েছে।")
        return

    # ══ নন-এডমিনদের মেসেজ ফরোয়ার্ড ══
    if not admin_status:
        try:
            bot.forward_message(MAIN_ADMIN_ID, chat_id, message.message_id)
            bot.send_message(MAIN_ADMIN_ID,
                f"📩 <b>নতুন মেসেজ:</b>\n"
                f"👤 আইডি: <code>{chat_id}</code>\n"
                f"💬 রিপ্লাই: <code>/reply {chat_id} আপনার_মেসেজ</code>"
            )
            bot.send_message(chat_id, "✅ <i>আপনার মেসেজ এডমিনের কাছে পাঠানো হয়েছে।</i>")
        except: pass
        return

    # এডমিন স্টেপ হ্যান্ডলিং
    step = user.get("step", "none")

    # ── Broadcast ──
    if step.startswith("wait_broadcast"):
        target = step.replace("wait_broadcast_", "") if "_" in step else "all"
        update_step(chat_id, "none")
        bot.send_message(chat_id, "⏳ ব্রডকাস্ট শুরু হচ্ছে (background-এ চলবে)...")
        threading.Thread(
            target=broadcast_worker,
            args=(chat_id, chat_id, message.message_id, target),
            daemon=True
        ).start()
        return

    # ── Force Subscribe যোগ ──
    if step == "wait_add_force_sub":
        if "|" in text:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) >= 3:
                fs_id = str(uuid.uuid4().hex)[:8]
                force_sub_col.insert_one({
                    "fs_id": fs_id, "name": parts[0],
                    "channel_id": parts[1], "url": parts[2], "status": "on"
                })
                update_step(chat_id, "none")
                bot.send_message(chat_id, f"✅ ফোর্স সাবস্ক্রাইব চ্যানেল যোগ হয়েছে: <b>{parts[0]}</b>")
            else:
                bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        return

    # ── Auto Channels যোগ ──
    if step in ["wait_add_ad", "wait_add_premium", "wait_add_log"]:
        if "|" in text:
            c_name, c_id = [p.strip() for p in text.split("|", 1)]
            c_type  = step.split("_")[2]
            ch_uuid = str(uuid.uuid4().hex)[:8]
            auto_channels_col.insert_one({
                "ch_id": ch_uuid, "type": c_type,
                "name": c_name, "channel_id": c_id, "status": "on"
            })
            update_step(chat_id, "none")
            bot.send_message(chat_id, f"✅ চ্যানেল যোগ হয়েছে: <b>{c_name}</b>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>")
        return

    # ── Custom Button যোগ ──
    if step == "wait_custom_btn":
        if "|" in text:
            b_name, b_link = [p.strip() for p in text.split("|", 1)]
            btns = user.get("custom_buttons", [])
            btns.append({"name": b_name, "url": b_link, "status": "on"})
            update_user(chat_id, {"custom_buttons": btns, "step": "none"})
            bot.send_message(chat_id, f"✅ কাস্টম বাটন যুক্ত হয়েছে: <b>{b_name}</b>")
        else:
            bot.send_message(chat_id, "⚠️ ফরম্যাট: <code>নাম | লিংক</code>")
        return

    # ── Ban User ──
    if step == "wait_ban_user" and text:
        target_id = text.strip()
        if target_id == str(MAIN_ADMIN_ID):
            bot.send_message(chat_id, "⛔ সুপার এডমিনকে ব্যান করা যাবে না!")
        elif not banned_col.find_one({"chat_id": target_id}):
            banned_col.insert_one({
                "chat_id": target_id, "reason": "এডমিন কর্তৃক ব্যান",
                "banned_at": datetime.now().isoformat(), "banned_by": chat_id
            })
            bot.send_message(chat_id, f"🚫 <code>{target_id}</code> ব্যান হয়েছে!")
        else:
            bot.send_message(chat_id, "⚠️ আগেই ব্যান করা আছে।")
        update_step(chat_id, "none")
        return

    # ── Text Settings ──
    text_step_map = {
        "wait_file_header":  ("header",    "📁 File Header"),
        "wait_file_footer":  ("footer",    "📁 File Footer"),
        "wait_post_header":  ("post_header","📤 Post Header"),
        "wait_post_footer":  ("post_footer","📤 Post Footer"),
    }
    if step in text_step_map and message.text:
        key, label = text_step_map[step]
        update_user(chat_id, {key: text, "step": "none"})
        bot.send_message(chat_id, f"✅ <b>{label}</b> সেট হয়েছে!")
        return

    if step == "wait_autodelete" and text.isdigit():
        update_user(chat_id, {"auto_delete": int(text), "step": "none"})
        val = int(text)
        msg = f"✅ Auto-Delete <b>{val} মিনিট</b> সেট হয়েছে!" if val > 0 else "✅ Auto-Delete <b>বন্ধ</b> করা হয়েছে!"
        bot.send_message(chat_id, msg)
        return

    if step == "wait_link_repeat" and text.isdigit():
        count = max(1, min(int(text), 5))
        update_user(chat_id, {"link_repeat_count": count, "step": "none"})
        bot.send_message(chat_id, f"✅ লিংক রিপিট <b>{count}x</b> সেট হয়েছে!")
        return

    if step == "wait_add_channel" and "|" in text:
        n, l = [p.strip() for p in text.split("|", 1)]
        channels_col.insert_one({"name": n, "url": l})
        update_step(chat_id, "none")
        bot.send_message(chat_id, f"✅ আপডেট চ্যানেল যোগ হয়েছে: <b>{n}</b>")
        return

    if step == "wait_add_tutorial" and "|" in text:
        n, l = [p.strip() for p in text.split("|", 1)]
        tutorials_col.insert_one({"name": n, "url": l})
        update_step(chat_id, "none")
        bot.send_message(chat_id, f"✅ টিউটোরিয়াল যোগ হয়েছে: <b>{n}</b>")
        return

    if step == "wait_add_admin" and text:
        new_admin_id = text.strip()
        if not admins_col.find_one({"chat_id": new_admin_id}):
            admins_col.insert_one({"chat_id": new_admin_id, "role": "admin", "added_at": datetime.now().isoformat()})
            bot.send_message(chat_id, f"✅ <code>{new_admin_id}</code> এডমিন হিসেবে যোগ হয়েছে!")
            try: bot.send_message(new_admin_id, "🎉 আপনাকে এডমিন করা হয়েছে!")
            except: pass
        else:
            bot.send_message(chat_id, "⚠️ এই আইডি আগেই এডমিন।")
        update_step(chat_id, "none")
        return

    # ── Restore ──
    if step == "wait_restore" and message.document:
        try:
            bot.send_message(chat_id, "⏳ ডাটাবেস রিস্টোর করা হচ্ছে...")
            file_info = bot.get_file(message.document.file_id)
            data_raw  = bot.download_file(file_info.file_path)
            data      = json.loads(data_raw)
            if "users" in data and data["users"]:
                for u in data["users"]:
                    if not users_col.find_one({"chat_id": u.get("chat_id")}):
                        users_col.insert_one(u)
            if "files" in data and data["files"]:
                for f in data["files"]:
                    if not files_col.find_one({"file_key": f.get("file_key")}):
                        files_col.insert_one(f)
            if "auto_channels" in data and data["auto_channels"]:
                auto_channels_col.insert_many(data["auto_channels"])
            if "force_sub" in data and data["force_sub"]:
                force_sub_col.insert_many(data["force_sub"])
            update_step(chat_id, "none")
            bot.send_message(chat_id, "✅ <b>ডাটাবেস সফলভাবে রিস্টোর হয়েছে!</b>")
        except Exception as e:
            bot.send_message(chat_id, f"❌ রিস্টোর ব্যর্থ!\nError: <code>{e}</code>")
        return

    # ── Thumbnail Upload ──
    if step == "wait_thumbnail":
        if text == "/skip":
            update_user(chat_id, {"step": "none", "pending_link": "", "pending_short_link": ""})
            bot.send_message(chat_id, "✅ থাম্বনেইল স্কিপ করা হয়েছে।")
            return
        if message.video:
            update_user(chat_id, {"temp_media_id": message.video.file_id, "temp_media_type": "video"})
            markup = InlineKeyboardMarkup()
            markup.row(
                InlineKeyboardButton("✅ Confirm করুন", callback_data="confirm_vid_thumb"),
                InlineKeyboardButton("❌ বাতিল", callback_data="cancel_vid_thumb")
            )
            bot.send_message(chat_id, "🎥 ভিডিও থাম্বনেইল হিসেবে পোস্ট করবেন?", reply_markup=markup)
            return
        elif message.photo:
            execute_channel_post(chat_id, user, "photo", message.photo[-1].file_id)
            return
        else:
            bot.send_message(chat_id, "⚠️ একটি ছবি বা ভিডিও দিন, অথবা /skip লিখুন।")
            return

    # ── File Upload (এডমিন) ──
    file_id, f_type = None, None
    if message.document:
        file_id, f_type = message.document.file_id, "document"
    elif message.video and step != "wait_thumbnail":
        file_id, f_type = message.video.file_id, "video"
    elif message.audio:
        file_id, f_type = message.audio.file_id, "audio"
    elif message.photo and step != "wait_thumbnail":
        file_id, f_type = message.photo[-1].file_id, "photo"

    if file_id:
        unique_id   = str(uuid.uuid4().hex)[:10]
        log_chat, log_msg = "", ""

        log_ch = auto_channels_col.find_one({"type": "log", "status": "on"})
        if log_ch:
            try:
                caption_log = f"💾 <b>File Backup</b>\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n🔑 <code>{unique_id}</code>"
                res = None
                if f_type == 'document': res = bot.send_document(log_ch['channel_id'], file_id, caption=caption_log)
                elif f_type == 'video':   res = bot.send_video(log_ch['channel_id'], file_id, caption=caption_log)
                elif f_type == 'photo':   res = bot.send_photo(log_ch['channel_id'], file_id, caption=caption_log)
                elif f_type == 'audio':   res = bot.send_audio(log_ch['channel_id'], file_id, caption=caption_log)
                if res:
                    log_chat, log_msg = log_ch['channel_id'], res.message_id
            except Exception as e:
                logger.warning(f"Log channel backup failed: {e}")

        if step == "wait_batch":
            batch_id = user.get("batch_id")
            files_col.insert_one({
                "file_key": unique_id, "file_id": file_id, "type": f_type,
                "uploader": chat_id, "batch_id": batch_id,
                "log_chat_id": log_chat, "log_msg_id": log_msg,
                "uploaded_at": datetime.now().isoformat()
            })
            count  = files_col.count_documents({"batch_id": batch_id})
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)", callback_data="finish_batch"))
            bot.send_message(chat_id, f"✅ <b>#{count} ফাইল ব্যাচে যোগ হয়েছে!</b>", reply_markup=markup)
        else:
            files_col.insert_one({
                "file_key": unique_id, "file_id": file_id, "type": f_type,
                "uploader": chat_id, "batch_id": "",
                "log_chat_id": log_chat, "log_msg_id": log_msg,
                "uploaded_at": datetime.now().isoformat()
            })
            bot_deep_link = f"https://t.me/{BOT_USERNAME}?start={unique_id}"
            short_link    = get_terabox_short_link(bot_deep_link)
            update_user(chat_id, {
                "step": "wait_thumbnail",
                "pending_link": bot_deep_link,
                "pending_short_link": short_link
            })
            update_user(chat_id, {"total_uploads": user.get("total_uploads", 0) + 1})
            _update_daily_stats("uploads", 1)

            bot.send_message(
                chat_id,
                f"✅ <b>ফাইল সেভ হয়েছে!</b>\n\n"
                f"💎 ডাইরেক্ট লিংক:\n<code>{bot_deep_link}</code>\n\n"
                f"📺 শর্ট লিংক:\n<code>{short_link}</code>\n\n"
                f"🖼️ এখন থাম্বনেইল (ছবি/ভিডিও) দিন অথবা /skip লিখুন।",
                disable_web_page_preview=True
            )

# ═══════════════════════════════════════════════════════════
#                  হেল্পার ফাংশনসমূহ
# ═══════════════════════════════════════════════════════════
def _main_menu_markup():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("📦 ব্যাচ আপলোড", callback_data="start_batch"))
    markup.row(
        InlineKeyboardButton("⚙️ সেটিংস", callback_data="settings"),
        InlineKeyboardButton("📊 স্ট্যাটস", callback_data="show_stats")
    )
    markup.row(
        InlineKeyboardButton("📢 ব্রডকাস্ট", callback_data="broadcast"),
        InlineKeyboardButton("ℹ️ হেল্প", callback_data="help_menu")
    )
    return markup

def _deliver_files(chat_id, file_key, user):
    """ফাইল ডেলিভারি লজিক"""
    files = list(files_col.find({"$or": [{"file_key": file_key}, {"batch_id": file_key}]}))
    if not files:
        bot.send_message(chat_id, "❌ <b>ফাইল পাওয়া যায়নি!</b>\nলিংকটি মেয়াদোত্তীর্ণ হতে পারে।")
        return

    bot.send_message(chat_id, f"⏳ আপনার {'ফাইলগুলো' if len(files) > 1 else 'ফাইলটি'} পাঠানো হচ্ছে...")
    uploader     = get_user(files[0]['uploader'])
    h_txt        = uploader.get('header', '')
    f_txt        = uploader.get('footer', '')
    caption      = f"{h_txt}\n\n{f_txt}".strip() if (h_txt or f_txt) else ""

    markup = InlineKeyboardMarkup()
    for tut in tutorials_col.find():
        markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
    for ch in channels_col.find():
        markup.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))

    delivered = 0
    for f in files:
        msg_id_sent = None
        try:
            res = None
            kw = {"caption": caption, "reply_markup": markup if markup.keyboard else None}
            if f['type'] == 'document': res = bot.send_document(chat_id, f['file_id'], **kw)
            elif f['type'] == 'video':  res = bot.send_video(chat_id, f['file_id'], **kw)
            elif f['type'] == 'photo':  res = bot.send_photo(chat_id, f['file_id'], **kw)
            elif f['type'] == 'audio':  res = bot.send_audio(chat_id, f['file_id'], **kw)
            if res:
                msg_id_sent = res.message_id
                delivered += 1
        except:
            if f.get('log_chat_id') and f.get('log_msg_id'):
                try:
                    res = bot.copy_message(chat_id, f['log_chat_id'], f['log_msg_id'],
                                           caption=caption,
                                           reply_markup=markup if markup.keyboard else None)
                    msg_id_sent = res.message_id
                    delivered += 1
                except: pass

        if msg_id_sent and uploader.get("auto_delete", 0) > 0:
            delete_at = int(time.time()) + (uploader["auto_delete"] * 60)
            queue_col.insert_one({"chat_id": chat_id, "message_id": msg_id_sent, "delete_at": delete_at})

        time.sleep(0.3)

    if delivered > 0:
        _update_daily_stats("downloads", delivered)
        update_user(chat_id, {"total_downloads": user.get("total_downloads", 0) + delivered})
        if uploader.get("auto_delete", 0) > 0:
            bot.send_message(
                chat_id,
                f"⚠️ <i>সতর্কতা: ফাইল{'গুলো' if delivered > 1 else 'টি'} "
                f"<b>{uploader['auto_delete']} মিনিট</b> পর মুছে যাবে।</i>"
            )
    else:
        bot.send_message(chat_id, "❌ ফাইল পাঠানো সম্ভব হয়নি। এডমিনের সাথে যোগাযোগ করুন।")

# ═══════════════════════════════════════════════════════════
#                Render Web Server (Health Check)
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/')
def home():
    s = get_bot_stats()
    return {
        "status": "running",
        "version": BOT_VERSION,
        "total_users": s['total_users'],
        "total_files": s['total_files'],
        "uptime": "active"
    }

@app.route('/health')
def health():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)

# ═══════════════════════════════════════════════════════════
#                       মেইন এন্ট্রি পয়েন্ট
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info(f"🚀 Premium Bot v{BOT_VERSION} Starting...")
    threading.Thread(target=run_server, daemon=True).start()
    logger.info("✅ Web server started")

    while True:
        try:
            logger.info("🤖 Bot polling started...")
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
