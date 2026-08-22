"""
╔══════════════════════════════════════════════════════════════╗
║           🚀 PREMIUM FILE SHARE BOT v6.0                     ║
║   Category System · Scheduled Post · Post Buttons ON/OFF     ║
║   Link/Text Filter · Protect Content · Force Subscribe       ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, re, time, json, uuid, threading, requests, telebot, logging, base64
from datetime import datetime, timedelta
from functools import wraps
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from pymongo import MongoClient
from flask import Blueprint, request, jsonify
from urllib.parse import quote

# ══════════════════════════════════════════════════
#  লগিং
# ══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
#  কনফিগারেশন
# ══════════════════════════════════════════════════
BOT_TOKEN     = os.environ.get("SHORTENER_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
BOT_USERNAME  = os.environ.get("BOT_USERNAME",  "YourBotUsername")
WEBBOT_USERNAME = os.environ.get("WEBBOT_USERNAME", "StreamXVideoBot")
MAIN_ADMIN_ID = os.environ.get("MAIN_ADMIN_ID", "5991854507")
TERABOX_TOKEN = os.environ.get("TERABOX_TOKEN", "71b16be6b48d01937bfe7d2c3043cbc0b6363c82")
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "https://telegram-bot-ca2a6-default-rtdb.firebaseio.com/")
MONGO_URL     = os.environ.get("MONGO_URL")
BOT_VERSION   = "6.0.0"

bot = telebot.TeleBot(BOT_TOKEN or "DUMMY_TOKEN", parse_mode="HTML")

# ══════════════════════════════════════════════════
#  MongoDB
# ══════════════════════════════════════════════════
client            = MongoClient(MONGO_URL)
db                = client['telegram_bot_db']
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
categories_col    = db['categories']
scheduled_col     = db['scheduled_posts']

try:
    if MONGO_URL and MONGO_URL != "আপনার_MongoDB_URL":
        users_col.create_index("chat_id", unique=True, background=True)
        files_col.create_index("file_key", background=True)
        files_col.create_index("batch_id", background=True)
        queue_col.create_index("delete_at", background=True)
        scheduled_col.create_index("scheduled_at", background=True)
        
        if not admins_col.find_one({"chat_id": str(MAIN_ADMIN_ID)}):
            admins_col.insert_one({"chat_id": str(MAIN_ADMIN_ID), "role": "super_admin", "added_at": datetime.now().isoformat()})
    else:
        logger.warning("⚠️ MONGO_URL is not set. Database initialization skipped.")
except Exception as e:
    logger.error(f"⚠️ Failed to connect to MongoDB or create indexes: {e}")

# ══════════════════════════════════════════════════
#  গ্লোবাল সেটিংস
# ══════════════════════════════════════════════════
def get_setting(key, default=0):
    doc = settings_col.find_one({"key": key})
    return doc["value"] if doc else default

def set_setting(key, value):
    settings_col.update_one({"key": key}, {"$set": {"value": value}}, upsert=True)

def toggle_setting(key):
    new = 0 if get_setting(key, 0) else 1
    set_setting(key, new)
    return new

def _ico(val):
    return "🟢" if val else "🔴"

# ══════════════════════════════════════════════════
#  ফিল্টার ইউটিলিটি
# ══════════════════════════════════════════════════
URL_RE = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@[A-Za-z0-9_]{5,})', re.IGNORECASE)

def filter_links(text):
    if not text: return text
    return re.sub(r'\n{3,}', '\n\n', URL_RE.sub('', text)).strip()

def clean_html(text):
    if not text: return ""
    return re.sub(r'<[^>]+>', '', text)

def apply_filters(text, uploader_id):
    u = get_user(uploader_id)
    if not text: return text
    if u.get("text_filter"): return ""
    if u.get("link_filter"):  return filter_links(text)
    return text

# ══════════════════════════════════════════════════
#  ডাটাবেস হেল্পার
# ══════════════════════════════════════════════════
_DEFAULTS = {
    "header": "", "footer": "", "post_header": "", "post_footer": "",
    "saved_title": "",
    "auto_delete": 0, "pending_link": "", "pending_short_link": "",
    "step": "none", "batch_id": "",
    "btn_download": 1, "btn_download_1": 1, "btn_download_2": 1, "btn_share": 1, "btn_tutorial": 1,
    "btn_link_in_caption": 0, 
    "link_repeat_count": 1, "auto_title_from_caption": 1,
    "custom_text_1": "Watch Part 1", "custom_link_1": "",
    "custom_text_2": "Watch Part 2", "custom_link_2": "",
    "custom_buttons": [],
    "temp_media_id": "", "temp_media_type": "",
    "joined_at": "", "last_active": "",
    "total_downloads": 0, "total_uploads": 0,
    "link_filter": 0, "text_filter": 0,
    "pending_category": "", "pending_schedule": "", "temp_caption": "",
    "pending_thumb_url": "", "pending_web_title": "",
    "pending_web_video_id": "", "pending_web_post_link": "",
    "pending_web_ads": 1, # কতটি অ্যাড দেখতে হবে
    "last_thumb_file_id": "",
}

def get_user(chat_id):
    chat_id = str(chat_id)
    now = datetime.now().isoformat()
    user = users_col.find_one({"chat_id": chat_id})
    if not user:
        user = {**_DEFAULTS, "chat_id": chat_id, "joined_at": now, "last_active": now}
        users_col.insert_one(user)
        _inc_stat("new_users")
    else:
        upd = {k: v for k, v in _DEFAULTS.items() if k not in user}
        upd["last_active"] = now
        users_col.update_one({"chat_id": chat_id}, {"$set": upd})
        user.update(upd)
    return user

def update_user(chat_id, updates):
    users_col.update_one({"chat_id": str(chat_id)}, {"$set": updates})

def update_step(chat_id, step):
    update_user(chat_id, {"step": step})

def is_admin(chat_id):
    if not chat_id: return False
    cid_str = str(chat_id).strip()
    if cid_str == str(MAIN_ADMIN_ID).strip():
        return True
    return bool(admins_col.find_one({"chat_id": cid_str}))

def is_banned(chat_id): return bool(banned_col.find_one({"chat_id": str(chat_id)}))

# ══════════════════════════════════════════════════
#  স্ট্যাটিস্টিক্স
# ══════════════════════════════════════════════════
def _inc_stat(field, n=1):
    today = datetime.now().strftime("%Y-%m-%d")
    stats_col.update_one({"date": today}, {"$inc": {field: n}}, upsert=True)

def get_stats():
    today    = datetime.now().strftime("%Y-%m-%d")
    td       = stats_col.find_one({"date": today}) or {}
    active   = users_col.count_documents({"last_active": {"$regex": f"^{today}"}})
    return {
        "total_users":  users_col.count_documents({}),
        "total_files":  files_col.count_documents({}),
        "total_admins": admins_col.count_documents({}),
        "total_banned": banned_col.count_documents({}),
        "active_today": active,
        "dl_today":     td.get("downloads", 0),
        "ul_today":     td.get("uploads", 0),
    }

# ══════════════════════════════════════════════════
#  ফোর্স সাবস্ক্রাইব
# ══════════════════════════════════════════════════
def check_force_sub(chat_id):
    chs = list(force_sub_col.find({"status": "on"}))
    if not chs: return True, []
    not_joined = []
    for ch in chs:
        try:
            m = bot.get_chat_member(ch['channel_id'], int(chat_id))
            if m.status in ['left', 'kicked']: not_joined.append(ch)
        except: not_joined.append(ch)
    return len(not_joined) == 0, not_joined

def send_force_sub_msg(chat_id, not_joined, file_key=None):
    mk = InlineKeyboardMarkup()
    for ch in not_joined:
        mk.add(InlineKeyboardButton(f"📢 {ch['name']} — Join করুন", url=ch['url']))
    mk.add(InlineKeyboardButton("✅ Join করেছি — যাচাই করুন", callback_data=f"check_sub_{file_key or 'none'}"))
    bot.send_message(chat_id, "🔒 <b>ফাইল পেতে নিচের চ্যানেলগুলোতে Join করুন!</b>\n\nJoin করার পর ✅ বাটনে ক্লিক করুন।", reply_markup=mk)

# ══════════════════════════════════════════════════
#  অটো-ডিলিট ওয়ার্কার
# ══════════════════════════════════════════════════
def _auto_delete_worker():
    while True:
        try:
            now = int(time.time())
            for item in list(queue_col.find({"delete_at": {"$lte": now}})):
                try:
                    bot.delete_message(item['chat_id'], item['message_id'])
                    mk = InlineKeyboardMarkup()
                    for ch in channels_col.find():
                        mk.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                    bot.send_message(item['chat_id'], "⚠️ <b>সময় শেষ! ফাইলটি মুছে গেছে।</b>\n🔁 আবার পেতে লিংকে ক্লিক করুন।", reply_markup=mk if mk.keyboard else None)
                except Exception as e:
                    logger.warning(f"AutoDelete: {e}")
                finally:
                    queue_col.delete_one({"_id": item["_id"]})
        except Exception as e:
            logger.error(f"AutoDelete worker: {e}")
        time.sleep(10)

threading.Thread(target=_auto_delete_worker, daemon=True).start()

# ══════════════════════════════════════════════════
#  ব্রডকাস্ট ওয়ার্কার
# ══════════════════════════════════════════════════
def _broadcast_worker(admin_id, from_chat, msg_id, target="all"):
    q = {}
    if target == "active":
        yd = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        q  = {"last_active": {"$gte": yd}}
    all_u = list(users_col.find(q, {"chat_id": 1}))
    total = len(all_u); ok = fail = 0
    try: bot.send_message(admin_id, f"📡 ব্রডকাস্ট শুরু! মোট: <b>{total}</b> জন")
    except: pass
    for i, u in enumerate(all_u):
        try:    bot.copy_message(u['chat_id'], from_chat, msg_id); ok += 1
        except: fail += 1
        time.sleep(0.05)
        if (i+1) % 100 == 0:
            try: bot.send_message(admin_id, f"📊 {i+1}/{total} | ✅{ok} ❌{fail}")
            except: pass
    try: bot.send_message(admin_id, f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n📨 মোট: <b>{total}</b>\n✅ <b>{ok}</b> | ❌ <b>{fail}</b>")
    except: pass

# ══════════════════════════════════════════════════
#  ক্যাটাগরি হেল্পার ও Firebase সিঙ্ক
# ══════════════════════════════════════════════════
def get_categories():
    cats = list(categories_col.find())
    for c in cats:
        if not c.get("cat_id"):
            c_id = str(c["_id"])
            categories_col.update_one({"_id": c["_id"]}, {"$set": {"cat_id": c_id}})
            c["cat_id"] = c_id
    return cats

def get_category(cat_id):
    if not cat_id: return None
    cat_str = str(cat_id).strip()
    cat = categories_col.find_one({"cat_id": cat_str})
    if not cat:
        try:
            from bson import ObjectId
            cat = categories_col.find_one({"_id": ObjectId(cat_str)})
        except:
            cat = categories_col.find_one({"_id": cat_str})
    return cat

def get_auto_channel(ch_id):
    if not ch_id: return None
    chid_str = str(ch_id).strip()
    ch = auto_channels_col.find_one({"ch_id": chid_str})
    if not ch:
        try:
            from bson import ObjectId
            ch = auto_channels_col.find_one({"_id": ObjectId(chid_str)})
        except:
            ch = auto_channels_col.find_one({"_id": chid_str})
    return ch

def sync_categories_to_firebase():
    """ওয়েবসাইটের জন্য ক্যাটাগরি লিস্ট Firebase-এ সিঙ্ক করে"""
    if not FIREBASE_DB_URL: return
    try:
        cats = [c['name'] for c in get_categories()]
        clean_url = FIREBASE_DB_URL.rstrip("/")
        requests.put(f"{clean_url}/categories.json", json=cats, timeout=10)
    except Exception as e:
        logger.warning(f"Firebase category sync error: {e}")

# ══════════════════════════════════════════════════
#  মাল্টি-ল্যাংগুয়েজ ডিকশনারি ও টিউটোরিয়াল হেল্পার
# ══════════════════════════════════════════════════
LANG_TEXTS = {
    "en": {
        "name": "English 🇬🇧",
        "badge": "🇬🇧 EN",
        "ad_caption_cta": "⬇️ Click the button below to download the video",
        "prem_caption_title": "🔗 <b>Direct Download:</b>",
        "file_count": "📁 <b>Total Files: {count}</b>\n",
        "dl_share_prefix": "⬇️ Download:",
        "btn_dl_1": "Download 1",
        "btn_dl_2": "Download 2",
        "btn_tut_1": "🎬 How to Watch 1",
        "btn_tut_2": "🎬 How to Watch 2",
        "btn_share": "🔗 Share"
    },
    "bn": {
        "name": "বাংলা 🇧🇩",
        "badge": "🇧🇩 BN",
        "ad_caption_cta": "⬇️ ভিডিও ডাউনলোড করতে নিচের বাটনে ক্লিক করুন",
        "prem_caption_title": "🔗 <b>সরাসরি ডাউনলোড:</b>",
        "file_count": "📁 <b>মোট ফাইল: {count}টি</b>\n",
        "dl_share_prefix": "⬇️ ডাউনলোড করুন:",
        "btn_dl_1": "ডাউনলোড ১",
        "btn_dl_2": "ডাউনলোড ২",
        "btn_tut_1": "🎬 দেখার নিয়ম ১",
        "btn_tut_2": "🎬 দেখার নিয়ম ২",
        "btn_share": "🔗 শেয়ার করুন"
    },
    "hi": {
        "name": "हिंदी 🇮🇳",
        "badge": "🇮🇳 HI",
        "ad_caption_cta": "⬇️ वीडियो डाउनलोड करने के लिए नीचे दिए गए बटन पर क्लिक करें",
        "prem_caption_title": "🔗 <b>सीधा डाउनलोड:</b>",
        "file_count": "📁 <b>कुल फाइलें: {count}</b>\n",
        "dl_share_prefix": "⬇️ डाउनलोड करें:",
        "btn_dl_1": "डाउनलोड १",
        "btn_dl_2": "डाउनलोड २",
        "btn_tut_1": "🎬 कैसे देखें १",
        "btn_tut_2": "🎬 कैसे देखें २",
        "btn_share": "🔗 शेयर करें"
    }
}

def get_lang_tutorial(lang, num=1):
    """ভাষা ভিত্তিক গ্লোবাল টিউটোরিয়াল লিংক নিয়ে আসে"""
    key = f"lang_tut_{lang}_{num}"
    doc = settings_col.find_one({"key": key})
    return doc.get("value", "") if doc else ""

def set_lang_tutorial(lang, num, url):
    """ভাষা ভিত্তিক গ্লোবাল টিউটোরিয়াল লিংক সেট করে"""
    key = f"lang_tut_{lang}_{num}"
    settings_col.update_one({"key": key}, {"$set": {"key": key, "value": url.strip()}}, upsert=True)

def resolve_channel_tutorial(ch, cat, num, user=None):
    """চ্যানেল, ক্যাটাগরি এবং গ্লোবাল ল্যাংগুয়েজ প্রিসেট মিলিয়ে সঠিক টিউটোরিয়াল লিংক নির্ধারণ করে"""
    num_str = str(num)
    ch = ch or {}
    cat = cat or {}
    ch_lang = ch.get("lang") or cat.get("lang") or "en"
    if ch_lang not in LANG_TEXTS: ch_lang = "en"
    
    # ১. চ্যানেল স্পেসিফিক লিংক
    ch_tut = ch.get(f"tutorial_url_{num_str}")
    if ch_tut and str(ch_tut).strip(): return str(ch_tut).strip()
    
    # ২. ক্যাটাগরি ল্যাংগুয়েজ স্পেসিফিক লিংক (যেমন tutorial_url_1_bn)
    cat_lang_tut = cat.get(f"tutorial_url_{num_str}_{ch_lang}")
    if cat_lang_tut and str(cat_lang_tut).strip(): return str(cat_lang_tut).strip()
    
    # ৩. ক্যাটাগরি জেনারেল লিংক (যদি ভাষার সাথে মিলে বা ক্যাটাগরিতে সরাসরি সেট থাকে)
    cat_tut = cat.get(f"tutorial_url_{num_str}")
    if cat_tut and str(cat_tut).strip() and (not cat.get("lang") or cat.get("lang") == ch_lang):
        return str(cat_tut).strip()
        
    # ৪. গ্লোবাল ল্যাংগুয়েজ প্রিসেট (বাংলা, হিন্দি, ইংরেজি)
    global_tut = get_lang_tutorial(ch_lang, num)
    if global_tut and str(global_tut).strip(): return str(global_tut).strip()
    
    # ৫. ইউজার কাস্টম লিংক ফলব্যাক
    if user:
        u_link = user.get(f"custom_link_{num_str}")
        if u_link and str(u_link).strip(): return str(u_link).strip()
        
    return ""

def _post_to_category(cat_id, mtype, mid, user, d_link, s_link):
    cat = get_category(cat_id)
    if not cat:
        logger.warning(f"Category post: category not found for id {cat_id}")
        return 0

    ph = apply_filters(user.get('post_header',''), user['chat_id'])
    pf = apply_filters(user.get('post_footer',''), user['chat_id'])
    ph_t = f"{ph}\n\n" if ph else ""
    pf_t = f"\n\n{pf}" if pf else ""
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    protect = bool(get_setting("protect_content", 0))
    rpt = max(1, min(user.get("link_repeat_count", 1), 5))

    file_count = _get_file_count_from_link(d_link)

    count = 0
    channels_list = cat.get("channels", [])
    if not channels_list:
        logger.warning(f"Category post [{cat.get('name')}]: has no channels linked!")
        return 0

    for ch in channels_list:
        # স্ট্যাটাস চেক (ডিফল্ট on)
        st = ch.get("status", "on")
        if st not in ["on", True, "active", 1, "1", "ON"]:
            continue

        raw_ch_id = str(ch.get('channel_id', '')).strip()
        if not raw_ch_id:
            logger.warning(f"Category [{cat.get('name')}] channel has empty channel_id: {ch}")
            continue

        try:
            target_chat_id = int(raw_ch_id)
        except ValueError:
            target_chat_id = raw_ch_id

        ch_type = ch.get("type", "ad")
        
        # প্রতি চ্যানেলের নিজস্ব ভাষা নির্ধারণ
        ch_lang = ch.get("lang") or cat.get("lang") or "en"
        if ch_lang not in LANG_TEXTS: ch_lang = "en"
        L = LANG_TEXTS[ch_lang]
        fc_txt = L["file_count"].format(count=file_count) if file_count > 0 else ""

        tut1 = resolve_channel_tutorial(ch, cat, 1, user)
        tut2 = resolve_channel_tutorial(ch, cat, 2, user)

        merged_ctx = {
            "lang": ch_lang,
            "tutorial_url_1": tut1,
            "tutorial_url_2": tut2
        }

        if ch_type == "premium":
            link = d_link
            links_str = "\n".join([link]*rpt)
            caption = f"{ph_t}{fc_txt}{L['prem_caption_title']}\n{links_str}\n\n<i>🕐 {now_str}</i>{pf_t}".strip() if user.get("btn_link_in_caption",1) else f"{ph_t}{fc_txt}{pf_t}".strip()
            clean_cap = clean_html(caption)
            markup = _build_post_markup(user, d_link, clean_cap, is_premium=True, ch=merged_ctx)
        elif ch_type == "log":
            caption = f"💾 <b>Backup</b> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            markup = None
        else:  # ad
            caption = f"{ph_t}{fc_txt}{L['ad_caption_cta']}\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
            dl_share_link = user.get("pending_web_post_link") or s_link
            clean_cap = clean_html(caption) + f"\n\n{L['dl_share_prefix']}\n{dl_share_link}"
            markup = _build_post_markup(user, s_link, clean_cap, is_premium=False, ch=merged_ctx)

        try:
            _send_media(target_chat_id, mtype, mid, caption, markup, protect if ch_type!="log" else False)
            count += 1
        except Exception as e:
            logger.error(f"Category post failed for [{cat.get('name')} / {ch.get('name')} ({target_chat_id})]: {e}")

    return count

# ══════════════════════════════════════════════════
#  সিডিউল পোস্ট ওয়ার্কার
# ══════════════════════════════════════════════════
def _scheduled_post_worker():
    while True:
        try:
            now = datetime.now()
            due = list(scheduled_col.find({"status": "pending", "scheduled_at": {"$lte": now.isoformat()}}))
            for item in due:
                try:
                    admin_id = item['admin_id']
                    user = get_user(admin_id)
                    cat_id = item.get("category_id", "")
                    mtype  = item['media_type']
                    mid_   = item['media_id']
                    d_link = item.get('d_link','')
                    s_link = item.get('s_link','')
                    user["pending_link"] = d_link
                    user["pending_short_link"] = s_link
                    user["pending_web_title"] = item.get("web_title", "")
                    user["pending_thumb_url"] = item.get("thumb_url", "")
                    user["pending_web_video_id"] = item.get("web_video_id", "")
                    user["pending_web_post_link"] = item.get("web_post_link", "")
                    user["pending_web_ads"] = item.get("web_ads", 1)

                    if cat_id:
                        cat = get_category(cat_id)
                        if not user.get("pending_web_post_link"):
                            user["pending_web_post_link"] = create_web_video_entry(user, cat.get("name", "Others") if cat else "Others")
                        count = _post_to_category(cat_id, mtype, mid_, user, d_link, s_link)
                        cat_name = cat['name'] if cat else "Unknown"
                        try: bot.send_message(admin_id, f"⏰ <b>সিডিউল পোস্ট সম্পন্ন!</b>\n📂 ক্যাটাগরি: <b>{cat_name}</b>\n📤 {count}টি চ্যানেলে পোস্ট হয়েছে।")
                        except: pass
                    else:
                        if not user.get("pending_web_post_link"):
                            user["pending_web_post_link"] = create_web_video_entry(user, "Others")
                        ph = apply_filters(user.get('post_header',''), admin_id)
                        pf = apply_filters(user.get('post_footer',''), admin_id)
                        ph_t = f"{ph}\n\n" if ph else ""
                        pf_t = f"\n\n{pf}" if pf else ""
                        now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
                        protect = bool(get_setting("protect_content", 0))
                        rpt = max(1, min(user.get("link_repeat_count",1),5))
                        file_count = _get_file_count_from_link(d_link)
                        fc_txt = f"📁 <b>মোট ফাইল: {file_count}টি</b>\n" if file_count > 0 else ""

                        ad_cap = f"{ph_t}{fc_txt}⬇️ ভিডিও ডাউনলোড করতে নিচের বাটনে ক্লিক করুন\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
                        dl_share_link = user.get("pending_web_post_link") or s_link
                        ad_share = clean_html(ad_cap) + f"\n\n⬇️ ডাউনলোড করুন:\n{dl_share_link}"
                        ad_mk = _build_post_markup(user, s_link, ad_share)
                        
                        pr_links = "\n".join([d_link]*rpt)
                        pr_cap = f"{ph_t}{fc_txt}🔗 <b>Direct Download:</b>\n{pr_links}\n\n<i>🕐 {now_str}</i>{pf_t}".strip() if user.get("btn_link_in_caption",1) else f"{fc_txt}".strip()
                        pr_share = clean_html(pr_cap)
                        pr_mk = _build_post_markup(user, d_link, pr_share, is_premium=True)
                        count = 0
                        for ch in auto_channels_col.find({"type":"ad","status":"on"}):
                            try: _send_media(ch['channel_id'], mtype, mid_, ad_cap, ad_mk, protect); count+=1
                            except: pass
                        for ch in auto_channels_col.find({"type":"premium","status":"on"}):
                            try: _send_media(ch['channel_id'], mtype, mid_, pr_cap, pr_mk, protect); count+=1
                            except: pass
                        try: bot.send_message(admin_id, f"⏰ <b>সিডিউল পোস্ট সম্পন্ন!</b>\n📤 {count}টি চ্যানেলে পোস্ট হয়েছে।")
                        except: pass

                    scheduled_col.update_one({"_id": item["_id"]}, {"$set": {"status": "done", "posted_at": datetime.now().isoformat()}})
                except Exception as e:
                    logger.error(f"Scheduled post exec: {e}")
                    scheduled_col.update_one({"_id": item["_id"]}, {"$set": {"status": "error", "error": str(e)}})
        except Exception as e:
            logger.error(f"Scheduled post worker: {e}")
        time.sleep(30)

threading.Thread(target=_scheduled_post_worker, daemon=True).start()

def _get_file_count_from_link(link):
    try:
        m = re.search(r'[?&]start=([A-Za-z0-9]+)', link)
        if not m: return 0
        fk = m.group(1)
        cnt = files_col.count_documents({"batch_id": fk})
        if cnt > 0: return cnt
        cnt = files_col.count_documents({"file_key": fk})
        return cnt
    except:
        return 0

def get_short_link(url):
    try:
        r = requests.get(f"https://teraboxlinks.com/api?api={TERABOX_TOKEN}&url={quote(url)}", timeout=8).json()
        if r and r.get("status") != "error" and r.get("shortenedUrl"):
            return r["shortenedUrl"]
    except Exception as e: logger.warning(f"ShortLink: {e}")
    return url

def upload_photo_to_imgbb(file_id):
    if not IMGBB_API_KEY:
        logger.warning("IMGBB_API_KEY not configured; thumbnail upload skipped.")
        return ""
    try:
        tg_file = bot.get_file(file_id)
        raw = bot.download_file(tg_file.file_path)
        payload = {"key": IMGBB_API_KEY, "image": base64.b64encode(raw).decode("ascii")}
        r = requests.post("https://api.imgbb.com/1/upload", data=payload, timeout=25).json()
        if r.get("success") and r.get("data", {}).get("url"):
            return r["data"]["url"]
        logger.warning(f"ImgBB upload failed: {r}")
    except Exception as e:
        logger.warning(f"ImgBB upload error: {e}")
    return ""

def _web_app_post_link(video_id):
    return f"https://t.me/{WEBBOT_USERNAME}/app?startapp={video_id}"

def create_web_video_entry(user, category_name="Others"):
    existing_id = user.get("pending_web_video_id", "")
    existing_link = user.get("pending_web_post_link", "")
    if existing_id and existing_link:
        return existing_link

    if not FIREBASE_DB_URL:
        logger.warning("FIREBASE_DB_URL not configured; web video entry skipped.")
        return ""

    title = (user.get("pending_web_title") or user.get("post_header") or "Untitled Video").strip()
    title = apply_filters(title, user["chat_id"])
    if not title:
        title = "Untitled Video"
        
    ads_count = int(user.get("pending_web_ads", 1))
    data = {
        "title": title, 
        "category": category_name or "Others",
        "thumb": user.get("pending_thumb_url", ""), 
        "url": user.get("pending_link", ""),
        "watchAds": ads_count,          # ✅ মেইন আনলক বাটনের জন্য অ্যাড সংখ্যা
        "fullCollectionUrl": "", 
        "fullCollectionAds": ads_count, # Full Collection এর জন্য অ্যাড সংখ্যা
        "views": 0, "likes": 0, "dislikes": 0, "posted": False,
        "timestamp": int(time.time() * 1000), "source": "shortener_bot",
    }
    try:
        clean_url = FIREBASE_DB_URL.rstrip("/")
        r = requests.post(f"{clean_url}/videos.json", json=data, timeout=15).json()
        video_id = r.get("name")
        if video_id:
            post_link = _web_app_post_link(video_id)
            update_user(user["chat_id"], {"pending_web_video_id": video_id, "pending_web_post_link": post_link})
            return post_link
        logger.warning(f"Firebase video create failed: {r}")
    except Exception as e:
        logger.warning(f"Firebase video create error: {e}")
    return ""

# ══════════════════════════════════════════════════
#  পোস্ট মার্কআপ বিল্ডার
# ══════════════════════════════════════════════════
def _build_post_markup(user, dl_link, share_text, is_premium=False, ch=None):
    mk = InlineKeyboardMarkup()
    ch = ch or {}
    lang = ch.get("lang", "en")
    if lang not in LANG_TEXTS: lang = "en"
    L = LANG_TEXTS[lang]

    if user.get("btn_tutorial", 1):
        for tut in tutorials_col.find():
            if tut.get("url") and str(tut["url"]).strip():
                mk.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=str(tut['url']).strip()))

    for btn in user.get("custom_buttons", []):
        if btn.get("status") == "on" and btn.get("url") and str(btn["url"]).strip():
            mk.add(InlineKeyboardButton(btn['name'], url=str(btn['url']).strip()))

    if user.get("btn_download", 1):
        if is_premium:
            web_post_link = dl_link
            second_link = dl_link
        else:
            web_post_link = user.get("pending_web_post_link", "") or user.get("pending_short_link") or dl_link
            second_link = user.get("pending_short_link") or dl_link
        
        # Row 1: Download 1 + Tutorial 1 (only if valid URL exists)
        if user.get("btn_download_1", 1):
            row1 = []
            btn_dl1_label = L["btn_dl_1"]
            target_dl1 = web_post_link or second_link or dl_link
            if target_dl1 and str(target_dl1).strip():
                row1.append(InlineKeyboardButton(btn_dl1_label, url=str(target_dl1).strip()))
                
            ct1_url = ch.get("tutorial_url_1") or user.get("custom_link_1")
            ct1_text = user.get("custom_text_1") if (not ch.get("tutorial_url_1") and user.get("custom_text_1")) else L["btn_tut_1"]
            if ct1_url and str(ct1_url).strip().startswith("http"):
                row1.append(InlineKeyboardButton(ct1_text, url=str(ct1_url).strip()))
            
            if row1:
                mk.row(*row1)

        # Row 2: Download 2 + Tutorial 2 (only if valid URL exists)
        if user.get("btn_download_2", 1):
            row2 = []
            btn_dl2_label = L["btn_dl_2"]
            target_dl2 = second_link or web_post_link or dl_link
            if target_dl2 and str(target_dl2).strip():
                row2.append(InlineKeyboardButton(btn_dl2_label, url=str(target_dl2).strip()))
                
            ct2_url = ch.get("tutorial_url_2") or user.get("custom_link_2")
            ct2_text = user.get("custom_text_2") if (not ch.get("tutorial_url_2") and user.get("custom_text_2")) else L["btn_tut_2"]
            if ct2_url and str(ct2_url).strip().startswith("http"):
                row2.append(InlineKeyboardButton(ct2_text, url=str(ct2_url).strip()))
                
            if row2:
                mk.row(*row2)

    if user.get("btn_share", 1) and share_text:
        encoded = quote(share_text, safe='')
        share_url = f"https://t.me/share/url?url=&text={encoded}"
        mk.row(InlineKeyboardButton(L["btn_share"], url=share_url))

    return mk if (mk.keyboard and len(mk.keyboard) > 0) else None

# ══════════════════════════════════════════════════
#  পোস্ট অপশন
# ══════════════════════════════════════════════════
def _ask_post_options(chat_id, user, mtype, mid):
    update_user(chat_id, {"temp_media_id": mid, "temp_media_type": mtype, "step":"wait_post_options"})
    cats = get_categories()
    m = _mk()
    if cats:
        for c in cats:
            m.add(_btn(f"📂 {c['name']}", f"postcat_{c['cat_id']}"))
        m.add(_btn("🌐 সব চ্যানেলে পোস্ট করো","postcat_all"))
    else:
        m.add(_btn("📤 এখনই পোস্ট করো","postcat_all"))
    
    # অ্যাড সংখ্যা সেট করার বাটন (নতুন)
    m.add(_btn(f"🔢 অ্যাড সংখ্যা: {user.get('pending_web_ads', 1)}টি", "set_ads_count"))
    
    m.add(_btn("⏰ সিডিউল করুন","ask_schedule"))
    bot.send_message(chat_id,
        "📂 <b>পোস্ট অপশন সিলেক্ট করুন</b>\n\nকোন ক্যাটাগরিতে পোস্ট করবেন?\nঅথবা সিডিউল করতে ⏰ বাটনে চাপুন।",
        reply_markup=m
    )

def _ask_web_title(chat_id, user, mtype, mid):
    update_user(chat_id, {"temp_media_id": mid, "temp_media_type": mtype, "step": "wait_web_title"})
    default_title = (user.get("post_header") or "").strip()
    hint = f"\n\nবর্তমান caption/title: <b>{default_title[:80]}</b>" if default_title else ""
    m = InlineKeyboardMarkup()
    m.row(
        InlineKeyboardButton("Use Post Title 📝", callback_data="use_post_title"),
        InlineKeyboardButton("Save Title 💾", callback_data="use_saved_title")
    )
    m.row(
        InlineKeyboardButton("No Title 🚫", callback_data="use_no_title")
    )
    bot.send_message(
        chat_id,
        "ভিডিওর title/name দিন।\nনা দিতে চাইলে বাটন ব্যবহার করুন বা <code>/skip</code> লিখুন।"
        f"{hint}",
        reply_markup=m
    )

def _send_media(ch_id, mtype, mid, caption, markup, protect=False):
    kw = {"caption": caption, "reply_markup": markup, "protect_content": False}
    if mtype == 'photo': bot.send_photo(ch_id, mid, **kw)
    elif mtype == 'video': bot.send_video(ch_id, mid, **kw)
    elif mtype == 'document': bot.send_document(ch_id, mid, **kw)
    elif mtype == 'audio': bot.send_audio(ch_id, mid, **kw)

def execute_channel_post(chat_id, user, mtype, mid, scheduled_at=None):
    d_link = user.get("pending_link", "")
    s_link = user.get("pending_short_link", "")

    if scheduled_at:
        sched_id = str(uuid.uuid4().hex)[:10]
        scheduled_col.insert_one({
            "sched_id": sched_id, "admin_id": chat_id, "media_type": mtype, "media_id": mid,
            "d_link": d_link, "s_link": s_link, "category_id": user.get("pending_category",""),
            "web_title": user.get("pending_web_title", ""), "thumb_url": user.get("pending_thumb_url", ""),
            "web_video_id": "", "web_post_link": "",
            "web_ads": user.get("pending_web_ads", 1),
            "scheduled_at": scheduled_at, "status": "pending", "created_at": datetime.now().isoformat()
        })
        cat = get_category(user.get("pending_category","")) if user.get("pending_category") else None
        cat_name = f"📂 {cat['name']}" if cat else "🌐 সব চ্যানেল"
        time_txt = "অনির্ধারিত (প্যানেল থেকে সেট করুন)" if "203" in scheduled_at else scheduled_at[:16].replace('T',' ')
        bot.send_message(chat_id, f"⏰ <b>সিডিউল পোস্ট সেভ হয়েছে!</b>\n📅 সময়: <b>{time_txt}</b>\n📌 ক্যাটাগরি: {cat_name}\n🆔 ID: <code>{sched_id}</code>\n\n💡 <i>নির্ধারিত সময়ে পোস্ট হওয়ার সময় স্বয়ংক্রিয়ভাবে ওয়েবসাইট ও ওয়েব বটে ভিডিওটি যোগ হবে।</i>")
        update_user(chat_id, {"step":"none","pending_link":"","pending_short_link":"","pending_category":"","pending_schedule":"","temp_media_id":"","temp_media_type":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
        return

    cat_id = user.get("pending_category","")
    if cat_id:
        cat = get_category(cat_id)
        count = _post_to_category(cat_id, mtype, mid, user, d_link, s_link)
        protect = bool(get_setting("protect_content", 0))
        _inc_stat("uploads")
        bot.send_message(chat_id, f"✅ <b>পোস্ট সম্পন্ন!</b>\n📂 ক্যাটাগরি: <b>{cat['name'] if cat else 'Unknown'}</b>\n📤 <b>{count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n🔒 Protect: {_ico(protect)} | 🔗 LF: {_ico(user.get('link_filter'))} | 📝 TF: {_ico(user.get('text_filter'))}")
        update_user(chat_id, {"step":"none","pending_link":"","pending_short_link":"","pending_category":"","temp_media_id":"","temp_media_type":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
        return

    cats = get_categories()
    if cats:
        update_user(chat_id, {"temp_media_id": mid, "temp_media_type": mtype, "step": "wait_category_select"})
        m = _mk()
        for c in cats:
            m.add(_btn(f"📂 {c['name']}", f"postcat_{c['cat_id']}"))
        m.add(_btn("🌐 সব চ্যানেলে পোস্ট করো","postcat_all"))
        bot.send_message(chat_id, "📂 <b>কোন ক্যাটাগরিতে পোস্ট করবেন?</b>\n\nএকটি ক্যাটাগরি সিলেক্ট করুন বা সব চ্যানেলে পোস্ট করুন।", reply_markup=m)
        return

    _do_post_all_channels(chat_id, user, mtype, mid, d_link, s_link)

def _do_post_all_channels(chat_id, user, mtype, mid, d_link, s_link):
    ph = apply_filters(user.get('post_header',''), chat_id)
    pf = apply_filters(user.get('post_footer',''), chat_id)
    ph_t = f"{ph}\n\n" if ph else ""
    pf_t = f"\n\n{pf}" if pf else ""
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    protect = bool(get_setting("protect_content", 0))
    rpt = max(1, min(user.get("link_repeat_count", 1), 5))

    file_count = _get_file_count_from_link(d_link)

    post_count = 0
    for ch in auto_channels_col.find({"status": "on"}):
        ch_type = ch.get("type", "ad")
        lang = ch.get("lang", "en")
        if lang not in LANG_TEXTS: lang = "en"
        L = LANG_TEXTS[lang]
        fc_txt = L["file_count"].format(count=file_count) if file_count > 0 else ""

        tut1 = resolve_channel_tutorial(ch, None, 1, user)
        tut2 = resolve_channel_tutorial(ch, None, 2, user)
        ch_ctx = {
            "lang": lang,
            "tutorial_url_1": tut1,
            "tutorial_url_2": tut2
        }

        if ch_type == "premium":
            pr_links = "\n".join([d_link]*rpt)
            prem_caption = f"{ph_t}{fc_txt}{L['prem_caption_title']}\n{links_str}\n\n<i>🕐 {now_str}</i>{pf_t}".strip() if user.get("btn_link_in_caption",1) else f"{ph_t}{fc_txt}{pf_t}".strip()
            pr_share = clean_html(prem_caption)
            prem_markup = _build_post_markup(user, d_link, pr_share, is_premium=True, ch=ch_ctx)
            try:
                _send_media(ch['channel_id'], mtype, mid, prem_caption, prem_markup, protect)
                post_count += 1
            except Exception as e:
                logger.warning(f"Premium post {ch.get('name')}: {e}")
        elif ch_type == "log":
            try:
                log_cap = f"💾 <b>Backup</b> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                _send_media(ch['channel_id'], mtype, mid, log_cap, None, False)
            except Exception as e:
                logger.warning(f"Log post: {e}")
        else:  # ad
            ad_caption = f"{ph_t}{fc_txt}{L['ad_caption_cta']}\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
            dl_share_link = user.get("pending_web_post_link") or s_link
            ad_share = clean_html(ad_caption) + f"\n\n{L['dl_share_prefix']}\n{dl_share_link}"
            ad_markup = _build_post_markup(user, s_link, ad_share, is_premium=False, ch=ch_ctx)
            try:
                _send_media(ch['channel_id'], mtype, mid, ad_caption, ad_markup, protect)
                post_count += 1
            except Exception as e:
                logger.warning(f"Ad post {ch.get('name')}: {e}")

    _inc_stat("uploads")
    bot.send_message(chat_id, f"✅ <b>পোস্ট সম্পন্ন!</b>\n📤 <b>{post_count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n🔒 Protect: {_ico(protect)} | 🔗 LF: {_ico(user.get('link_filter'))} | 📝 TF: {_ico(user.get('text_filter'))}")
    update_user(chat_id, {"step":"none","pending_link":"","pending_short_link":"","pending_category":"","temp_media_id":"","temp_media_type":"","post_header":"","post_footer":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})

# ══════════════════════════════════════════════════
#  ফাইল ডেলিভারি
# ══════════════════════════════════════════════════
def _deliver_files(chat_id, file_key, user, is_unlocked=False):
    if file_key.startswith("unprotect_"):
        file_key = file_key[10:]
        is_unlocked = True

    files = list(files_col.find({"$or": [{"file_key": file_key}, {"batch_id": file_key}]}))
    if not files:
        bot.send_message(chat_id, "❌ <b>ফাইল পাওয়া যায়নি!</b>\nলিংকটি মেয়াদোত্তীর্ণ হতে পারে।")
        return

    bot.send_message(chat_id, f"⏳ {'ফাইলগুলো' if len(files)>1 else 'ফাইলটি'} পাঠানো হচ্ছে...")
    uploader = get_user(files[0]['uploader'])
    h  = uploader.get('header','')
    f_ = uploader.get('footer','')
    caption = apply_filters(f"{h}\n\n{f_}".strip() if (h or f_) else "", files[0]['uploader'])

    mk = InlineKeyboardMarkup()
    if uploader.get("btn_tutorial", 1):
        for tut in tutorials_col.find():
            mk.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
    for ch in channels_col.find():
        mk.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))

    protect  = bool(get_setting("protect_content", 0))

    if protect and not is_unlocked:
        web_app_url = f"https://t.me/{WEBBOT_USERNAME}/app?startapp=dl_unprotect_{file_key}"
        mk.add(InlineKeyboardButton("🔓 গ্যালারিতে সেভ/ডাউনলোড (অ্যাড দেখুন)", url=web_app_url))
    elif is_unlocked:
        protect = False
        caption = "✅ <b>ডাউনলোড ও সেভ আনলক সম্পন্ন!</b>\n\nফাইলটি নিচে দেওয়া হলো। এখন আপনি এটি সরাসরি ডাউনলোড বা গ্যালারিতে সেভ করতে পারবেন।\n\n" + caption

    delivered = 0

    for f in files:
        sent_id = None
        kw = {"caption": caption, "reply_markup": mk if mk.keyboard else None, "protect_content": protect}
        try:
            res = None
            if f['type']=='document': res = bot.send_document(chat_id, f['file_id'], **kw)
            elif f['type']=='video':  res = bot.send_video(chat_id,    f['file_id'], **kw)
            elif f['type']=='photo':  res = bot.send_photo(chat_id,    f['file_id'], **kw)
            elif f['type']=='audio':  res = bot.send_audio(chat_id,    f['file_id'], caption=caption, reply_markup=kw['reply_markup'], protect_content=protect)
            if res: sent_id = res.message_id; delivered += 1
        except:
            if f.get('log_chat_id') and f.get('log_msg_id'):
                try:
                    res = bot.copy_message(chat_id, f['log_chat_id'], f['log_msg_id'], caption=caption, reply_markup=mk if mk.keyboard else None, protect_content=protect)
                    sent_id = res.message_id; delivered += 1
                except: pass

        if sent_id and uploader.get("auto_delete", 0) > 0:
            queue_col.insert_one({"chat_id": chat_id, "message_id": sent_id, "delete_at": int(time.time()) + uploader["auto_delete"]*60})
        time.sleep(0.3)

    if delivered:
        _inc_stat("downloads", delivered)
        update_user(chat_id, {"total_downloads": user.get("total_downloads",0)+delivered})
        if uploader.get("auto_delete",0) > 0:
            bot.send_message(chat_id, f"⚠️ <i>ফাইল{'গুলো' if delivered>1 else 'টি'} <b>{uploader['auto_delete']} মিনিট</b> পর মুছে যাবে।</i>")
    else:
        bot.send_message(chat_id, "❌ ফাইল পাঠানো সম্ভব হয়নি।")

# ══════════════════════════════════════════════════
#  মেনু হেল্পার
# ══════════════════════════════════════════════════
def _mk(): return InlineKeyboardMarkup()
def _back(cb): return InlineKeyboardButton("🔙 ব্যাক", callback_data=cb)
def _btn(label, cb): return InlineKeyboardButton(label, callback_data=cb)

def _main_menu():
    m = _mk()
    m.add(_btn("📦 ব্যাচ আপলোড", "start_batch"))
    m.row(_btn("⚙️ সেটিংস", "settings"), _btn("📊 স্ট্যাটস", "show_stats"))
    m.row(_btn("📢 ব্রডকাস্ট", "broadcast"), _btn("⏰ সিডিউল", "menu_schedule"))
    m.row(_btn("📂 ক্যাটাগরি", "menu_categories"), _btn("ℹ️ হেল্প", "help_menu"))
    return m

def _admin_reply_keyboard():
    rk = ReplyKeyboardMarkup(resize_keyboard=True)
    rk.row(KeyboardButton("📦 ব্যাচ আপলোড"), KeyboardButton("🤖 এডমিন প্যানেল"))
    rk.row(KeyboardButton("❌ বাতিল"))
    return rk

def _post_btn_menu(u):
    dl  = _ico(u.get("btn_download",1))
    dl1 = _ico(u.get("btn_download_1",1))
    dl2 = _ico(u.get("btn_download_2",1))
    sh  = _ico(u.get("btn_share",1))
    tut = _ico(u.get("btn_tutorial",1))
    lc  = _ico(u.get("btn_link_in_caption",1))
    at  = _ico(u.get("auto_title_from_caption",1))
    rc  = u.get("link_repeat_count",1)

    m = _mk()
    m.row(_btn(f"📥 ডাউনলোড ১ বাটন {dl1}", "togbtn_download_1"), _btn(f"📥 ডাউনলোড ২ বাটন {dl2}", "togbtn_download_2"))
    m.row(_btn(f"📥 মাষ্টার ডাউনলোড {dl}", "togbtn_download"), _btn(f"🔗 শেয়ার বাটন {sh}",    "togbtn_share"))
    m.row(_btn(f"📽️ টিউটোরিয়াল {tut}",   "togbtn_tutorial"), _btn(f"📝 ক্যাপশনে লিংক {lc}", "togbtn_link_caption"))
    m.row(_btn(f"🤖 অটো টাইটেল {at}",     "togbtn_auto_title"), _btn(f"🔄 লিংক রিপিট: {rc}x", "set_link_repeat"))
    m.add(_btn("🔘 কাস্টম ডাউনলোড টেক্সট/লিংক", "menu_custom_dl"))
    m.add(_btn("➕ সাধারণ কাস্টম বাটন", "menu_custom_buttons"))
    m.add(_back("menu_post_settings"))
    return m

# ══════════════════════════════════════════════════
#  কলব্যাক হ্যান্ডলার
# ══════════════════════════════════════════════════
@bot.callback_query_handler(func=lambda call: True)
def cb(call):
    cid  = str(call.message.chat.id)
    mid  = call.message.message_id
    data = call.data
    user = get_user(cid)

    if is_banned(cid):
        bot.answer_callback_query(call.id, "🚫 আপনি ব্যান করা হয়েছেন!", show_alert=True); return

    if data.startswith("check_sub_"):
        fk = data[10:]
        joined, nj = check_force_sub(cid)
        if joined:
            bot.answer_callback_query(call.id, "✅ Join নিশ্চিত হয়েছে!", show_alert=True)
            try: bot.delete_message(cid, mid)
            except: pass
            if fk and fk != "none": _deliver_files(cid, fk, user)
        else:
            bot.answer_callback_query(call.id, "❌ এখনো সব চ্যানেলে Join করেননি!", show_alert=True)
        return

    if not is_admin(cid):
        bot.answer_callback_query(call.id, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!", show_alert=True); return

    if data == "autogen_thumb":
        user = get_user(cid)
        pending_link = user.get("pending_link", "")
        match = re.search(r'[?&]start=([A-Za-z0-9]+)', pending_link)
        auto_thumb_url = None
        orig_file_id = None
        orig_file_type = None
        thumb_file_id = None
        key = None
        file_doc = None
        
        if match:
            key = match.group(1)
            file_doc = files_col.find_one({"$or": [{"file_key": key}, {"batch_id": key}]})
            if file_doc:
                orig_file_id = file_doc.get("file_id")
                orig_file_type = file_doc.get("type")
                auto_thumb_url = file_doc.get("auto_thumb_url")
                thumb_file_id = file_doc.get("thumb_file_id")
                
                # Check batch for auto_thumb_url
                if not auto_thumb_url:
                    other_doc = files_col.find_one({"batch_id": key, "auto_thumb_url": {"$exists": True, "$ne": ""}})
                    if other_doc:
                        auto_thumb_url = other_doc.get("auto_thumb_url")
                
                # Check batch for thumb_file_id
                if not thumb_file_id:
                    other_doc_thumb = files_col.find_one({"batch_id": key, "thumb_file_id": {"$exists": True, "$ne": None}})
                    if other_doc_thumb:
                        thumb_file_id = other_doc_thumb.get("thumb_file_id")
        
        if not orig_file_id:
            bot.answer_callback_query(call.id, "❌ কোনো ফাইল পাওয়া যায়নি!", show_alert=True)
            return
            
        if not thumb_file_id:
            bot.answer_callback_query(call.id, "⚠️ এই ফাইল থেকে থাম্বনেইল জেনারেট করা সম্ভব নয়!", show_alert=True)
            return
            
        # Fallback if auto_thumb_url is missing but thumb_file_id is present (e.g. older files)
        if not auto_thumb_url:
            bot.answer_callback_query(call.id, "⏳ থাম্বনেইল ডাউনলোড ও আপলোড হচ্ছে...")
            status_msg = bot.send_message(cid, "⏳ ভিডিও থাম্বনেইল আপলোড হচ্ছে...")
            thumb_url = upload_photo_to_imgbb(thumb_file_id)
            if thumb_url:
                auto_thumb_url = thumb_url
                try: bot.delete_message(cid, status_msg.message_id)
                except: pass
            else:
                try: bot.delete_message(cid, status_msg.message_id)
                except: pass
                auto_thumb_url = ""

        bot.answer_callback_query(call.id, "⏳ থাম্বনেইল সেট করা হচ্ছে...")
        
        update_user(cid, {
            "pending_thumb_url": auto_thumb_url,
            "temp_media_id": thumb_file_id,
            "temp_media_type": "photo",
            "last_thumb_file_id": thumb_file_id or "",
            "step": "none"
        })
        
        if auto_thumb_url:
            bot.send_message(cid, "✅ থাম্বনেইল অটো-জেনারেট করা হয়েছে।")
            try: bot.delete_message(cid, mid)
            except: pass
            user2 = get_user(cid)
            _ask_web_title(cid, user2, "photo", thumb_file_id)
        else:
            m = InlineKeyboardMarkup()
            m.row(
                InlineKeyboardButton("🔄 রি-আপলোড ImgBB", callback_data="reupload_thumb_imgbb"),
                InlineKeyboardButton("🔗 ম্যানুয়াল লিংক", callback_data="manual_thumb_url")
            )
            m.row(
                InlineKeyboardButton("⏩ স্কিপ / কনটিনিউ", callback_data="continue_no_thumb")
            )
            try: bot.delete_message(cid, mid)
            except: pass
            bot.send_message(cid, "⚠️ ImgBB সার্ভার সমস্যার কারণে থাম্বনেইল আপলোড করা যায়নি।\nনিচের অপশন সিলেক্ট করুন:", reply_markup=m)
        return

    if data == "skip_thumb":
        user = get_user(cid)
        pending_link = user.get("pending_link", "")
        match = re.search(r'[?&]start=([A-Za-z0-9]+)', pending_link)
        orig_file_id = None
        orig_file_type = None
        
        if match:
            key = match.group(1)
            file_doc = files_col.find_one({"$or": [{"file_key": key}, {"batch_id": key}]})
            if file_doc:
                orig_file_id = file_doc.get("file_id")
                orig_file_type = file_doc.get("type")
                
        if not orig_file_id:
            bot.answer_callback_query(call.id, "❌ কোনো ফাইল পাওয়া যায়নি!", show_alert=True)
            return
            
        bot.answer_callback_query(call.id, "⏩ থাম্বনেইল স্কিপ করা হয়েছে।")
        update_user(cid, {
            "pending_thumb_url": "",
            "temp_media_id": orig_file_id,
            "temp_media_type": orig_file_type,
            "step": "none"
        })
        try: bot.delete_message(cid, mid)
        except: pass
        user2 = get_user(cid)
        _ask_web_title(cid, user2, orig_file_type, orig_file_id)
        return

    if data == "confirm_vid_thumb":
        bot.delete_message(cid, mid)
        _ask_web_title(cid, user, user.get("temp_media_type"), user.get("temp_media_id")); return
    if data == "cancel_vid_thumb":
        bot.delete_message(cid, mid)
        update_user(cid, {"step":"wait_thumbnail","temp_media_id":"","temp_media_type":""})
        bot.send_message(cid, "❌ বাতিল। নতুন থাম্বনেইল দিন।"); return

    if data == "manual_thumb_url":
        try: bot.delete_message(cid, mid)
        except: pass
        update_step(cid, "wait_manual_thumb_url")
        bot.send_message(
            cid,
            "🖼️ <b>ম্যানুয়াল থাম্বনেইল ইমেজ লিংক দিন</b>\n\n"
            "ছবির সরাসরি URL লিখে পাঠান (যেমন: <code>https://i.ibb.co/xxxx/image.jpg</code>)\n\n"
            "অথবা স্কিপ করতে <code>/skip</code> লিখুন।"
        )
        return

    if data == "reupload_thumb_imgbb":
        user = get_user(cid)
        last_fid = user.get("last_thumb_file_id") or user.get("temp_media_id")
        if not last_fid:
            bot.answer_callback_query(call.id, "❌ কোনো থাম্বনেইল ফাইল পাওয়া যায়নি!", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "⏳ ImgBB তে আবার আপলোড করা হচ্ছে...")
        status_msg = bot.send_message(cid, "⏳ ImgBB তে রি-আপলোড হচ্ছে...")
        thumb_url = upload_photo_to_imgbb(last_fid)
        try: bot.delete_message(cid, status_msg.message_id)
        except: pass
        
        if thumb_url:
            update_user(cid, {"pending_thumb_url": thumb_url})
            bot.send_message(cid, "✅ থাম্বনেইল সফলভাবে ImgBB তে রি-আপলোড হয়েছে!")
            try: bot.delete_message(cid, mid)
            except: pass
            mtype = user.get("temp_media_type") or "photo"
            _ask_web_title(cid, get_user(cid), mtype, last_fid)
        else:
            m = InlineKeyboardMarkup()
            m.row(
                InlineKeyboardButton("🔄 আবার চেষ্টা করুন", callback_data="reupload_thumb_imgbb"),
                InlineKeyboardButton("🔗 ম্যানুয়াল লিংক", callback_data="manual_thumb_url")
            )
            m.row(
                InlineKeyboardButton("⏩ স্কিপ / কনটিনিউ", callback_data="continue_no_thumb")
            )
            bot.send_message(cid, "❌ আবার চেষ্টা করা হয়েছে কিন্তু ImgBB আপলোড ফেল হয়েছে। ম্যানুয়াল লিংক ব্যবহার করতে পারেন:", reply_markup=m)
        return

    if data == "continue_no_thumb":
        try: bot.delete_message(cid, mid)
        except: pass
        user = get_user(cid)
        mtype = user.get("temp_media_type") or "photo"
        mid_val = user.get("temp_media_id") or ""
        _ask_web_title(cid, user, mtype, mid_val)
        return

    if data in ["use_post_title", "skip_web_title"]:
        try: bot.delete_message(cid, mid)
        except: pass
        user = get_user(cid)
        title = (user.get("post_header") or "Untitled Video").strip()
        if not title:
            title = "Untitled Video"
        update_user(cid, {"pending_web_title": title, "post_header": title, "step": "none"})
        user2 = get_user(cid)
        _ask_post_options(cid, user2, user2.get("temp_media_type"), user2.get("temp_media_id"))
        return

    if data == "use_saved_title":
        user = get_user(cid)
        title = user.get("saved_title", "").strip()
        if not title:
            bot.answer_callback_query(call.id, "⚠️ সেটিংস থেকে প্রথমে Save Title সেট করুন!", show_alert=True)
            return
        
        bot.answer_callback_query(call.id, "✅ সেভ টাইটেল ব্যবহার করা হয়েছে।")
        try: bot.delete_message(cid, mid)
        except: pass
        
        update_user(cid, {"pending_web_title": title, "post_header": title, "step": "none"})
        user2 = get_user(cid)
        _ask_post_options(cid, user2, user2.get("temp_media_type"), user2.get("temp_media_id"))
        return

    if data == "use_no_title":
        try: bot.delete_message(cid, mid)
        except: pass
        bot.answer_callback_query(call.id, "✅ টাইটেল ছাড়া পোস্ট করা হবে।")
        update_user(cid, {"pending_web_title": "", "post_header": "", "step": "none"})
        user2 = get_user(cid)
        _ask_post_options(cid, user2, user2.get("temp_media_type"), user2.get("temp_media_id"))
        return

    # ── Ads Count Selector (নতুন) ──
    if data == "set_ads_count":
        user = get_user(cid)
        m = _mk()
        for i in range(1, 6):
            m.add(_btn(f"{i}টি অ্যাড", f"setads_{i}"))
        m.add(_btn("✏️ কাস্টম সংখ্যা", "setads_custom"))
        m.add(_btn("🔙 ব্যাক", "back_to_post_options"))
        try:
            bot.edit_message_text(f"🔢 <b>কতটি অ্যাড দেখতে হবে নির্বাচন করুন</b>\nবর্তমান: <b>{user.get('pending_web_ads', 1)}টি</b>", cid, mid, reply_markup=m)
        except:
            bot.send_message(cid, f"🔢 <b>কতটি অ্যাড দেখতে হবে নির্বাচন করুন</b>\nবর্তমান: <b>{user.get('pending_web_ads', 1)}টি</b>", reply_markup=m)
        return
        
    elif data == "back_to_post_options":
        try: bot.delete_message(cid, mid)
        except: pass
        user = get_user(cid)
        _ask_post_options(cid, user, user.get("temp_media_type"), user.get("temp_media_id"))
        return
        
    elif data.startswith("setads_"):
        val = data[7:]
        if val == "custom":
            update_step(cid, "wait_custom_ads")
            bot.send_message(cid, "✏️ কাস্টম অ্যাড সংখ্যা লিখুন (যেমন: 7):")
        else:
            update_user(cid, {"pending_web_ads": int(val)})
            user = get_user(cid)
            try: bot.delete_message(cid, mid)
            except: pass
            _ask_post_options(cid, user, user.get("temp_media_type"), user.get("temp_media_id"))
        return

    if data == "main_menu":
        update_step(cid, "none")
        s = get_stats()
        bot.edit_message_text(
            f"╔══════════════════════════╗\n║   🤖 <b>এডমিন প্যানেল</b>   ║\n╚══════════════════════════╝\n\n👥 মোট ইউজার : <b>{s['total_users']}</b>\n📁 মোট ফাইল  : <b>{s['total_files']}</b>\n🟢 আজ সক্রিয় : <b>{s['active_today']}</b>\n📥 আজ ডাউনলোড: <b>{s['dl_today']}</b>",
            cid, mid, reply_markup=_main_menu()
        )

    elif data == "show_stats":
        s = get_stats(); m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n{'─'*26}\n👥 মোট ইউজার   : <b>{s['total_users']}</b>\n🟢 আজ সক্রিয়   : <b>{s['active_today']}</b>\n📁 মোট ফাইল    : <b>{s['total_files']}</b>\n📥 আজ ডাউনলোড : <b>{s['dl_today']}</b>\n📤 আজ আপলোড   : <b>{s['ul_today']}</b>\n👑 এডমিন       : <b>{s['total_admins']}</b>\n🚫 ব্যানড       : <b>{s['total_banned']}</b>\n{'─'*26}\n🕐 {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            cid, mid, reply_markup=m
        )

    elif data == "broadcast":
        m = _mk()
        m.row(_btn("📡 সবাইকে", "bc_all"), _btn("🟢 সক্রিয়দের", "bc_active"))
        m.add(_back("main_menu"))
        bot.edit_message_text("📢 <b>ব্রডকাস্ট</b>\nকাদের কাছে পাঠাবেন?", cid, mid, reply_markup=m)

    elif data in ["bc_all","bc_active"]:
        tgt = "all" if data=="bc_all" else "active"
        update_user(cid, {"step": f"wait_broadcast_{tgt}"})
        bot.send_message(cid, "📢 ব্রডকাস্টের মেসেজ/ছবি/ভিডিও পাঠান:")

    elif data == "start_batch":
        bid = str(uuid.uuid4().hex)[:10]
        update_user(cid, {"batch_id": bid, "step": "wait_batch"})
        m = _mk(); m.add(_btn("✅ আপলোড শেষ — Finish", "finish_batch"))
        bot.edit_message_text("📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n\nফাইলগুলো একে একে পাঠান।\nশেষ হলে Finish বাটনে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data == "finish_batch":
        bid = user.get("batch_id")
        if not bid:
            bot.answer_callback_query(call.id, "⚠️ ব্যাচ আগেই শেষ!", show_alert=True); return
        cnt = files_col.count_documents({"batch_id": bid})
        if cnt == 0:
            bot.answer_callback_query(call.id, "⚠️ কোনো ফাইল যোগ হয়নি!", show_alert=True); return
        bot.edit_message_text("⏳ লিংক তৈরি হচ্ছে...", cid, mid)
        dl = f"https://t.me/{BOT_USERNAME}?start={bid}"
        sl = get_short_link(dl)
        update_user(cid, {"step":"wait_thumbnail","pending_link":dl,"pending_short_link":sl,"batch_id":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
        m = InlineKeyboardMarkup()
        m.row(
            InlineKeyboardButton("Auto Generate 🤖", callback_data="autogen_thumb"),
            InlineKeyboardButton("Skip ⏩", callback_data="skip_thumb")
        )
        bot.edit_message_text(
            f"✅ <b>{cnt}টি ফাইল সেভ হয়েছে!</b>\n\n💎 Direct Link:\n<code>{dl}</code>\n\n📺 Short Link:\n<code>{sl}</code>\n\n🖼️ থাম্বনেইল (ছবি/ভিডিও) পাঠান অথবা নিচের বাটন ব্যবহার করুন।",
            cid, mid, disable_web_page_preview=True,
            reply_markup=m
        )

    elif data.startswith("postcat_"):
        user = get_user(cid)
        mtype = user.get("temp_media_type","")
        mmid  = user.get("temp_media_id","")
        cat_target = data[8:]  

        if cat_target == "all":
            update_user(cid, {"pending_category":"","step":"none"})
            user2 = get_user(cid)
            if not user2.get("pending_web_post_link"):
                user2["pending_web_post_link"] = create_web_video_entry(user2, "Others")
                user2 = get_user(cid)
            _do_post_all_channels(cid, user2, mtype, mmid, user2.get("pending_link",""), user2.get("pending_short_link",""))
        else:
            cat = get_category(cat_target)
            if cat:
                update_user(cid, {"pending_category": cat_target})
                user2 = get_user(cid)
                if not user2.get("pending_web_post_link"):
                    user2["pending_web_post_link"] = create_web_video_entry(user2, cat.get("name", "Others"))
                    user2 = get_user(cid)
                count = _post_to_category(cat_target, mtype, mmid, user2, user2.get("pending_link",""), user2.get("pending_short_link",""))
                protect = bool(get_setting("protect_content", 0))
                _inc_stat("uploads")
                try:
                    bot.edit_message_text(f"✅ <b>পোস্ট সম্পন্ন!</b>\n📂 ক্যাটাগরি: <b>{cat['name']}</b>\n📤 <b>{count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n🔒 Protect: {_ico(protect)}", cid, mid)
                except:
                    bot.send_message(cid, f"✅ <b>পোস্ট সম্পন্ন!</b>\n📂 ক্যাটাগরি: <b>{cat['name']}</b>\n📤 <b>{count}</b>টি চ্যানেলে পোস্ট হয়েছে।")
                update_user(cid,{"step":"none","pending_link":"","pending_short_link":"","pending_category":"","temp_media_id":"","temp_media_type":"","post_header":"","post_footer":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
            else:
                bot.answer_callback_query(call.id,"⚠️ ক্যাটাগরি পাওয়া যায়নি!", show_alert=True)

    elif data == "ask_schedule":
        cats = get_categories()
        m = _mk()
        if cats:
            for c in cats:
                m.add(_btn(f"📂 {c['name']}", f"schedcat_{c['cat_id']}"))
            m.add(_btn("🌐 সব চ্যানেলে সিডিউল করো", "schedcat_all"))
        else:
            m.add(_btn("⏰ সব চ্যানেলে সিডিউল করো", "schedcat_all"))
        m.add(_back("back_to_post_options"))
        bot.edit_message_text(
            "⏰ <b>সিডিউল পোস্টের জন্য ক্যাটাগরি সিলেক্ট করুন:</b>\n\nকোন ক্যাটাগরিতে সিডিউল পোস্টটি সেভ করতে চান?",
            cid, mid, reply_markup=m
        )

    elif data.startswith("schedcat_"):
        try: bot.delete_message(cid, mid)
        except: pass
        cat_target = data[9:]
        if cat_target == "all":
            cat_target = ""
        
        # Set a placeholder far-future scheduled time (e.g. 10 years from now)
        future_dt = datetime.utcnow() + timedelta(days=3650)
        future_iso = future_dt.isoformat()
        
        update_user(cid, {"pending_category": cat_target})
        user2 = get_user(cid)
        mtype_s = user2.get("temp_media_type","")
        mmid_s  = user2.get("temp_media_id","")
        
        # Video is NOT created on Firebase now; it will be created dynamically when the scheduled post fires!
        execute_channel_post(cid, user2, mtype_s, mmid_s, scheduled_at=future_iso)
        return

    elif data == "menu_schedule":
        all_pending = list(scheduled_col.find({"status": "pending"}))
        unscheduled = [s for s in all_pending if str(s.get("scheduled_at","")).startswith("203") or s.get("time_set") is False]
        timed = [s for s in all_pending if not str(s.get("scheduled_at","")).startswith("203") and s.get("time_set") is not False]
        timed.sort(key=lambda x: x.get("scheduled_at",""))

        txt = (
            f"⏰ <b>সিডিউল পোস্ট ম্যানেজমেন্ট</b>\n"
            f"{'─'*26}\n"
            f"⏳ <b>টাইম সেট বাকি (ড্রাফট কিউ):</b> <b>{len(unscheduled)}</b>টি\n"
            f"⏰ <b>টাইম সেট করা (রেডি সিডিউল):</b> <b>{len(timed)}</b>টি\n"
            f"{'─'*26}\n"
            f"💡 <i>বট থেকে সিডিউল করা নতুন পোস্টগুলো 'টাইম সেট বাকি' কিউতে থাকে। সহজে সময় সেট করতে নিচের বাটনে ক্লিক করুন।</i>"
        )
        m = _mk()
        m.add(_btn(f"⏳ টাইম সেট বাকি ড্রাফট ({len(unscheduled)}টি)", "schedlist_unscheduled"))
        m.add(_btn(f"⏰ টাইম সেট করা পোস্ট ({len(timed)}টি)", "schedlist_timed"))
        m.add(_back("main_menu"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data == "schedlist_unscheduled":
        all_pending = list(scheduled_col.find({"status": "pending"}))
        unscheduled = [s for s in all_pending if str(s.get("scheduled_at","")).startswith("203") or s.get("time_set") is False]
        unscheduled.sort(key=lambda x: x.get("created_at",""), reverse=True)
        m = _mk()
        for s in unscheduled[:15]:
            cat = get_category(s.get("category_id","")) if s.get("category_id") else None
            cat_txt = cat['name'] if cat else "সব চ্যানেল"
            m.row(_btn(f"⏳ {s.get('web_title') or cat_txt} ({s['sched_id'][:6]})", f"scheddetail_{s['sched_id']}"), _btn("🗑️", f"del_sched_{s['sched_id']}"))
        m.add(_back("menu_schedule"))
        bot.edit_message_text(f"⏳ <b>টাইম সেট বাকি ড্রাফট পোস্ট ({len(unscheduled)}টি)</b>\n{'─'*26}\nটাইম সেট করতে বা এখনই পোস্ট করতে যেকোনো পোস্টে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data == "schedlist_timed":
        all_pending = list(scheduled_col.find({"status": "pending"}))
        timed = [s for s in all_pending if not str(s.get("scheduled_at","")).startswith("203") and s.get("time_set") is not False]
        timed.sort(key=lambda x: x.get("scheduled_at",""))
        m = _mk()
        for s in timed[:15]:
            cat = get_category(s.get("category_id","")) if s.get("category_id") else None
            cat_txt = cat['name'] if cat else "সব চ্যানেল"
            s_time = s.get("scheduled_at","")[:16].replace("T"," ")
            m.row(_btn(f"⏰ {s_time} — {cat_txt}", f"scheddetail_{s['sched_id']}"), _btn("🗑️", f"del_sched_{s['sched_id']}"))
        m.add(_back("menu_schedule"))
        bot.edit_message_text(f"⏰ <b>টাইম সেট করা রেডি পোস্ট ({len(timed)}টি)</b>\n{'─'*26}\nডিটেইলস দেখতে বা সময় পরিবর্তন করতে পোস্টে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data.startswith("scheddetail_"):
        sid = data[12:]
        s = scheduled_col.find_one({"sched_id": sid})
        if not s:
            bot.answer_callback_query(call.id, "⚠️ পোস্টটি পাওয়া যায়নি!", show_alert=True); return
        cat = get_category(s.get("category_id","")) if s.get("category_id") else None
        cat_txt = cat['name'] if cat else "🌐 সব চ্যানেল"
        is_timed = not str(s.get("scheduled_at","")).startswith("203") and s.get("time_set") is not False
        time_display = s.get("scheduled_at","")[:16].replace("T"," ") if is_timed else "⏳ টাইম সেট করা নেই (ড্রাফট)"
        
        txt = (
            f"📌 <b>সিডিউল পোস্ট ডিটেইলস</b>\n"
            f"{'─'*26}\n"
            f"🆔 <b>আইডি:</b> <code>{s['sched_id']}</code>\n"
            f"📂 <b>ক্যাটাগরি:</b> <b>{cat_txt}</b>\n"
            f"🎬 <b>টাইটেল:</b> {s.get('web_title') or 'ডিফল্ট'}\n"
            f"📅 <b>সিডিউল সময়:</b> <b>{time_display}</b>\n"
            f"📊 <b>স্ট্যাটাস:</b> {s.get('status')}\n"
        )
        m = _mk()
        m.row(_btn("✏️ সময় সেট / পরিবর্তন", f"sched_settime_{sid}"), _btn("🚀 এখনই পোস্ট করুন", f"sched_postnow_{sid}"))
        m.add(_btn("🗑️ এই পোস্টটি মুছুন", f"del_sched_{sid}"))
        m.add(_back("schedlist_timed" if is_timed else "schedlist_unscheduled"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("sched_settime_"):
        sid = data[14:]
        update_user(cid, {"step": f"wait_sched_newtime_{sid}"})
        bot.send_message(cid, f"⏰ <b>সিডিউল সময় দিন (বাংলাদেশ সময়):</b>\n\nফরম্যাট: <code>YYYY-MM-DD HH:MM</code>\n(যেমন: <code>2026-08-25 20:30</code>)")

    elif data.startswith("sched_postnow_"):
        sid = data[14:]
        item = scheduled_col.find_one({"sched_id": sid, "status": "pending"})
        if not item:
            bot.answer_callback_query(call.id, "⚠️ পোস্ট পাওয়া যায়নি!", show_alert=True); return
        admin_id = item['admin_id']
        user = get_user(admin_id)
        cat_id = item.get("category_id", "")
        mtype  = item['media_type']
        mid_   = item['media_id']
        d_link = item.get('d_link','')
        s_link = item.get('s_link','')
        user["pending_link"] = d_link
        user["pending_short_link"] = s_link
        user["pending_web_title"] = item.get("web_title", "")
        user["pending_thumb_url"] = item.get("thumb_url", "")
        user["pending_web_video_id"] = item.get("web_video_id", "")
        user["pending_web_post_link"] = item.get("web_post_link", "")
        user["pending_web_ads"] = item.get("web_ads", 1)

        count = 0
        if cat_id:
            cat = get_category(cat_id)
            if not user.get("pending_web_post_link"):
                user["pending_web_post_link"] = create_web_video_entry(user, cat.get("name", "Others") if cat else "Others")
            count = _post_to_category(cat_id, mtype, mid_, user, d_link, s_link)
        else:
            if not user.get("pending_web_post_link"):
                user["pending_web_post_link"] = create_web_video_entry(user, "Others")
            _do_post_all_channels(admin_id, user, mtype, mid_, d_link, s_link)
            count = 1

        scheduled_col.update_one({"sched_id": sid}, {"$set": {"status": "done", "posted_at": datetime.now().isoformat()}})
        bot.answer_callback_query(call.id, f"✅ পোস্ট সম্পন্ন! ({count}টি চ্যানেলে)", show_alert=True)
        call.data = "menu_schedule"; cb(call)

    elif data.startswith("del_sched_"):
        sid = data[10:]
        s_ = scheduled_col.find_one({"sched_id": sid})
        if s_:
            s_time = s_.get("scheduled_at","")[:16].replace("T"," ")
            m = _mk()
            m.row(_btn("🗑️ হ্যাঁ, মুছুন", f"confirm_del_sched_{sid}"), _btn("❌ না","menu_schedule"))
            bot.edit_message_text(f"⚠️ এই সিডিউল পোস্ট মুছবেন?\n⏰ সময়: <b>{s_time}</b>", cid, mid, reply_markup=m)

    elif data.startswith("confirm_del_sched_"):
        sid = data[18:]
        scheduled_col.delete_one({"sched_id": sid})
        bot.answer_callback_query(call.id,"✅ সিডিউল মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_schedule"; cb(call)

    elif data == "menu_categories":
        cats = get_categories()
        m = _mk()
        for c in cats:
            chs = c.get("channels", [])
            bn_c = len([x for x in chs if (x.get('lang') or 'en') == 'bn'])
            hi_c = len([x for x in chs if (x.get('lang') or 'en') == 'hi'])
            en_c = len([x for x in chs if (x.get('lang') or 'en') == 'en'])
            m.row(_btn(f"📂 {c['name']} (🇧🇩{bn_c} 🇮🇳{hi_c} 🇬🇧{en_c})", f"view_cat_{c['cat_id']}"), _btn("🗑️", f"del_cat_{c['cat_id']}"))
        m.add(_btn("➕ নতুন ক্যাটাগরি যোগ করুন","add_category"))
        m.add(_back("settings"))
        bot.edit_message_text(f"📂 <b>ক্যাটাগরি ম্যানেজমেন্ট</b>\n{'─'*26}\nমোট: <b>{len(cats)}</b>টি ক্যাটাগরি\n\n⚙️ <i>ক্যাটাগরির ভাষা ভিত্তিক চ্যানেল ও দেখার নিয়ম সেট করতে নামের ওপর ক্লিক করুন।</i>", cid, mid, reply_markup=m)

    elif data == "add_category":
        update_step(cid, "wait_add_category")
        bot.send_message(cid,"📂 <b>নতুন ক্যাটাগরির নাম লিখুন:</b>\n\nউদাহরণ: <code>Love</code>, <code>Sad</code> বা <code>Funny</code>")

    elif data.startswith("del_cat_"):
        cid2 = data[8:]
        cat = get_category(cid2)
        if cat:
            categories_col.delete_one({"_id": cat["_id"]})
            sync_categories_to_firebase()
            bot.answer_callback_query(call.id, f"✅ '{cat['name']}' মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_categories"; cb(call)

    elif data.startswith("view_cat_"):
        cat_id2 = data[9:]
        cat = get_category(cat_id2)
        if not cat:
            bot.answer_callback_query(call.id,"⚠️ পাওয়া যায়নি!", show_alert=True); return
        channels = cat.get("channels",[])
        bn_chs  = [c for c in channels if (c.get("lang") or "en") == "bn"]
        hi_chs  = [c for c in channels if (c.get("lang") or "en") == "hi"]
        en_chs  = [c for c in channels if (c.get("lang") or "en") == "en"]
        
        ad_chs  = [c for c in channels if c.get("type")=="ad"]
        pr_chs  = [c for c in channels if c.get("type")=="premium"]
        log_chs = [c for c in channels if c.get("type")=="log"]
        
        txt = (
            f"📂 <b>ক্যাটাগরি: {cat['name']}</b>\n"
            f"{'─'*26}\n"
            f"🇧🇩 <b>বাংলা চ্যানেল:</b> <b>{len(bn_chs)}</b>টি\n"
            f"🇮🇳 <b>Hindi চ্যানেল:</b> <b>{len(hi_chs)}</b>টি\n"
            f"🇬🇧 <b>English চ্যানেল:</b> <b>{len(en_chs)}</b>টি\n"
            f"{'─'*26}\n"
            f"📺 Ad: <b>{len(ad_chs)}</b> | 💎 Premium: <b>{len(pr_chs)}</b> | 💾 Log: <b>{len(log_chs)}</b>\n\n"
            f"💡 <i>পোস্ট করার সময় বট প্রতিটি চ্যানেলে তার নির্ধারিত ভাষা ও টিউটোরিয়াল বাটন অনুযায়ী পোস্ট করবে।</i>"
        )
        m = _mk()
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", f"catadd_sellang_{cat_id2}"))
        m.row(_btn(f"🇧🇩 বাংলা ({len(bn_chs)}টি)", f"catchlist_{cat_id2}_bn"), _btn(f"🇮🇳 Hindi ({len(hi_chs)}টি)", f"catchlist_{cat_id2}_hi"))
        m.add(_btn(f"🇬🇧 English ({len(en_chs)}টি)", f"catchlist_{cat_id2}_en"))
        m.add(_btn("🎬 ভাষা অনুযায়ী দেখার নিয়ম লিংক", f"cat_langtuts_{cat_id2}"))
        m.add(_btn(f"📺 সব চ্যানেল টাইপ তালিকা ({len(channels)}টি)", f"catlist_ad_{cat_id2}"))
        m.add(_back("menu_categories"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("catadd_sellang_"):
        cat_id2 = data[15:]
        cat = get_category(cat_id2)
        if not cat: return
        m = _mk()
        m.add(_btn("🇧🇩 বাংলা চ্যানেল হিসেবে যোগ করুন", f"cataddch_form_{cat_id2}_bn"))
        m.add(_btn("🇮🇳 Hindi চ্যানেল হিসেবে যোগ করুন", f"cataddch_form_{cat_id2}_hi"))
        m.add(_btn("🇬🇧 English চ্যানেল হিসেবে যোগ করুন", f"cataddch_form_{cat_id2}_en"))
        m.add(_back(f"view_cat_{cat_id2}"))
        bot.edit_message_text(f"🌐 <b>কোন ভাষার চ্যানেল যোগ করবেন?</b>\n\n📂 ক্যাটাগরি: <b>{cat.get('name')}</b>", cid, mid, reply_markup=m)

    elif data.startswith("cataddch_form_"):
        parts = data[14:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, lang = parts
        cat = get_category(cat_id2)
        if not cat: return
        lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
        m = _mk()
        m.add(_btn("📺 Ad চ্যানেল", f"cataddch_type_{cat_id2}_{lang}_ad"))
        m.add(_btn("💎 Premium চ্যানেল", f"cataddch_type_{cat_id2}_{lang}_premium"))
        m.add(_btn("💾 Log চ্যানেল", f"cataddch_type_{cat_id2}_{lang}_log"))
        m.add(_back(f"catadd_sellang_{cat_id2}"))
        bot.edit_message_text(f"📡 <b>চ্যানেল টাইপ নির্বাচন করুন</b>\n\n📂 ক্যাটাগরি: <b>{cat.get('name')}</b>\n🌐 ভাষা: <b>{lang_name}</b>", cid, mid, reply_markup=m)

    elif data.startswith("cataddch_type_"):
        parts = data[14:].split("_", 2)
        if len(parts) < 3: return
        cat_id2, lang, ch_type = parts
        cat = get_category(cat_id2)
        if not cat: return
        lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
        type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        
        # সেভ করা অটো চ্যানেলগুলোর মধ্য থেকে সহজে যোগ করার অপশন
        existing = list(auto_channels_col.find({"type": ch_type, "status": "on"}))
        already_ids = [c.get("channel_id") for c in cat.get("channels",[])]
        available = [ch for ch in existing if ch.get("channel_id") not in already_ids]
        
        m = _mk()
        if available:
            for ch in available:
                m.add(_btn(f"📡 {ch.get('name','?')} ({ch.get('channel_id','')})", f"catpickch_{cat_id2}_{lang}_{ch_type}_{ch.get('ch_id','')}"))
        m.add(_btn("✏️ নতুন চ্যানেল (নাম ও আইডি লিখে)", f"cataddch_man_{cat_id2}_{lang}_{ch_type}"))
        m.add(_back(f"cataddch_form_{cat_id2}_{lang}"))
        bot.edit_message_text(f"📡 <b>{type_names.get(ch_type,'?')} চ্যানেল যোগ — {lang_name}</b>\n\n📂 ক্যাটাগরি: <b>{cat.get('name')}</b>", cid, mid, reply_markup=m)

    elif data.startswith("catpickch_"):
        parts = data[10:].split("_", 3)
        if len(parts) < 4: return
        cat_id2, lang, ch_type, ch_id = parts
        cat = get_category(cat_id2)
        ch = auto_channels_col.find_one({"ch_id": ch_id})
        if not cat or not ch: return
        channels = cat.get("channels", [])
        channels.append({
            "name": ch.get("name", "?"),
            "channel_id": str(ch.get("channel_id", "")),
            "type": ch_type,
            "lang": lang,
            "tutorial_url_1": ch.get("tutorial_url_1", ""),
            "tutorial_url_2": ch.get("tutorial_url_2", ""),
            "status": "on"
        })
        categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
        bot.answer_callback_query(call.id, f"✅ {ch.get('name','?')} যোগ হয়েছে!", show_alert=True)
        call.data = f"catchlist_{cat_id2}_{lang}"; cb(call)

    elif data.startswith("cataddch_man_"):
        parts = data[13:].split("_", 2)
        if len(parts) < 3: return
        cat_id2, lang, ch_type = parts
        update_user(cid, {"step": f"wait_cataddch_{cat_id2}_{lang}_{ch_type}"})
        type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        bot.send_message(cid, f"📝 <b>{type_names.get(ch_type,'?')} চ্যানেল ম্যানুয়ালি যোগ করুন:</b>\n\nফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>\n(যেমন: <code>বাংলা মুভি | -1001234567890</code>)")

    elif data.startswith("catchlist_"):
        parts = data[10:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, lang = parts
        cat = get_category(cat_id2)
        if not cat: return
        channels = cat.get("channels", [])
        lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
        
        m = _mk()
        for idx, ch in enumerate(channels):
            if (ch.get("lang") or "en") != lang: continue
            st_ico = _ico(ch.get("status","on") == "on")
            t_badge = "📺" if ch.get("type")=="ad" else ("💎" if ch.get("type")=="premium" else "💾")
            m.row(_btn(f"{st_ico} {t_badge} {ch.get('name','?')}", f"chdetail_{cat_id2}_{idx}"), _btn("🗑️", f"chdel_{cat_id2}_{idx}"))
        
        m.add(_btn(f"➕ নতুন {lang_name} চ্যানেল যোগ করুন", f"cataddch_form_{cat_id2}_{lang}"))
        m.add(_back(f"view_cat_{cat_id2}"))
        bot.edit_message_text(f"📂 <b>{cat.get('name')} — {lang_name} চ্যানেল তালিকা</b>\n{'─'*26}\nচ্যানেল সেটিংস ও দেখার নিয়ম লিংক এডিট করতে চ্যানেলের নামের ওপর ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data.startswith("chdetail_"):
        parts = data[9:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        idx = int(idx_s)
        cat = get_category(cat_id2)
        if not cat: return
        channels = cat.get("channels", [])
        if idx >= len(channels): return
        ch = channels[idx]
        ch_lang = ch.get("lang") or "en"
        lang_name = LANG_TEXTS.get(ch_lang, {}).get("name", ch_lang)
        type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        
        tut1 = ch.get("tutorial_url_1") or cat.get(f"tutorial_url_1_{ch_lang}") or get_lang_tutorial(ch_lang, 1) or "সেট নেই ❌"
        tut2 = ch.get("tutorial_url_2") or cat.get(f"tutorial_url_2_{ch_lang}") or get_lang_tutorial(ch_lang, 2) or "সেট নেই ❌"
        
        txt = (
            f"📡 <b>চ্যানেল সেটিংস: {ch.get('name','?')}</b>\n"
            f"{'─'*26}\n"
            f"🆔 <b>আইডি:</b> <code>{ch.get('channel_id','')}</code>\n"
            f"🌐 <b>ভাষা:</b> <b>{lang_name}</b>\n"
            f"📺 <b>টাইপ:</b> <b>{type_names.get(ch.get('type','ad'),'?')}</b>\n"
            f"🔛 <b>স্ট্যাটাস:</b> <b>{_ico(ch.get('status','on')=='on')}</b>\n\n"
            f"🎬 <b>দেখার নিয়ম ১ লিংক:</b>\n<code>{tut1}</code>\n"
            f"🎬 <b>দেখার নিয়ম ২ লিংক:</b>\n<code>{tut2}</code>\n"
        )
        m = _mk()
        m.add(_btn(f"🌐 ভাষা পরিবর্তন ({lang_name})", f"ch_changelang_{cat_id2}_{idx}"))
        m.row(_btn("🎬 দেখার নিয়ম ১ সেট", f"ch_settut1_{cat_id2}_{idx}"), _btn("🎬 দেখার নিয়ম ২ সেট", f"ch_settut2_{cat_id2}_{idx}"))
        m.row(_btn(f"📺 টাইপ: {type_names.get(ch.get('type','ad'),'?')}", f"ch_changetype_{cat_id2}_{idx}"), _btn(f"🔛 {_ico(ch.get('status','on')=='on')}", f"ch_togstatus_{cat_id2}_{idx}"))
        m.add(_btn("🗑️ এই চ্যানেল মুছুন", f"chdel_{cat_id2}_{idx}"))
        m.add(_back(f"catchlist_{cat_id2}_{ch_lang}"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("ch_changelang_"):
        parts = data[14:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        m = _mk()
        m.add(_btn("🇧🇩 বাংলা (Bengali)", f"ch_setlang_{cat_id2}_{idx_s}_bn"))
        m.add(_btn("🇮🇳 हिंदी (Hindi)", f"ch_setlang_{cat_id2}_{idx_s}_hi"))
        m.add(_btn("🇬🇧 English", f"ch_setlang_{cat_id2}_{idx_s}_en"))
        m.add(_back(f"chdetail_{cat_id2}_{idx_s}"))
        bot.edit_message_text("🌐 <b>চ্যানেলের ভাষা সিলেক্ট করুন:</b>", cid, mid, reply_markup=m)

    elif data.startswith("ch_setlang_"):
        parts = data[11:].split("_", 2)
        if len(parts) < 3: return
        cat_id2, idx_s, new_lang = parts
        cat = get_category(cat_id2)
        if cat:
            channels = cat.get("channels", [])
            idx = int(idx_s)
            if idx < len(channels):
                channels[idx]["lang"] = new_lang
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                bot.answer_callback_query(call.id, f"✅ ভাষা সেট হয়েছে: {LANG_TEXTS.get(new_lang,{}).get('name',new_lang)}", show_alert=True)
                call.data = f"chdetail_{cat_id2}_{idx_s}"; cb(call)

    elif data.startswith("ch_changetype_"):
        parts = data[14:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        m = _mk()
        m.add(_btn("📺 Ad চ্যানেল", f"ch_settype_{cat_id2}_{idx_s}_ad"))
        m.add(_btn("💎 Premium চ্যানেল", f"ch_settype_{cat_id2}_{idx_s}_premium"))
        m.add(_btn("💾 Log চ্যানেল", f"ch_settype_{cat_id2}_{idx_s}_log"))
        m.add(_back(f"chdetail_{cat_id2}_{idx_s}"))
        bot.edit_message_text("📺 <b>চ্যানেল টাইপ নির্বাচন করুন:</b>", cid, mid, reply_markup=m)

    elif data.startswith("ch_settype_"):
        parts = data[11:].split("_", 2)
        if len(parts) < 3: return
        cat_id2, idx_s, new_type = parts
        cat = get_category(cat_id2)
        if cat:
            channels = cat.get("channels", [])
            idx = int(idx_s)
            if idx < len(channels):
                channels[idx]["type"] = new_type
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                bot.answer_callback_query(call.id, "✅ চ্যানেল টাইপ পরিবর্তন হয়েছে!", show_alert=True)
                call.data = f"chdetail_{cat_id2}_{idx_s}"; cb(call)

    elif data.startswith("ch_togstatus_"):
        parts = data[13:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        cat = get_category(cat_id2)
        if cat:
            channels = cat.get("channels", [])
            idx = int(idx_s)
            if idx < len(channels):
                channels[idx]["status"] = "off" if channels[idx].get("status","on") == "on" else "on"
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                call.data = f"chdetail_{cat_id2}_{idx_s}"; cb(call)

    elif data.startswith("chdel_"):
        parts = data[6:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        cat = get_category(cat_id2)
        if cat:
            channels = cat.get("channels", [])
            idx = int(idx_s)
            if idx < len(channels):
                channels.pop(idx)
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                bot.answer_callback_query(call.id, "✅ চ্যানেল মুছে ফেলা হয়েছে!", show_alert=True)
                call.data = f"view_cat_{cat_id2}"; cb(call)

    elif data.startswith("ch_settut1_"):
        parts = data[11:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        update_step(cid, f"wait_chtut_1_{cat_id2}_{idx_s}")
        bot.send_message(cid, "🎬 <b>এই চ্যানেলের জন্য দেখার নিয়ম ১ (Tutorial 1) লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/123</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data.startswith("ch_settut2_"):
        parts = data[11:].split("_", 1)
        if len(parts) < 2: return
        cat_id2, idx_s = parts
        update_step(cid, f"wait_chtut_2_{cat_id2}_{idx_s}")
        bot.send_message(cid, "🎬 <b>এই চ্যানেলের জন্য দেখার নিয়ম ২ (Tutorial 2) লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/456</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data.startswith("cat_langtuts_"):
        cat_id2 = data[13:]
        cat = get_category(cat_id2)
        if not cat: return
        
        bn1 = cat.get("tutorial_url_1_bn") or get_lang_tutorial("bn", 1) or "সেট নেই ❌"
        bn2 = cat.get("tutorial_url_2_bn") or get_lang_tutorial("bn", 2) or "সেট নেই ❌"
        hi1 = cat.get("tutorial_url_1_hi") or get_lang_tutorial("hi", 1) or "সেট নেই ❌"
        hi2 = cat.get("tutorial_url_2_hi") or get_lang_tutorial("hi", 2) or "সেট নেই ❌"
        en1 = cat.get("tutorial_url_1_en") or get_lang_tutorial("en", 1) or "সেট নেই ❌"
        en2 = cat.get("tutorial_url_2_en") or get_lang_tutorial("en", 2) or "সেট নেই ❌"
        
        txt = (
            f"🎬 <b>ভাষা অনুযায়ী দেখার নিয়ম লিংক — {cat.get('name')}</b>\n"
            f"{'─'*26}\n"
            f"🇧🇩 <b>বাংলা ১:</b> <code>{bn1}</code>\n"
            f"🇧🇩 <b>বাংলা ২:</b> <code>{bn2}</code>\n\n"
            f"🇮🇳 <b>Hindi ১:</b> <code>{hi1}</code>\n"
            f"🇮🇳 <b>Hindi ২:</b> <code>{hi2}</code>\n\n"
            f"🇬🇧 <b>English ১:</b> <code>{en1}</code>\n"
            f"🇬🇧 <b>English ২:</b> <code>{en2}</code>\n"
            f"{'─'*26}\n"
            f"💡 <i>যেকোনো ভাষার দেখার নিয়ম লিংক সেট করতে নিচের বাটনে চাপুন।</i>"
        )
        m = _mk()
        m.row(_btn("🇧🇩 বাংলা ১ লিংক", f"set_cattut_{cat_id2}_bn_1"), _btn("🇧🇩 বাংলা ২ লিংক", f"set_cattut_{cat_id2}_bn_2"))
        m.row(_btn("🇮🇳 Hindi ১ লিংক", f"set_cattut_{cat_id2}_hi_1"), _btn("🇮🇳 Hindi ২ লিংক", f"set_cattut_{cat_id2}_hi_2"))
        m.row(_btn("🇬🇧 English ১ লিংক", f"set_cattut_{cat_id2}_en_1"), _btn("🇬🇧 English ২ লিংক", f"set_cattut_{cat_id2}_en_2"))
        m.add(_back(f"view_cat_{cat_id2}"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("set_cattut_"):
        parts = data[11:].split("_", 2)
        if len(parts) < 3: return
        cat_id2, lang, num = parts
        cat = get_category(cat_id2)
        if not cat: return
        update_step(cid, f"wait_cattutlang_{cat_id2}_{lang}_{num}")
        lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
        bot.send_message(cid, f"🎬 <b>'{cat.get('name')}' ক্যাটাগরির {lang_name} দেখার নিয়ম {num} লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/123</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data == "menu_global_langtuts":
        bn1 = get_lang_tutorial("bn", 1) or "সেট নেই ❌"
        bn2 = get_lang_tutorial("bn", 2) or "সেট নেই ❌"
        hi1 = get_lang_tutorial("hi", 1) or "সেট নেই ❌"
        hi2 = get_lang_tutorial("hi", 2) or "সেট নেই ❌"
        en1 = get_lang_tutorial("en", 1) or "সেট নেই ❌"
        en2 = get_lang_tutorial("en", 2) or "সেট নেই ❌"
        
        txt = (
            f"🌐 <b>গ্লোবাল ভাষা ভিত্তিক দেখার নিয়ম লিংক</b>\n"
            f"{'─'*26}\n"
            f"🇧🇩 <b>বাংলা ১:</b> <code>{bn1}</code>\n"
            f"🇧🇩 <b>বাংলা ২:</b> <code>{bn2}</code>\n\n"
            f"🇮🇳 <b>Hindi ১:</b> <code>{hi1}</code>\n"
            f"🇮🇳 <b>Hindi ২:</b> <code>{hi2}</code>\n\n"
            f"🇬🇧 <b>English ১:</b> <code>{en1}</code>\n"
            f"🇬🇧 <b>English ২:</b> <code>{en2}</code>\n"
            f"{'─'*26}\n"
            f"💡 <i>এখানে সেট করা লিংক সমস্ত ক্যাটাগরির বাংলা/হিন্দি/ইংরেজি চ্যানেলে ডিফল্ট হিসেবে কাজ করবে।</i>"
        )
        m = _mk()
        m.row(_btn("🇧🇩 বাংলা ১ লিংক", "set_globtut_bn_1"), _btn("🇧🇩 বাংলা ২ লিংক", "set_globtut_bn_2"))
        m.row(_btn("🇮🇳 Hindi ১ লিংক", "set_globtut_hi_1"), _btn("🇮🇳 Hindi ২ লিংক", "set_globtut_hi_2"))
        m.row(_btn("🇬🇧 English ১ লিংক", "set_globtut_en_1"), _btn("🇬🇧 English ২ লিংক", "set_globtut_en_2"))
        m.add(_back("menu_post_settings"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("set_globtut_"):
        parts = data[12:].split("_", 1)
        if len(parts) < 2: return
        lang, num = parts
        update_step(cid, f"wait_globtut_{lang}_{num}")
        lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
        bot.send_message(cid, f"🎬 <b>{lang_name} এর জন্য গ্লোবাল দেখার নিয়ম {num} লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/123</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data.startswith("catlist_"):
        parts = data.split("_", 2)
        if len(parts) < 3: return
        ch_type2 = parts[1]; cat_id3 = parts[2]
        cat = get_category(cat_id3)
        if not cat:
            bot.answer_callback_query(call.id,"⚠️ পাওয়া যায়নি!", show_alert=True); return
        channels = cat.get("channels",[])
        type_chs = [c for c in channels if c.get("type")==ch_type2]
        type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        m = _mk()
        for idx, ch in enumerate(channels):
            if ch.get("type") != ch_type2: continue
            l_badge = LANG_TEXTS.get(ch.get("lang") or "en", {}).get("badge", "🇬🇧 EN")
            m.row(_btn(f"{_ico(ch.get('status','on')=='on')} [{l_badge}] {ch.get('name','?')}", f"chdetail_{cat_id3}_{idx}"), _btn("🗑️", f"chdel_{cat_id3}_{idx}"))
        m.add(_btn(f"➕ {type_names.get(ch_type2,'?')} চ্যানেল যোগ করুন", f"catadd_sellang_{cat_id3}"))
        m.add(_back(f"view_cat_{cat_id3}"))
        bot.edit_message_text(f"<b>{type_names.get(ch_type2,'?')} চ্যানেল — {cat['name']}</b>\nমোট: {len(type_chs)}টি", cid, mid, reply_markup=m)

    elif data == "settings":
        update_step(cid, "none")
        m = _mk()
        m.row(_btn("📝 পোস্ট সেটিংস",  "menu_post_settings"), _btn("📁 ফাইল সেটিংস",   "menu_file_settings"))
        m.row(_btn("📢 আপডেট চ্যানেল", "menu_channels"), _btn("🎥 টিউটোরিয়াল",    "menu_tutorials"))
        m.row(_btn("📤 অটো পোস্ট",     "menu_auto_post"), _btn("🔒 Force Sub",      "menu_force_sub"))
        m.row(_btn("📂 ক্যাটাগরি",      "menu_categories"), _btn("⏰ সিডিউল পোস্ট",   "menu_schedule"))
        m.add(_btn("⚙️ অ্যাডভান্সড সেটিংস", "menu_advanced"))
        m.add(_back("main_menu"))
        bot.edit_message_text("⚙️ <b>বট সেটিংস</b>\n\nযেকোনো সেটিং পরিবর্তন করতে বাটনে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data == "menu_post_settings":
        u  = get_user(cid)
        ph = u.get("post_header","") or "—"
        pf = u.get("post_footer","") or "—"
        st = u.get("saved_title","") or "—"
        lf = _ico(u.get("link_filter",0))
        tf = _ico(u.get("text_filter",0))
        m = _mk()
        m.row(_btn("✏️ Header সেট",  "set_post_header"), _btn("🗑️ Header মুছুন","del_post_header"))
        m.row(_btn("✏️ Footer সেট",  "set_post_footer"), _btn("🗑️ Footer মুছুন","del_post_footer"))
        m.row(_btn("✏️ Save Title সেট", "set_saved_title"), _btn("🗑️ Save Title মুছুন","del_saved_title"))
        m.add(_btn("─────────────────────────", "noop"))
        m.row(_btn(f"🔗 লিংক ফিল্টার {lf}",  "toggle_link_filter"), _btn(f"📝 টেক্সট ফিল্টার {tf}", "toggle_text_filter"))
        m.add(_btn("─────────────────────────", "noop"))
        m.add(_btn("🔘 পোস্ট বাটন অন/অফ ও কনফিগ", "menu_post_buttons"))
        m.add(_btn("🌐 ভাষা ভিত্তিক গ্লোবাল টিউটোরিয়াল", "menu_global_langtuts"))
        m.add(_back("settings"))
        bot.edit_message_text(f"📝 <b>পোস্ট সেটিংস</b>\n{'─'*26}\n📌 <b>Header:</b>\n<i>{ph[:80]}</i>\n\n📌 <b>Footer:</b>\n<i>{pf[:80]}</i>\n\n📌 <b>Save Title:</b>\n<i>{st[:80]}</i>", cid, mid, reply_markup=m)

    elif data == "del_post_header":
        update_user(cid, {"post_header":""}); bot.answer_callback_query(call.id,"✅ Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "del_post_footer":
        update_user(cid, {"post_footer":""}); bot.answer_callback_query(call.id,"✅ Footer মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "del_saved_title":
        update_user(cid, {"saved_title":""}); bot.answer_callback_query(call.id,"✅ Save Title মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "toggle_link_filter":
        new = 1 - user.get("link_filter",0)
        update_user(cid, {"link_filter": new, "text_filter": 0 if new else user.get("text_filter",0)})
        bot.answer_callback_query(call.id, f"🔗 লিংক ফিল্টার: {_ico(new)}", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "toggle_text_filter":
        new = 1 - user.get("text_filter",0)
        update_user(cid, {"text_filter": new, "link_filter": 0 if new else user.get("link_filter",0)})
        bot.answer_callback_query(call.id, f"📝 টেক্সট ফিল্টার: {_ico(new)}", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "noop":
        bot.answer_callback_query(call.id); return

    elif data == "menu_post_buttons":
        u = get_user(cid)
        bot.edit_message_text(
            f"🔘 <b>পোস্ট বাটন কনফিগারেশন</b>\n{'─'*26}\n📥 মাষ্টার ডাউনলোড : {_ico(u.get('btn_download',1))}\n📥 ডাউনলোড ১ বাটন : {_ico(u.get('btn_download_1',1))}\n📥 ডাউনলোড ২ বাটন : {_ico(u.get('btn_download_2',1))}\n🔗 শেয়ার বাটন      : {_ico(u.get('btn_share',1))}\n📽️ টিউটোরিয়াল বাটন : {_ico(u.get('btn_tutorial',1))}\n📝 ক্যাপশনে লিংক   : {_ico(u.get('btn_link_in_caption',1))}\n🔄 লিংক রিপিট      : <b>{u.get('link_repeat_count',1)}x</b>\n🤖 অটো টাইটেল     : {_ico(u.get('auto_title_from_caption',1))}",
            cid, mid, reply_markup=_post_btn_menu(u)
        )

    elif data.startswith("togbtn_"):
        key_map = {
            "togbtn_download": "btn_download", 
            "togbtn_download_1": "btn_download_1",
            "togbtn_download_2": "btn_download_2",
            "togbtn_share": "btn_share", 
            "togbtn_tutorial": "btn_tutorial", "togbtn_link_caption": "btn_link_in_caption",
            "togbtn_auto_title": "auto_title_from_caption"
        }
        if data in key_map:
            k   = key_map[data]
            new = 1 - user.get(k, 1)
            update_user(cid, {k: new})
            bot.answer_callback_query(call.id, f"{_ico(new)} অপশন আপডেট হয়েছে!", show_alert=False)
            call.data = "menu_post_buttons"; cb(call)

    elif data == "menu_custom_dl":
        u = get_user(cid)
        ct1 = u.get("custom_text_1", "None")
        cl1 = u.get("custom_link_1", "None")
        ct2 = u.get("custom_text_2", "None")
        cl2 = u.get("custom_link_2", "None")
        m = _mk()
        m.add(_btn("✏️ টেক্সট ১ সেট করুন", "set_ct1"))
        m.add(_btn("🔗 লিংক ১ সেট করুন", "set_cl1"))
        m.add(_btn("✏️ টেক্সট ২ সেট করুন", "set_ct2"))
        m.add(_btn("🔗 লিংক ২ সেট করুন", "set_cl2"))
        m.add(_back("menu_post_buttons"))
        bot.edit_message_text(
            f"🔘 <b>কাস্টম ডাউনলোড টেক্সট ও লিংক</b>\n{'─'*26}\n<b>বাটন ১:</b>\nটেক্সট: {ct1}\nলিংক: {cl1}\n\n<b>বাটন ২:</b>\nটেক্সট: {ct2}\nলিংক: {cl2}\n",
            cid, mid, reply_markup=m
        )

    elif data == "set_ct1":
        update_step(cid, "wait_set_ct1"); bot.send_message(cid, "১ নম্বর বাটনের জন্য টেক্সট লিখুন:")
    elif data == "set_cl1":
        update_step(cid, "wait_set_cl1"); bot.send_message(cid, "১ নম্বর বাটনের জন্য লিংক দিন:")
    elif data == "set_ct2":
        update_step(cid, "wait_set_ct2"); bot.send_message(cid, "২ নম্বর বাটনের জন্য টেক্সট লিখুন:")
    elif data == "set_cl2":
        update_step(cid, "wait_set_cl2"); bot.send_message(cid, "২ নম্বর বাটনের জন্য লিংক দিন:")

    elif data == "menu_file_settings":
        u  = get_user(cid)
        fh = u.get("header","") or "—"
        ff = u.get("footer","") or "—"
        ad = u.get("auto_delete",0)
        al = f"{ad} মিনিট" if ad>0 else "বন্ধ"
        m = _mk()
        m.row(_btn("✏️ Header সেট",   "set_file_header"), _btn("🗑️ Header মুছুন", "del_file_header"))
        m.row(_btn("✏️ Footer সেট",   "set_file_footer"), _btn("🗑️ Footer মুছুন", "del_file_footer"))
        m.add(_btn(f"⏳ Auto-Delete: {al}", "set_autodelete"))
        m.add(_back("settings"))
        bot.edit_message_text(f"📁 <b>ফাইল সেটিংস</b>\n{'─'*26}\n📌 <b>Header:</b>\n<i>{fh[:80]}</i>\n\n📌 <b>Footer:</b>\n<i>{ff[:80]}</i>\n{'─'*26}\n⏳ Auto-Delete: <b>{al}</b>", cid, mid, reply_markup=m)

    elif data == "del_file_header":
        update_user(cid, {"header":""}); bot.answer_callback_query(call.id,"✅ File Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_file_settings"; cb(call)

    elif data == "del_file_footer":
        update_user(cid, {"footer":""}); bot.answer_callback_query(call.id,"✅ File Footer মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_file_settings"; cb(call)

    elif data == "toggle_protect_content":
        v = toggle_setting("protect_content")
        msg = "🔒 Protect Content চালু!" if v else "🔓 Protect Content বন্ধ!"
        bot.answer_callback_query(call.id, msg, show_alert=True)
        call.data="menu_advanced"; cb(call)

    elif data == "menu_force_sub":
        fcs = list(force_sub_col.find())
        m   = _mk()
        for fc in fcs:
            m.row(_btn(f"{_ico(fc.get('status')=='on')} {fc['name']}", f"tog_fs_{fc['fs_id']}"), _btn("🗑️ মুছুন", f"del_fs_{fc['fs_id']}"))
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", "add_force_sub"))
        m.add(_back("settings"))
        status = f"চালু — {len(fcs)}টি চ্যানেল 🟢" if fcs else "কোনো চ্যানেল নেই 🔴"
        bot.edit_message_text(f"🔒 <b>ফোর্স সাবস্ক্রাইব</b>\n{'─'*26}\nস্ট্যাটাস: {status}", cid, mid, reply_markup=m)

    elif data == "add_force_sub":
        update_step(cid, "wait_add_force_sub")
        bot.send_message(cid, "📢 <b>Force Subscribe চ্যানেল যোগ করুন:</b>\n\nফরম্যাট:\n<code>নাম | চ্যানেল_আইডি | লিংক</code>")

    elif data.startswith("tog_fs_"):
        fc = force_sub_col.find_one({"fs_id": data[7:]})
        if fc:
            force_sub_col.update_one({"fs_id": data[7:]}, {"$set":{"status":"off" if fc.get("status")=="on" else "on"}})
            call.data="menu_force_sub"; cb(call)

    elif data.startswith("del_fs_"):
        force_sub_col.delete_one({"fs_id": data[7:]})
        bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_force_sub"; cb(call)

    elif data == "menu_auto_post":
        m = _mk()
        m.add(_btn(f"📺 Ad Channel   ({auto_channels_col.count_documents({'type':'ad'})}টি)",      "list_ch_ad"))
        m.add(_btn(f"💎 Premium       ({auto_channels_col.count_documents({'type':'premium'})}টি)", "list_ch_premium"))
        m.add(_btn(f"💾 Log Channel   ({auto_channels_col.count_documents({'type':'log'})}টি)",     "list_ch_log"))
        m.add(_back("settings"))
        bot.edit_message_text("📤 <b>অটো পোস্ট চ্যানেল ম্যানেজমেন্ট</b>", cid, mid, reply_markup=m)

    elif data.startswith("list_ch_"):
        ctype = data[8:]
        chs   = list(auto_channels_col.find({"type": ctype}))
        m     = _mk()
        for ch in chs:
            if not ch.get("ch_id"):
                cid2 = str(uuid.uuid4().hex)[:8]
                auto_channels_col.update_one({"_id":ch["_id"]}, {"$set":{"ch_id":cid2,"status":"on"}})
                ch["ch_id"]=cid2; ch["status"]="on"
            lang_code = ch.get("lang", "en")
            badge = LANG_TEXTS.get(lang_code, {}).get("badge", "🇬🇧 EN")
            m.row(
                _btn(f"{_ico(ch.get('status','on')=='on')} {ch.get('name','Unknown')} [{badge}]", f"chset_{ch['ch_id']}"),
                _btn("⚙️", f"chset_{ch['ch_id']}"),
                _btn("🗑️", f"delch_{ch['ch_id']}")
            )
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", f"add_ch_{ctype}"))
        m.add(_back("menu_auto_post"))
        names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        bot.edit_message_text(f"<b>{names.get(ctype)} Channels</b>\n\n⚙️ <i>চ্যানেল সেটিংস ও ভাষা পরিবর্তন করতে নামের ওপর ক্লিক করুন।</i>", cid, mid, reply_markup=m)

    elif data.startswith("chset_"):
        chid = data[6:]
        ch = auto_channels_col.find_one({"ch_id": chid})
        if not ch:
            bot.answer_callback_query(call.id, "⚠️ চ্যানেল পাওয়া যায়নি!", show_alert=True); return
        lang_code = ch.get("lang", "en")
        lang_name = LANG_TEXTS.get(lang_code, {}).get("name", "English 🇬🇧")
        tut1 = ch.get("tutorial_url_1") or "সেট করা নেই ❌"
        tut2 = ch.get("tutorial_url_2") or "সেট করা নেই ❌"
        
        txt = (
            f"⚙️ <b>চ্যানেল সেটিংস: {ch.get('name')}</b>\n"
            f"{'─'*26}\n"
            f"🆔 <b>Channel ID:</b> <code>{ch.get('channel_id')}</code>\n"
            f"🏷️ <b>টাইপ:</b> <b>{ch.get('type')}</b>\n"
            f"🌐 <b>ভাষা (Language):</b> <b>{lang_name}</b>\n"
            f"🎬 <b>দেখার নিয়ম ১ লিংক:</b>\n<code>{tut1}</code>\n"
            f"🎬 <b>দেখার নিয়ম ২ লিংক:</b>\n<code>{tut2}</code>\n"
            f"🔘 <b>স্ট্যাটাস:</b> <b>{_ico(ch.get('status','on')=='on')}</b>"
        )
        m = _mk()
        m.add(_btn(f"🌐 ভাষা পরিবর্তন ({lang_name})", f"chlang_{chid}"))
        m.row(_btn("🎬 দেখার নিয়ম ১ লিংক", f"chtut1_{chid}"), _btn("🎬 দেখার নিয়ম ২ লিংক", f"chtut2_{chid}"))
        m.row(_btn(f"🔘 স্ট্যাটাস: {_ico(ch.get('status','on')=='on')}", f"togch_{chid}"), _btn("🗑️ চ্যানেল মুছুন", f"delch_{chid}"))
        m.add(_back(f"list_ch_{ch.get('type','ad')}"))
        bot.edit_message_text(txt, cid, mid, reply_markup=m)

    elif data.startswith("chlang_"):
        chid = data[7:]
        ch = auto_channels_col.find_one({"ch_id": chid})
        if not ch:
            bot.answer_callback_query(call.id, "⚠️ চ্যানেল পাওয়া যায়নি!", show_alert=True); return
        m = _mk()
        m.add(_btn("🇬🇧 English (Default)", f"setlang_{chid}_en"))
        m.add(_btn("🇧🇩 বাংলা (Bengali)", f"setlang_{chid}_bn"))
        m.add(_btn("🇮🇳 हिंदी (Hindi)", f"setlang_{chid}_hi"))
        m.add(_back(f"chset_{chid}"))
        bot.edit_message_text(f"🌐 <b>চ্যানেলের ভাষা সিলেক্ট করুন</b>\n\n📌 চ্যানেল: <b>{ch.get('name')}</b>\n\nযে ভাষা সিলেক্ট করবেন, ওই চ্যানেলে পোস্ট এবং বাটন সেই ভাষায় তৈরি হবে।", cid, mid, reply_markup=m)

    elif data.startswith("setlang_"):
        parts = data[8:].rsplit("_", 1)
        if len(parts) == 2:
            chid, selected_lang = parts
            if selected_lang in LANG_TEXTS:
                auto_channels_col.update_one({"ch_id": chid}, {"$set": {"lang": selected_lang}})
                ch = auto_channels_col.find_one({"ch_id": chid})
                if ch:
                    for cat in categories_col.find():
                        chs = cat.get("channels", [])
                        changed = False
                        for c in chs:
                            if c.get("channel_id") == ch.get("channel_id"):
                                c["lang"] = selected_lang
                                changed = True
                        if changed:
                            categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
                bot.answer_callback_query(call.id, f"✅ ভাষা সেট হয়েছে: {LANG_TEXTS[selected_lang]['name']}", show_alert=True)
                call.data = f"chset_{chid}"; cb(call)

    elif data.startswith("chtut1_"):
        chid = data[7:]
        ch = auto_channels_col.find_one({"ch_id": chid})
        if not ch: return
        update_step(cid, f"wait_chtut1_{chid}")
        bot.send_message(cid, f"🎬 <b>'{ch.get('name')}' এর জন্য দেখার নিয়ম ১ (Tutorial 1) লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/123</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data.startswith("chtut2_"):
        chid = data[7:]
        ch = auto_channels_col.find_one({"ch_id": chid})
        if not ch: return
        update_step(cid, f"wait_chtut2_{chid}")
        bot.send_message(cid, f"🎬 <b>'{ch.get('name')}' এর জন্য দেখার নিয়ম ২ (Tutorial 2) লিংক দিন:</b>\n\nভিডিওর URL লিখে পাঠান (যেমন: <code>https://t.me/tutorial/456</code>)\nমুছে ফেলতে চাইলে <code>/none</code> লিখুন।")

    elif data.startswith("togch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            new_st = "off" if ch.get("status","on")=="on" else "on"
            auto_channels_col.update_one({"ch_id":ch['ch_id']},{"$set":{"status":new_st}})
            for cat in categories_col.find():
                chs = cat.get("channels", [])
                changed = False
                for c in chs:
                    if c.get("channel_id") == ch.get("channel_id"):
                        c["status"] = new_st
                        changed = True
                if changed:
                    categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
            call.data=f"chset_{ch['ch_id']}"; cb(call)

    elif data.startswith("delch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            auto_channels_col.delete_one({"ch_id":ch['ch_id']})
            for cat in categories_col.find():
                chs = [c for c in cat.get("channels", []) if c.get("channel_id") != ch.get("channel_id")]
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
            bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)
            call.data=f"list_ch_{ch['type']}"; cb(call)

    elif data.startswith("add_ch_"):
        update_step(cid, f"wait_add_{data[7:]}")
        bot.send_message(cid, "📝 ফরম্যাট:\n<code>নাম | চ্যানেল_আইডি</code>\n\n(ডিফল্ট ভাষা English সেট থাকবে, পরে সেটিংস থেকে পরিবর্তন করতে পারবেন)")

    elif data == "menu_channels":
        m = _mk()
        m.row(_btn("➕ নতুন চ্যানেল","add_channel"), _btn("🗑️ সব মুছুন","clear_channels"))
        m.add(_back("settings"))
        bot.edit_message_text("📢 <b>আপডেট চ্যানেল ম্যানেজমেন্ট</b>", cid, mid, reply_markup=m)

    elif data == "menu_tutorials":
        m = _mk()
        m.row(_btn("➕ নতুন ভিডিও","add_tutorial"), _btn("🗑️ সব মুছুন","clear_tutorials"))
        m.add(_back("settings"))
        bot.edit_message_text("🎥 <b>টিউটোরিয়াল ভিডিও ম্যানেজমেন্ট</b>", cid, mid, reply_markup=m)

    elif data == "clear_channels":   channels_col.delete_many({}); bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)
    elif data == "clear_tutorials":  tutorials_col.delete_many({}); bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)

    elif data == "menu_custom_buttons":
        btns = user.get("custom_buttons",[])
        m    = _mk()
        for i, b in enumerate(btns):
            m.row(_btn(f"{_ico(b.get('status')=='on')} {b['name']}", f"togbtn_cb_{i}"), _btn("🗑️", f"delbtn_{i}"))
        m.add(_btn("➕ নতুন কাস্টম বাটন যোগ করুন","add_custom_btn"))
        m.add(_back("menu_post_buttons"))
        bot.edit_message_text(f"🔘 <b>কাস্টম বাটন ({len(btns)}টি)</b>", cid, mid, reply_markup=m)

    elif data.startswith("togbtn_cb_"):
        idx = int(data[10:]); btns = user.get("custom_buttons",[])
        if idx < len(btns):
            btns[idx]["status"] = "off" if btns[idx].get("status")=="on" else "on"
            update_user(cid,{"custom_buttons":btns})
            call.data="menu_custom_buttons"; cb(call)

    elif data.startswith("delbtn_"):
        idx = int(data[7:]); btns = user.get("custom_buttons",[])
        if idx < len(btns):
            btns.pop(idx); update_user(cid,{"custom_buttons":btns})
            bot.answer_callback_query(call.id,"✅ বাটন মুছে ফেলা হয়েছে!")
            call.data="menu_custom_buttons"; cb(call)

    elif data == "add_custom_btn":
        update_step(cid,"wait_custom_btn")
        bot.send_message(cid,"ফরম্যাট: <code>নাম | লিংক</code>")

    elif data == "menu_advanced":
        pc = get_setting("protect_content",0)
        m  = _mk()
        m.add(_btn(f"🔒 Protect Content: {_ico(pc)}","toggle_protect_content"))
        m.add(_btn("─────────────────────────","noop"))
        m.row(_btn("👥 এডমিন ম্যানেজ","manage_admins"), _btn("🚫 ব্যান ম্যানেজ","manage_bans"))
        m.row(_btn("💾 ব্যাকআপ","cmd_backup"), _btn("🔄 রিস্টোর","cmd_restore"))
        m.add(_back("settings"))
        bot.edit_message_text(f"⚙️ <b>অ্যাডভান্সড সেটিংস</b>\n{'─'*26}\n🔒 Protect Content: {_ico(pc)}", cid, mid, reply_markup=m)

    elif data == "manage_admins":
        all_a = list(admins_col.find()); m = _mk()
        for a in all_a:
            if a['chat_id'] != str(MAIN_ADMIN_ID):
                m.add(_btn(f"👤 {a['chat_id']} [{a.get('role','admin')}]", f"rem_adm_{a['chat_id']}"))
        m.add(_btn("➕ নতুন এডমিন যোগ করুন","add_admin"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(f"👥 <b>এডমিন ম্যানেজমেন্ট</b>\nমোট: {len(all_a)} জন", cid, mid, reply_markup=m)

    elif data == "add_admin":
        update_step(cid,"wait_add_admin"); bot.send_message(cid,"➕ নতুন এডমিনের Telegram ID দিন:")

    elif data.startswith("rem_adm_"):
        tid = data[8:]
        if tid == str(MAIN_ADMIN_ID):
            bot.answer_callback_query(call.id,"⛔ সুপার এডমিন সরানো যাবে না!", show_alert=True); return
        admins_col.delete_one({"chat_id":tid}); bot.answer_callback_query(call.id,f"✅ {tid} সরানো হয়েছে!", show_alert=True)
        call.data="manage_admins"; cb(call)

    elif data == "manage_bans":
        bans = list(banned_col.find()); m = _mk()
        for bu in bans[:10]:
            m.add(_btn(f"🚫 {bu['chat_id']}", f"unban_{bu['chat_id']}"))
        m.add(_btn("➕ নতুন ব্যান করুন","add_ban"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(f"🚫 <b>ব্যান ম্যানেজমেন্ট</b>\nমোট: {len(bans)} জন", cid, mid, reply_markup=m)

    elif data == "add_ban":
        update_step(cid,"wait_ban_user"); bot.send_message(cid,"🚫 ব্যান করতে ID দিন (কারণ লিখতে পারেন):\n<code>1234567890 কারণ</code>")

    elif data.startswith("unban_"):
        tid = data[6:]; banned_col.delete_one({"chat_id":tid})
        bot.answer_callback_query(call.id,f"✅ {tid} আনব্যান হয়েছে!", show_alert=True)
        call.data="manage_bans"; cb(call)

    elif data == "cmd_backup":
        bot.answer_callback_query(call.id,"⏳ ব্যাকআপ তৈরি হচ্ছে...")
        bot.send_message(cid,"⏳ ডাটাবেস ব্যাকআপ তৈরি হচ্ছে...")
        bk = {
            "version":BOT_VERSION,"backup_date":datetime.now().isoformat(),
            "users":list(users_col.find({},{"_id":0})), "files":list(files_col.find({},{"_id":0})),
            "tutorials":list(tutorials_col.find({},{"_id":0})), "channels":list(channels_col.find({},{"_id":0})),
            "auto_channels":list(auto_channels_col.find({},{"_id":0})),
            "force_sub":list(force_sub_col.find({},{"_id":0})),
            "settings":list(settings_col.find({},{"_id":0})),
        }
        try:
            with open("backup.json","w",encoding="utf-8") as f: json.dump(bk,f,ensure_ascii=False,indent=2,default=str)
            with open("backup.json","rb") as f:
                bot.send_document(cid,f,caption=f"✅ <b>ব্যাকআপ সম্পন্ন!</b>\n📅 {datetime.now().strftime('%d %b %Y, %H:%M')}\n👥 ইউজার: {len(bk['users'])} | 📁 ফাইল: {len(bk['files'])}\n🔖 v{BOT_VERSION}")
        except Exception as e: bot.send_message(cid,f"❌ ব্যাকআপ ব্যর্থ:\n<code>{e}</code>")
        finally:
            if os.path.exists("backup.json"): os.remove("backup.json")

    elif data == "cmd_restore":
        update_step(cid,"wait_restore"); bot.send_message(cid,"🔄 <code>backup.json</code> ফাইলটি দিন।")

    _step_map = {
        "set_post_header": ("wait_post_header","📝 পোস্টের <b>Header</b> লিখুন:"),
        "set_post_footer": ("wait_post_footer","📝 পোস্টের <b>Footer</b> লিখুন:"),
        "set_saved_title": ("wait_saved_title","📝 সেটিংসের জন্য <b>Save Title</b> লিখুন:"),
        "set_file_header": ("wait_file_header","📁 ফাইলের <b>Header</b> লিখুন:"),
        "set_file_footer": ("wait_file_footer","📁 ফাইলের <b>Footer</b> লিখুন:"),
        "add_channel":     ("wait_add_channel","📢 ফরম্যাট: <code>নাম | লিংক</code>"),
        "add_tutorial":    ("wait_add_tutorial","📽️ ফরম্যাট: <code>নাম | লিংক</code>"),
        "set_autodelete":  ("wait_autodelete","⏳ Auto-Delete সময় লিখুন (মিনিট)। বন্ধ করতে 0।"),
        "set_link_repeat": ("wait_link_repeat","🔄 লিংক কতবার রিপিট হবে? (১–৫)"),
    }
    if data in _step_map:
        sv, prompt = _step_map[data]
        update_step(cid, sv); bot.send_message(cid, prompt, parse_mode="HTML")

    elif data == "help_menu":
        m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(
            f"ℹ️ <b>হেল্প — Bot v{BOT_VERSION}</b>\n{'─'*26}\n📝 <b>পোস্ট সেটিংস:</b> Header/Footer সেট,এডিট,ডিলিট\n🔗 <b>লিংক ফিল্টার:</b> লিংক সরিয়ে টেক্সট রাখে\n📝 <b>টেক্সট ফিল্টার:</b> পুরো ক্যাপশন সরায়\n🔘 <b>পোস্ট বাটন:</b> ডাউনলোড/শেয়ার/টিউটোরিয়াল ON/OFF\n📂 <b>ক্যাটাগরি:</b> হিন্দি/বাংলা ইত্যাদি ক্যাটাগরিতে আলাদা Ad/Premium/Log চ্যানেল\n⏰ <b>সিডিউল পোস্ট:</b> ভবিষ্যতে নির্দিষ্ট সময়ে পোস্ট করুন\n🔒 <b>Protect Content:</b> ফরোয়ার্ড/সেভ বন্ধ\n🔒 <b>Force Subscribe:</b> চ্যানেল Join বাধ্যতামূলক\n📦 <b>Batch Upload:</b> একাধিক ফাইল একসাথে\n⏳ <b>Auto-Delete:</b> নির্দিষ্ট সময়ে ফাইল মুছে\n💾 <b>Backup/Restore:</b> ডাটাবেস সুরক্ষিত রাখুন\n{'─'*26}\n<b>Commands:</b>\n/stats | /ban ID কারণ | /unban ID\n/reply ID মেসেজ | /cancel",
            cid, mid, reply_markup=m
        )

# ══════════════════════════════════════════════════
#  মেসেজ হ্যান্ডলার
# ══════════════════════════════════════════════════
@bot.message_handler(content_types=['text','photo','document','video','audio'])
def handle_message(message):
    cid   = str(message.chat.id)
    text  = message.text or message.caption or ""
    user  = get_user(cid)
    step  = user.get("step", "none")
    adm   = is_admin(cid)

    if is_banned(cid) and not adm:
        try: bot.send_message(cid,"🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        except: pass
        return

    if text.startswith("/start"):
        pts = text.split(" ")
        if len(pts)>1:
            fk = pts[1]; joined, nj = check_force_sub(cid)
            if not joined: send_force_sub_msg(cid, nj, fk); return
            _deliver_files(cid, fk, user)
        else:
            if adm:
                s = get_stats()
                bot.send_message(cid,
                    f"╔══════════════════════════╗\n║   🤖 <b>এডমিন প্যানেল</b>   ║\n╚══════════════════════════╝\n\n👥 মোট ইউজার : <b>{s['total_users']}</b>\n📁 মোট ফাইল  : <b>{s['total_files']}</b>\n🟢 আজ সক্রিয় : <b>{s['active_today']}</b>",
                    reply_markup=_main_menu()
                )
                bot.send_message(cid, "📌 কুইক অ্যাকশন কিবোর্ড সক্রিয় করা হয়েছে:", reply_markup=_admin_reply_keyboard())
            else:
                m = InlineKeyboardMarkup()
                for tut in tutorials_col.find(): m.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
                for ch  in channels_col.find():  m.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                bot.send_message(cid,"👋 <b>স্বাগতম!</b>", reply_markup=m if m.keyboard else None)
        return

    if text=="/stats" and adm:
        s = get_stats()
        bot.send_message(cid, f"📊 <b>স্ট্যাটিস্টিক্স</b>\n{'─'*24}\n👥 ইউজার: <b>{s['total_users']}</b> | 🟢 সক্রিয়: <b>{s['active_today']}</b>\n📁 ফাইল: <b>{s['total_files']}</b>\n📥 ডাউনলোড: <b>{s['dl_today']}</b> | 📤 আপলোড: <b>{s['ul_today']}</b>")
        return

    if text.startswith("/ban ") and adm:
        pts=text.split(" ",2); tid=pts[1]; rsn=pts[2] if len(pts)>2 else "কারণ উল্লেখ নেই"
        if tid==str(MAIN_ADMIN_ID): bot.send_message(cid,"⛔ সুপার এডমিন ব্যান করা যাবে না!"); return
        if not banned_col.find_one({"chat_id":tid}):
            banned_col.insert_one({"chat_id":tid,"reason":rsn,"banned_at":datetime.now().isoformat()})
            bot.send_message(cid,f"🚫 <code>{tid}</code> ব্যান হয়েছে।\nকারণ: {rsn}")
        else: bot.send_message(cid,f"⚠️ <code>{tid}</code> আগেই ব্যান।")
        return

    if text.startswith("/unban ") and adm:
        tid=text.split()[1]; r=banned_col.delete_one({"chat_id":tid})
        bot.send_message(cid, f"✅ <code>{tid}</code> আনব্যান হয়েছে!" if r.deleted_count else "⚠️ ব্যান লিস্টে নেই।"); return

    if text.startswith("/reply ") and adm:
        pts=text.split(" ",2)
        if len(pts)==3:
            try: bot.send_message(pts[1],f"👨‍💻 <b>এডমিনের উত্তর:</b>\n\n{pts[2]}"); bot.send_message(cid,"✅ পাঠানো হয়েছে!")
            except: bot.send_message(cid,"❌ পাঠানো যায়নি।")
        return

    if text in ["/cancel", "❌ বাতিল", "❌ Cancel"]:
        update_step(cid,"none"); bot.send_message(cid,"❌ বাতিল করা হয়েছে।"); return

    if adm and text in ["📦 ব্যাচ আপলোড", "📦 Batch Upload"]:
        bid = str(uuid.uuid4().hex)[:10]
        update_user(cid, {"batch_id": bid, "step": "wait_batch"})
        m = _mk()
        m.add(_btn("✅ আপলোড শেষ — Finish", "finish_batch"))
        bot.send_message(
            cid,
            f"📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n🆔 Batch ID: <code>{bid}</code>\n\nপরপর সব ফাইল পাঠাতে থাকুন। শেষ হলে নিচের <b>Finish</b> বাটনে চাপুন।",
            reply_markup=m
        )
        return

    if adm and text in ["🤖 এডমিন প্যানেল", "🤖 Admin Panel"]:
        update_step(cid, "none")
        s = get_stats()
        bot.send_message(
            cid,
            f"╔══════════════════════════╗\n║   🤖 <b>এডমিন প্যানেল</b>   ║\n╚══════════════════════════╝\n\n👥 মোট ইউজার : <b>{s['total_users']}</b>\n📁 মোট ফাইল  : <b>{s['total_files']}</b>\n🟢 আজ সক্রিয় : <b>{s['active_today']}</b>",
            reply_markup=_main_menu()
        )
        return

    if not adm:
        try:
            bot.forward_message(MAIN_ADMIN_ID, cid, message.message_id)
            bot.send_message(MAIN_ADMIN_ID,f"📩 নতুন মেসেজ\n👤 <code>{cid}</code>\n💬 <code>/reply {cid} মেসেজ</code>")
            bot.send_message(cid,"✅ এডমিনের কাছে পাঠানো হয়েছে।")
        except: pass
        return

    # ── Custom DL Text/Link Steps (Button Settings) ──
    if step == "wait_set_ct1" and text:
        update_user(cid, {"custom_text_1": text.strip(), "step": "none"})
        bot.send_message(cid, "✅ <b>বাটন ১ টেক্সট সেট হয়েছে!</b>")
        return
    if step == "wait_set_cl1" and text:
        url_val = "" if text.strip() in ["/none", "/clear", "none"] else text.strip()
        update_user(cid, {"custom_link_1": url_val, "step": "none"})
        bot.send_message(cid, f"✅ <b>বাটন ১ লিংক {'সেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>")
        return
    if step == "wait_set_ct2" and text:
        update_user(cid, {"custom_text_2": text.strip(), "step": "none"})
        bot.send_message(cid, "✅ <b>বাটন ২ টেক্সট সেট হয়েছে!</b>")
        return
    if step == "wait_set_cl2" and text:
        url_val = "" if text.strip() in ["/none", "/clear", "none"] else text.strip()
        update_user(cid, {"custom_link_2": url_val, "step": "none"})
        bot.send_message(cid, f"✅ <b>বাটন ২ লিংক {'সেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>")
        return

    # ── Channel Tutorial Steps ──
    if step.startswith("wait_chtut1_"):
        chid = step.replace("wait_chtut1_", "").strip()
        url_val = "" if text.strip() in ["/none", "/clear", "none"] else text.strip()
        auto_channels_col.update_one({"ch_id": chid}, {"$set": {"tutorial_url_1": url_val}})
        ch = auto_channels_col.find_one({"ch_id": chid})
        if ch:
            for cat in categories_col.find():
                chs = cat.get("channels", [])
                changed = False
                for c in chs:
                    if c.get("channel_id") == ch.get("channel_id"):
                        c["tutorial_url_1"] = url_val
                        changed = True
                if changed:
                    categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
        update_step(cid, "none")
        ch_name = ch.get("name") if ch else chid
        bot.send_message(cid, f"✅ <b>'{ch_name}' চ্যানেলের দেখার নিয়ম ১ লিংক {'আপডেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>")
        return

    if step.startswith("wait_chtut2_"):
        chid = step.replace("wait_chtut2_", "").strip()
        url_val = "" if text.strip() in ["/none", "/clear", "none"] else text.strip()
        auto_channels_col.update_one({"ch_id": chid}, {"$set": {"tutorial_url_2": url_val}})
        ch = auto_channels_col.find_one({"ch_id": chid})
        if ch:
            for cat in categories_col.find():
                chs = cat.get("channels", [])
                changed = False
                for c in chs:
                    if c.get("channel_id") == ch.get("channel_id"):
                        c["tutorial_url_2"] = url_val
                        changed = True
                if changed:
                    categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
        update_step(cid, "none")
        ch_name = ch.get("name") if ch else chid
        bot.send_message(cid, f"✅ <b>'{ch_name}' চ্যানেলের দেখার নিয়ম ২ লিংক {'আপডেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>")
        return

    # ── Global Language Tutorial Steps ──
    if step.startswith("wait_globtut_"):
        parts = step[13:].split("_", 1)
        if len(parts) == 2:
            lang, num = parts
            url_val = "" if text.strip().lower() in ["/none", "/clear", "none", "clear", "0"] else text.strip()
            set_lang_tutorial(lang, num, url_val)
            update_step(cid, "none")
            lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
            m = _mk(); m.add(_btn("🔙 গ্লোবাল ভাষা টিউটোরিয়ালে ফিরুন", "menu_global_langtuts"))
            bot.send_message(cid, f"✅ <b>{lang_name} এর জন্য গ্লোবাল দেখার নিয়ম {num} লিংক {'সেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>\n\n🔗 <code>{url_val if url_val else 'মুছে ফেলা হয়েছে'}</code>", reply_markup=m, disable_web_page_preview=True)
            return

    # ── Category Language Tutorial Steps ──
    if step.startswith("wait_cattutlang_"):
        parts = step[16:].split("_", 2)
        if len(parts) == 3:
            cat_id_t, lang, num = parts
            url_val = "" if text.strip().lower() in ["/none", "/clear", "none", "clear", "0"] else text.strip()
            cat = get_category(cat_id_t)
            if cat:
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {f"tutorial_url_{num}_{lang}": url_val}})
            update_step(cid, "none")
            lang_name = LANG_TEXTS.get(lang, {}).get("name", lang)
            m = _mk(); m.add(_btn(f"📂 '{cat.get('name') if cat else ''}' ভাষা টিউটোরিয়ালে ফিরুন", f"cat_langtuts_{cat_id_t}"))
            bot.send_message(cid, f"✅ <b>'{cat.get('name') if cat else ''}' ক্যাটাগরির {lang_name} দেখার নিয়ম {num} লিংক {'সেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>\n\n🔗 <code>{url_val if url_val else 'মুছে ফেলা হয়েছে'}</code>", reply_markup=m, disable_web_page_preview=True)
            return

    # ── Channel Specific Tutorial Steps ──
    if step.startswith("wait_chtut_"):
        parts = step[11:].split("_", 2)
        if len(parts) == 3:
            num_s, cat_id_s, idx_s = parts
            idx = int(idx_s)
            url_val = "" if text.strip().lower() in ["/none", "/clear", "none", "clear", "0"] else text.strip()
            cat = get_category(cat_id_s)
            if cat:
                channels = cat.get("channels", [])
                if idx < len(channels):
                    channels[idx][f"tutorial_url_{num_s}"] = url_val
                    categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                    ch_name = channels[idx].get("name", "?")
                    update_step(cid, "none")
                    m = _mk(); m.add(_btn(f"⚙️ '{ch_name}' চ্যানেল সেটিংসে যান", f"chdetail_{cat_id_s}_{idx_s}"))
                    bot.send_message(cid, f"✅ <b>'{ch_name}' চ্যানেলের দেখার নিয়ম {num_s} লিংক {'সেট হয়েছে' if url_val else 'মুছে ফেলা হয়েছে'}!</b>\n\n🔗 <code>{url_val if url_val else 'মুছে ফেলা হয়েছে'}</code>", reply_markup=m, disable_web_page_preview=True)
                    return

    # ── Category Add Channel by Language Steps ──
    if step.startswith("wait_cataddch_"):
        parts = step[14:].split("_", 2)
        if len(parts) == 3 and "|" in text:
            cat_id_n, lang_n, type_n = parts
            cn, ci = [p.strip() for p in text.split("|", 1)]
            cat = get_category(cat_id_n)
            if cat:
                channels = cat.get("channels", [])
                channels.append({
                    "name": cn,
                    "channel_id": ci,
                    "type": type_n,
                    "lang": lang_n,
                    "status": "on",
                    "tutorial_url_1": "",
                    "tutorial_url_2": ""
                })
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
                update_step(cid, "none")
                lang_name = LANG_TEXTS.get(lang_n, {}).get("name", lang_n)
                type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
                m = _mk()
                m.add(_btn(f"📂 {lang_name} চ্যানেল তালিকায় যান", f"catchlist_{cat_id_n}_{lang_n}"))
                bot.send_message(cid, f"✅ <b>{lang_name} {type_names.get(type_n,'?')} চ্যানেল যোগ হয়েছে:</b> <b>{cn}</b>\n🆔 ID: <code>{ci}</code>", reply_markup=m)
                return
        elif step.startswith("wait_cataddch_"):
            bot.send_message(cid, "⚠️ সঠিক ফরম্যাট দিন: <code>নাম | চ্যানেল_আইডি</code>\n(যেমন: <code>বাংলা মুভি | -1001234567890</code>)")
            return

    # ── Legacy Category Tutorial Steps ──
    if step.startswith("wait_cattut1_"):
        cat_id = step.replace("wait_cattut1_", "").strip()
        url_val = "" if text.strip().lower() in ["/none", "/clear", "none", "clear", "0"] else text.strip()
        categories_col.update_one({"$or": [{"cat_id": cat_id}, {"_id": cat_id}]}, {"$set": {"tutorial_url_1": url_val}})
        try:
            from bson import ObjectId
            categories_col.update_one({"_id": ObjectId(cat_id)}, {"$set": {"tutorial_url_1": url_val}})
        except: pass
        sync_categories_to_firebase()
        cat = get_category(cat_id)
        update_step(cid, "none")
        cat_name = cat.get("name") if cat else cat_id
        target_id = cat.get("cat_id", cat_id) if cat else cat_id
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton(f"📂 '{cat_name}' ক্যাটাগরি সেটিংসে যান", callback_data=f"view_cat_{target_id}"))
        if url_val:
            bot.send_message(cid, f"✅ <b>'{cat_name}' ক্যাটাগরির দেখার নিয়ম ১ লিংক সেভ হয়েছে!</b>\n\n🔗 <b>লিংক:</b> <code>{url_val}</code>\n\n💡 <i>এই ক্যাটাগরিতে পোস্ট করার সময় ডাউনলোড ১ এর পাশে এই টিউটোরিয়াল বাটনটি কাজ করবে।</i>", reply_markup=m, disable_web_page_preview=True)
        else:
            bot.send_message(cid, f"🗑️ <b>'{cat_name}' ক্যাটাগরির দেখার নিয়ম ১ লিংক মুছে ফেলা হয়েছে!</b>", reply_markup=m)
        return

    if step.startswith("wait_cattut2_"):
        cat_id = step.replace("wait_cattut2_", "").strip()
        url_val = "" if text.strip().lower() in ["/none", "/clear", "none", "clear", "0"] else text.strip()
        categories_col.update_one({"$or": [{"cat_id": cat_id}, {"_id": cat_id}]}, {"$set": {"tutorial_url_2": url_val}})
        try:
            from bson import ObjectId
            categories_col.update_one({"_id": ObjectId(cat_id)}, {"$set": {"tutorial_url_2": url_val}})
        except: pass
        sync_categories_to_firebase()
        cat = get_category(cat_id)
        update_step(cid, "none")
        cat_name = cat.get("name") if cat else cat_id
        target_id = cat.get("cat_id", cat_id) if cat else cat_id
        m = InlineKeyboardMarkup()
        m.add(InlineKeyboardButton(f"📂 '{cat_name}' ক্যাটাগরি সেটিংসে যান", callback_data=f"view_cat_{target_id}"))
        if url_val:
            bot.send_message(cid, f"✅ <b>'{cat_name}' ক্যাটাগরির দেখার নিয়ম ২ লিংক সেভ হয়েছে!</b>\n\n🔗 <b>লিংক:</b> <code>{url_val}</code>\n\n💡 <i>এই ক্যাটাগরিতে পোস্ট করার সময় ডাউনলোড ২ এর পাশে এই টিউটোরিয়াল বাটনটি কাজ করবে।</i>", reply_markup=m, disable_web_page_preview=True)
        else:
            bot.send_message(cid, f"🗑️ <b>'{cat_name}' ক্যাটাগরির দেখার নিয়ম ২ লিংক মুছে ফেলা হয়েছে!</b>", reply_markup=m)
        return
        
    # ── Custom Ads Count Step ──
    if step == "wait_custom_ads":
        if text.isdigit() and int(text) > 0:
            update_user(cid, {"pending_web_ads": int(text), "step": "none"})
            bot.send_message(cid, f"✅ অ্যাড সংখ্যা সেট হয়েছে: {text}টি")
            user = get_user(cid)
            _ask_post_options(cid, user, user.get("temp_media_type"), user.get("temp_media_id"))
        else:
            bot.send_message(cid, "⚠️ সঠিক সংখ্যা লিখুন!")
        return

    if step.startswith("wait_broadcast"):
        tgt = step.replace("wait_broadcast_","") if "_" in step else "all"
        update_step(cid,"none"); bot.send_message(cid,"⏳ ব্রডকাস্ট background-এ শুরু হচ্ছে...")
        threading.Thread(target=_broadcast_worker,daemon=True,args=(cid,cid,message.message_id,tgt)).start(); return

    if step=="wait_add_category":
        name = text.strip()
        if name:
            cat_id = str(uuid.uuid4().hex)[:8]
            categories_col.insert_one({"cat_id":cat_id,"name":name,"channels":[],"created_at":datetime.now().isoformat()})
            sync_categories_to_firebase() # ওয়েবসাইটে সিঙ্ক
            update_step(cid,"none")
            bot.send_message(cid, f"✅ <b>ক্যাটাগরি তৈরি হয়েছে!</b>\n📂 নাম: <b>{name}</b>")
        else: bot.send_message(cid,"⚠️ নাম খালি রাখা যাবে না।")
        return

    if step.startswith("wait_catadd_") and "|" in text:
        rest = step[12:]  
        parts = rest.split("_",1)
        if len(parts)==2:
            ch_type_new = parts[0]; cat_id_new = parts[1]
            cn,ci = [p.strip() for p in text.split("|",1)]
            cat = get_category(cat_id_new)
            if cat:
                channels = cat.get("channels",[])
                channels.append({"name":cn,"channel_id":ci,"type":ch_type_new,"status":"on"})
                categories_col.update_one({"cat_id":cat_id_new},{"$set":{"channels":channels}})
                update_step(cid,"none")
                type_names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
                bot.send_message(cid,f"✅ {type_names.get(ch_type_new,'?')} চ্যানেল যোগ হয়েছে: <b>{cn}</b>")
            else: bot.send_message(cid,"⚠️ ক্যাটাগরি পাওয়া যায়নি।")
        return
    elif step.startswith("wait_catadd_"):
        bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>")
        return

    if step=="wait_schedule_time":
        try:
            dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
            dt_utc = dt - timedelta(hours=6)
            if dt_utc <= datetime.utcnow():
                bot.send_message(cid,"⚠️ সময়টি ভবিষ্যতে হওয়া উচিত!"); return
            update_user(cid, {"pending_schedule": dt_utc.isoformat(), "step":"none"})
            user2 = get_user(cid)
            mtype_s = user2.get("temp_media_type","")
            mmid_s  = user2.get("temp_media_id","")
            execute_channel_post(cid, user2, mtype_s, mmid_s, scheduled_at=dt_utc.isoformat())
        except ValueError:
            bot.send_message(cid,"⚠️ ফরম্যাট ভুল! সঠিক ফরম্যাট:\n<code>YYYY-MM-DD HH:MM</code>")
        return

    if step.startswith("wait_sched_newtime_"):
        sid = step.replace("wait_sched_newtime_", "").strip()
        try:
            dt = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
            dt_utc = dt - timedelta(hours=6)
            if dt_utc <= datetime.utcnow():
                bot.send_message(cid,"⚠️ সময়টি ভবিষ্যতে হওয়া উচিত!"); return
            scheduled_col.update_one({"sched_id": sid}, {"$set": {"scheduled_at": dt_utc.isoformat(), "status": "pending", "time_set": True}})
            update_step(cid, "none")
            m = _mk(); m.add(_btn("⏰ সিডিউল মেনুতে ফিরুন", "menu_schedule"))
            bot.send_message(cid, f"✅ <b>সিডিউল সময় সফলভাবে সেট হয়েছে!</b>\n📅 সময়: <b>{text.strip()}</b>", reply_markup=m)
        except ValueError:
            bot.send_message(cid,"⚠️ ভুল ফরম্যাট! সঠিক ফরম্যাট:\n<code>YYYY-MM-DD HH:MM</code>")
        return

    if step=="wait_add_force_sub":
        if "|" in text:
            pts=[p.strip() for p in text.split("|")]
            if len(pts)>=3:
                force_sub_col.insert_one({"fs_id":str(uuid.uuid4().hex)[:8],"name":pts[0],"channel_id":pts[1],"url":pts[2],"status":"on"})
                update_step(cid,"none"); bot.send_message(cid,f"✅ Force Subscribe চ্যানেল যোগ: <b>{pts[0]}</b>")
            else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        return

    if step in ["wait_add_ad","wait_add_premium","wait_add_log"]:
        if "|" in text:
            cn,ci=[p.strip() for p in text.split("|",1)]; ct=step.split("_")[2]
            auto_channels_col.insert_one({"ch_id":str(uuid.uuid4().hex)[:8],"type":ct,"name":cn,"channel_id":ci,"status":"on"})
            update_step(cid,"none"); bot.send_message(cid,f"✅ চ্যানেল যোগ: <b>{cn}</b>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>")
        return

    if step=="wait_custom_btn":
        if "|" in text:
            bn,bl=[p.strip() for p in text.split("|",1)]
            btns=user.get("custom_buttons",[]); btns.append({"name":bn,"url":bl,"status":"on"})
            update_user(cid,{"custom_buttons":btns,"step":"none"}); bot.send_message(cid,f"✅ বাটন যোগ: <b>{bn}</b>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | লিংক</code>")
        return

    if step=="wait_ban_user":
        pts=text.strip().split(" ",1); tid=pts[0]; rsn=pts[1] if len(pts)>1 else "এডমিন কর্তৃক ব্যান"
        if tid==str(MAIN_ADMIN_ID): bot.send_message(cid,"⛔ সুপার এডমিন ব্যান করা যাবে না!")
        elif not banned_col.find_one({"chat_id":tid}):
            banned_col.insert_one({"chat_id":tid,"reason":rsn,"banned_at":datetime.now().isoformat()})
            bot.send_message(cid,f"🚫 <code>{tid}</code> ব্যান হয়েছে!")
        else: bot.send_message(cid,"⚠️ আগেই ব্যান।")
        update_step(cid,"none"); return

    if step=="wait_add_admin":
        tid=text.strip()
        if not admins_col.find_one({"chat_id":tid}):
            admins_col.insert_one({"chat_id":tid,"role":"admin","added_at":datetime.now().isoformat()})
            bot.send_message(cid,f"✅ <code>{tid}</code> এডমিন হয়েছে!")
            try: bot.send_message(tid,"🎉 আপনাকে এডমিন করা হয়েছে!")
            except: pass
        else: bot.send_message(cid,"⚠️ এই আইডি আগেই এডমিন।")
        update_step(cid,"none"); return

    ts = {"wait_post_header":("post_header","📝 Post Header"),"wait_post_footer":("post_footer","📝 Post Footer"),
          "wait_file_header":("header","📁 File Header"),"wait_file_footer":("footer","📁 File Footer"),
          "wait_saved_title":("saved_title","📝 Save Title")}
    if step in ts and message.text:
        k,lbl=ts[step]; update_user(cid,{k:text,"step":"none"}); bot.send_message(cid,f"✅ <b>{lbl}</b> সেট হয়েছে!"); return

    if step=="wait_autodelete" and text.isdigit():
        v=int(text); update_user(cid,{"auto_delete":v,"step":"none"})
        bot.send_message(cid,f"✅ Auto-Delete <b>{v} মিনিট</b>!" if v>0 else "✅ Auto-Delete <b>বন্ধ</b>!"); return

    if step=="wait_link_repeat" and text.isdigit():
        v=max(1,min(int(text),5)); update_user(cid,{"link_repeat_count":v,"step":"none"})
        bot.send_message(cid,f"✅ লিংক রিপিট <b>{v}x</b>!"); return

    if step=="wait_add_channel":
        if "|" in text:
            n,l=[p.strip() for p in text.split("|",1)]
        else:
            n = "📢 চ্যানেল"
            l = text.strip()
        channels_col.insert_one({"name":n,"url":l})
        update_step(cid,"none"); bot.send_message(cid,f"✅ <b>চ্যানেল যোগ হয়েছে:</b> <b>{n}</b>\n🔗 <code>{l}</code>", disable_web_page_preview=True); return

    if step=="wait_add_tutorial":
        if "|" in text:
            n,l=[p.strip() for p in text.split("|",1)]
        else:
            n = "🎥 দেখার নিয়ম"
            l = text.strip()
        tutorials_col.insert_one({"name":n,"url":l})
        update_step(cid,"none"); bot.send_message(cid,f"✅ <b>টিউটোরিয়াল যোগ হয়েছে:</b> <b>{n}</b>\n🔗 <code>{l}</code>", disable_web_page_preview=True); return

    if step=="wait_restore" and message.document:
        try:
            bot.send_message(cid,"⏳ রিস্টোর হচ্ছে...")
            fi=bot.get_file(message.document.file_id); data=json.loads(bot.download_file(fi.file_path))
            for col_name,col_obj,key in [("users",users_col,"chat_id"),("files",files_col,"file_key")]:
                for item in data.get(col_name,[]):
                    if not col_obj.find_one({key:item.get(key)}): col_obj.insert_one(item)
            for col_name,col_obj in [("auto_channels",auto_channels_col),("force_sub",force_sub_col)]:
                if data.get(col_name): col_obj.insert_many(data[col_name])
            for s_ in data.get("settings",[]):
                settings_col.update_one({"key":s_.get("key")},{"$set":s_},upsert=True)
            update_step(cid,"none"); bot.send_message(cid,"✅ <b>রিস্টোর সম্পন্ন হয়েছে!</b>")
        except Exception as e: bot.send_message(cid,f"❌ রিস্টোর ব্যর্থ!\n<code>{e}</code>")
        return

    if step=="wait_web_title":
        title = "" if text == "/skip" else text.strip()
        if not title:
            title = (user.get("post_header") or "Untitled Video").strip()
            if not title:
                title = "Untitled Video"
        update_user(cid, {"pending_web_title": title, "post_header": title, "step": "none"})
        user2 = get_user(cid)
        _ask_post_options(cid, user2, user2.get("temp_media_type"), user2.get("temp_media_id"))
        return

    if step == "wait_manual_thumb_url":
        if text == "/skip":
            update_step(cid, "none")
            bot.send_message(cid, "⏩ থাম্বনেইল স্কিপ করা হয়েছে।")
            user2 = get_user(cid)
            _ask_web_title(cid, user2, user2.get("temp_media_type"), user2.get("temp_media_id"))
            return
        
        url_input = text.strip()
        if url_input.startswith("http://") or url_input.startswith("https://"):
            update_user(cid, {"pending_thumb_url": url_input, "step": "none"})
            bot.send_message(cid, "✅ ম্যানুয়াল থাম্বনেইল URL সেট করা হয়েছে!")
            user2 = get_user(cid)
            mtype = user2.get("temp_media_type") or "photo"
            mid_val = user2.get("temp_media_id") or ""
            _ask_web_title(cid, user2, mtype, mid_val)
        else:
            bot.send_message(cid, "⚠️ সঠিক ছবির URL দিন (http:// বা https:// দিয়ে শুরু হতে হবে) অথবা <code>/skip</code> লিখুন।")
        return

    if step=="wait_thumbnail":
        if text=="/skip":
            pending_link = user.get("pending_link", "")
            match = re.search(r'[?&]start=([A-Za-z0-9]+)', pending_link)
            orig_file_id = None
            orig_file_type = None
            if match:
                key = match.group(1)
                file_doc = files_col.find_one({"$or": [{"file_key": key}, {"batch_id": key}]})
                if file_doc:
                    orig_file_id = file_doc.get("file_id")
                    orig_file_type = file_doc.get("type")
            if orig_file_id:
                update_user(cid, {
                    "pending_thumb_url": "",
                    "temp_media_id": orig_file_id,
                    "temp_media_type": orig_file_type,
                    "step": "none"
                })
                bot.send_message(cid, "✅ থাম্বনেইল স্কিপ করা হয়েছে।")
                _ask_web_title(cid, get_user(cid), orig_file_type, orig_file_id)
            else:
                update_user(cid,{"step":"none","pending_link":"","pending_short_link":"","pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
                bot.send_message(cid,"✅ স্কিপ করা হয়েছে।")
            return
        if message.video:
            thumb_caption = message.caption or ""
            if thumb_caption:
                update_user(cid, {"post_header": thumb_caption})
            bot.send_message(cid, "⏳ ভিডিও থাম্বেইল আপলোড হচ্ছে...")
            
            thumb_file_id = None
            if hasattr(message.video, 'thumbnail') and message.video.thumbnail:
                thumb_file_id = message.video.thumbnail.file_id
            elif hasattr(message.video, 'thumb') and message.video.thumb:
                thumb_file_id = message.video.thumb.file_id
                
            thumb_url = ""
            if thumb_file_id:
                thumb_url = upload_photo_to_imgbb(thumb_file_id)
                
            updates = {
                "temp_media_id": message.video.file_id,
                "temp_media_type": "video",
                "pending_thumb_url": thumb_url,
                "last_thumb_file_id": thumb_file_id or ""
            }
            update_user(cid, updates)
            
            if thumb_url:
                bot.send_message(cid, "✅ ভিডিও থাম্বেইল ইমেজ তৈরি হয়েছে।")
                m=InlineKeyboardMarkup()
                m.row(InlineKeyboardButton("✅ Confirm",callback_data="confirm_vid_thumb"),InlineKeyboardButton("❌ বাতিল",callback_data="cancel_vid_thumb"))
                preview = (f"📝 ক্যাপশন সেট: <b>{thumb_caption[:50]}</b>\n\n") if thumb_caption else ""
                bot.send_message(cid, f"{preview}🎥 এই ভিডিওটি পোস্ট করবেন?", reply_markup=m); return
            else:
                m = InlineKeyboardMarkup()
                m.row(
                    InlineKeyboardButton("🔄 রি-আপলোড ImgBB", callback_data="reupload_thumb_imgbb"),
                    InlineKeyboardButton("🔗 ম্যানুয়াল লিংক", callback_data="manual_thumb_url")
                )
                m.row(
                    InlineKeyboardButton("✅ Confirm (থাম্বেইল ছাড়া)", callback_data="confirm_vid_thumb"),
                    InlineKeyboardButton("❌ বাতিল", callback_data="cancel_vid_thumb")
                )
                bot.send_message(cid, "⚠️ ImgBB সার্ভার সমস্যার কারণে থাম্বেইল URL তৈরি হয়নি।\nপুনরায় আপলোড করতে 'রি-আপলোড' চাপুন বা ম্যানুয়ালি লিংক দিন:", reply_markup=m); return
        elif message.photo:
            thumb_caption = message.caption or ""
            if thumb_caption:
                update_user(cid, {"post_header": thumb_caption})
                logger.info(f"Thumbnail caption set for {cid}: {thumb_caption[:50]}")
            bot.send_message(cid, "⏳ থাম্বেইল upload হচ্ছে...")
            photo_file_id = message.photo[-1].file_id
            thumb_url = upload_photo_to_imgbb(photo_file_id)
            updates = {"pending_thumb_url": thumb_url, "last_thumb_file_id": photo_file_id}
            update_user(cid, updates)
            if thumb_url:
                bot.send_message(cid, "✅ থাম্বেইল URL তৈরি হয়েছে।")
                _ask_web_title(cid, get_user(cid), "photo", photo_file_id); return
            else:
                m = InlineKeyboardMarkup()
                m.row(
                    InlineKeyboardButton("🔄 রি-আপলোড ImgBB", callback_data="reupload_thumb_imgbb"),
                    InlineKeyboardButton("🔗 ম্যানুয়াল লিংক", callback_data="manual_thumb_url")
                )
                m.row(
                    InlineKeyboardButton("⏩ স্কিপ / কনটিনিউ", callback_data="continue_no_thumb")
                )
                bot.send_message(cid, "⚠️ ImgBB সার্ভার সমস্যার কারণে থাম্বেইল আপলোড ফেল হয়েছে।\nনিচের অপশন সিলেক্ট করুন:", reply_markup=m); return
        else:
            bot.send_message(cid,"⚠️ ছবি বা ভিডিও দিন অথবা /skip লিখুন।"); return

    fid=ftype=None
    thumb_id=None
    if message.document:
        fid,ftype=message.document.file_id,"document"
        if hasattr(message.document, 'thumbnail') and message.document.thumbnail:
            thumb_id = message.document.thumbnail.file_id
        elif hasattr(message.document, 'thumb') and message.document.thumb:
            thumb_id = message.document.thumb.file_id
    elif message.video and step!="wait_thumbnail":
        fid,ftype=message.video.file_id,"video"
        if hasattr(message.video, 'thumbnail') and message.video.thumbnail:
            thumb_id = message.video.thumbnail.file_id
        elif hasattr(message.video, 'thumb') and message.video.thumb:
            thumb_id = message.video.thumb.file_id
    elif message.audio:                            fid,ftype=message.audio.file_id,"audio"
    elif message.photo and step!="wait_thumbnail": fid,ftype=message.photo[-1].file_id,"photo"

    if fid:
        if message.caption:
            update_user(cid, {"post_header": message.caption})
        uid=str(uuid.uuid4().hex)[:10]; lch=""; lmid=""
        log_ch=auto_channels_col.find_one({"type":"log","status":"on"})
        if log_ch:
            try:
                cap_log=f"💾 <b>Backup</b> | 🔑 <code>{uid}</code> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                res=None
                if ftype=="document": res=bot.send_document(log_ch['channel_id'],fid,caption=cap_log)
                elif ftype=="video":  res=bot.send_video(log_ch['channel_id'],fid,caption=cap_log)
                elif ftype=="photo":  res=bot.send_photo(log_ch['channel_id'],fid,caption=cap_log)
                elif ftype=="audio":  res=bot.send_audio(log_ch['channel_id'],fid,caption=cap_log)
                if res: lch,lmid=log_ch['channel_id'],res.message_id
            except Exception as e: logger.warning(f"Log backup: {e}")

        auto_thumb_url = ""
        if thumb_id:
            auto_thumb_url = upload_photo_to_imgbb(thumb_id)

        doc={"file_key":uid,"file_id":fid,"type":ftype,"uploader":cid,"log_chat_id":lch,"log_msg_id":lmid,"uploaded_at":datetime.now().isoformat()}
        if thumb_id:
            doc["thumb_file_id"] = thumb_id
        if auto_thumb_url:
            doc["auto_thumb_url"] = auto_thumb_url

        if step=="wait_batch":
            doc["batch_id"]=user.get("batch_id"); files_col.insert_one(doc)
            cnt=files_col.count_documents({"batch_id":user.get("batch_id")})
            m=InlineKeyboardMarkup(); m.add(InlineKeyboardButton("✅ আপলোড শেষ — Finish",callback_data="finish_batch"))
            bot.send_message(cid,f"✅ <b>#{cnt} ফাইল ব্যাচে যোগ হয়েছে!</b>",reply_markup=m)
        else:
            doc["batch_id"]=""; files_col.insert_one(doc)
            dl=f"https://t.me/{BOT_USERNAME}?start={uid}"; sl=get_short_link(dl)
            update_user(cid,{"step":"wait_thumbnail","pending_link":dl,"pending_short_link":sl,"total_uploads":user.get("total_uploads",0)+1,"pending_thumb_url":"","pending_web_title":"","pending_web_video_id":"","pending_web_post_link":""})
            _inc_stat("uploads")
            m = InlineKeyboardMarkup()
            m.row(
                InlineKeyboardButton("Auto Generate 🤖", callback_data="autogen_thumb"),
                InlineKeyboardButton("Skip ⏩", callback_data="skip_thumb")
            )
            m.row(
                InlineKeyboardButton("🔗 ম্যানুয়াল ইমেজ লিংক", callback_data="manual_thumb_url")
            )
            bot.send_message(cid,
                f"✅ <b>ফাইল সেভ হয়েছে!</b>\n\n💎 Direct Link:\n<code>{dl}</code>\n\n📺 Short Link:\n<code>{sl}</code>\n\n🖼️ থাম্বনেইল (ছবি/ভিডিও) পাঠান অথবা নিচের বাটন ব্যবহার করুন।",
                disable_web_page_preview=True,
                reply_markup=m
            )

# ══════════════════════════════════════════════════
#  Flask Server
# ══════════════════════════════════════════════════
shortener_bp = Blueprint('shortener', __name__)

PANEL_SECRET = os.environ.get("PANEL_SECRET", "my-secret-key-change-this")

def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if request.method == 'OPTIONS':
            return jsonify({"ok": True}), 200
        key = request.headers.get("X-Admin-Key", "")
        if key != PANEL_SECRET:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

@shortener_bp.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Admin-Key"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return response

@shortener_bp.route('/shortener/status', methods=['GET','OPTIONS'])
def home():
    s = get_stats()
    return jsonify({"status":"running","version":BOT_VERSION,"users":s['total_users'],"files":s['total_files']})

@shortener_bp.route('/health', methods=['GET','OPTIONS'])
def health():
    return jsonify({"status":"ok","time":datetime.now().isoformat()})

@shortener_bp.route('/panel')
def panel():
    import os
    for p in ['Shortener bot/admin_panel.html', 'admin_panel.html', 'static/admin_panel.html']:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                content = f.read()
                content = content.replace('let CFG={},', f'let CFG={{key:"{PANEL_SECRET}"}},')
                return content, 200, {'Content-Type': 'text/html; charset=utf-8'}
    return '<h2>admin_panel.html ফাইল পাওয়া যায়নি। bot.py এর পাশে রাখুন।</h2>', 200, {'Content-Type': 'text/html; charset=utf-8'}

@shortener_bp.route('/api/stats', methods=['GET','OPTIONS'])
@require_auth
def api_stats():
    s = get_stats()
    all_pending = list(scheduled_col.find({"status": "pending"}))
    s['pending_scheduled'] = len(all_pending)
    s['pending_scheduled_timed'] = len([x for x in all_pending if not str(x.get('scheduled_at','')).startswith('203') and x.get('time_set') is not False])
    s['pending_scheduled_unscheduled'] = s['pending_scheduled'] - s['pending_scheduled_timed']
    return jsonify(s)

@shortener_bp.route('/api/scheduled', methods=['GET','OPTIONS'])
@require_auth
def api_scheduled():
    items = list(scheduled_col.find({}, {"_id":0}).sort("created_at", -1).limit(300))
    for item in items:
        # Determine if real time is set or it's an unscheduled draft from bot
        is_timed = not str(item.get("scheduled_at", "")).startswith("203") and item.get("time_set") is not False
        item["is_time_set"] = is_timed
        if not is_timed and item.get("status") == "pending":
            item["queue_type"] = "unscheduled"
        elif is_timed and item.get("status") == "pending":
            item["queue_type"] = "timed"
        else:
            item["queue_type"] = item.get("status", "done")
    return jsonify(items)

@shortener_bp.route('/api/scheduled/delete/<sched_id>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_sched(sched_id):
    result = scheduled_col.delete_one({"sched_id": sched_id})
    return jsonify({"ok": True, "deleted": result.deleted_count})

@shortener_bp.route('/api/scheduled/post_now/<sched_id>', methods=['POST','OPTIONS'])
@require_auth
def api_post_now_sched(sched_id):
    item = scheduled_col.find_one({"sched_id": sched_id, "status": "pending"})
    if not item:
        return jsonify({"ok": False, "error": "সিডিউল পোস্ট পাওয়া যায়নি বা আগেই পোস্ট হয়েছে"}), 404
    
    admin_id = item['admin_id']
    user = get_user(admin_id)
    cat_id = item.get("category_id", "")
    mtype  = item['media_type']
    mid_   = item['media_id']
    d_link = item.get('d_link','')
    s_link = item.get('s_link','')
    user["pending_link"] = d_link
    user["pending_short_link"] = s_link
    user["pending_web_title"] = item.get("web_title", "")
    user["pending_thumb_url"] = item.get("thumb_url", "")
    user["pending_web_video_id"] = item.get("web_video_id", "")
    user["pending_web_post_link"] = item.get("web_post_link", "")
    user["pending_web_ads"] = item.get("web_ads", 1)

    count = 0
    if cat_id:
        cat = get_category(cat_id)
        if not user.get("pending_web_post_link"):
            user["pending_web_post_link"] = create_web_video_entry(user, cat.get("name", "Others") if cat else "Others")
        count = _post_to_category(cat_id, mtype, mid_, user, d_link, s_link)
    else:
        if not user.get("pending_web_post_link"):
            user["pending_web_post_link"] = create_web_video_entry(user, "Others")
        _do_post_all_channels(admin_id, user, mtype, mid_, d_link, s_link)
        count = 1

    scheduled_col.update_one({"sched_id": sched_id}, {"$set": {"status": "done", "posted_at": datetime.now().isoformat()}})
    return jsonify({"ok": True, "count": count})

@shortener_bp.route('/api/scheduled/update_time/<sched_id>', methods=['POST','OPTIONS'])
@require_auth
def api_update_sched_time(sched_id):
    data = request.json or {}
    new_time = data.get("scheduled_at", "")
    if not new_time:
        return jsonify({"ok": False, "error": "নতুন সময় দিন"}), 400
    
    try:
        new_time = new_time.replace("T", " ")
        dt = datetime.strptime(new_time.strip(), "%Y-%m-%d %H:%M")
        dt_utc = dt - timedelta(hours=6) # UTC+6 to UTC
        scheduled_col.update_one({"sched_id": sched_id}, {"$set": {"scheduled_at": dt_utc.isoformat(), "status": "pending", "time_set": True}})
        return jsonify({"ok": True})
    except ValueError:
        return jsonify({"ok": False, "error": "সঠিক ফরম্যাট দিন (YYYY-MM-DD HH:MM)"}), 400

@shortener_bp.route('/api/scheduled/bulk_interval', methods=['POST','OPTIONS'])
@require_auth
def api_bulk_interval_sched():
    data = request.json or {}
    sched_ids = data.get("sched_ids", [])
    start_time_str = data.get("start_time", "")
    interval_minutes = int(data.get("interval_minutes", 360))

    if not sched_ids:
        pending_items = list(scheduled_col.find({"status": "pending"}).sort("created_at", 1))
    else:
        pending_items = list(scheduled_col.find({"sched_id": {"$in": sched_ids}}).sort("created_at", 1))

    if not pending_items:
        return jsonify({"ok": False, "error": "কোনো সিডিউল পোস্ট পাওয়া যায়নি"}), 404

    if start_time_str:
        try:
            dt_start_local = datetime.strptime(start_time_str.replace("T", " ").strip(), "%Y-%m-%d %H:%M")
            dt_start_utc = dt_start_local - timedelta(hours=6)
        except ValueError:
            dt_start_utc = datetime.utcnow()
    else:
        dt_start_utc = datetime.utcnow()

    updated_count = 0
    for idx, item in enumerate(pending_items):
        scheduled_dt = dt_start_utc + timedelta(minutes=idx * interval_minutes)
        scheduled_col.update_one(
            {"sched_id": item["sched_id"]},
            {"$set": {"scheduled_at": scheduled_dt.isoformat(), "status": "pending", "time_set": True}}
        )
        updated_count += 1

    return jsonify({"ok": True, "updated_count": updated_count})

@shortener_bp.route('/api/scheduled/bulk_delete', methods=['POST','DELETE','OPTIONS'])
@require_auth
def api_bulk_delete_sched():
    data = request.json or {}
    sched_ids = data.get("sched_ids", [])
    if not sched_ids:
        return jsonify({"ok": False, "error": "কোনো পোস্ট সিলেক্ট করা হয়নি"}), 400
    res = scheduled_col.delete_many({"sched_id": {"$in": sched_ids}})
    return jsonify({"ok": True, "deleted": res.deleted_count})

@shortener_bp.route('/api/categories', methods=['GET','OPTIONS'])
@require_auth
def api_categories():
    return jsonify(list(categories_col.find({}, {"_id":0})))

@shortener_bp.route('/api/channels', methods=['GET','OPTIONS'])
@require_auth
def api_channels():
    return jsonify(list(auto_channels_col.find({}, {"_id":0})))

@shortener_bp.route('/api/channels/delete/<ch_id>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_channel(ch_id):
    auto_channels_col.delete_one({"ch_id": ch_id})
    return jsonify({"ok": True})

@shortener_bp.route('/api/admins', methods=['GET','OPTIONS'])
@require_auth
def api_admins():
    return jsonify(list(admins_col.find({}, {"_id":0})))

@shortener_bp.route('/api/admins/add', methods=['POST','OPTIONS'])
@require_auth
def api_add_admin():
    data = request.json or {}
    chat_id = str(data.get("chat_id","")).strip()
    if not chat_id:
        return jsonify({"ok": False, "error": "chat_id দিন"}), 400
    if admins_col.find_one({"chat_id": chat_id}):
        return jsonify({"ok": False, "error": "এই আইডি আগেই এডমিন"})
    admins_col.insert_one({"chat_id": chat_id, "role": "admin", "added_at": datetime.now().isoformat()})
    try: bot.send_message(chat_id, "🎉 আপনাকে এডমিন করা হয়েছে!")
    except: pass
    return jsonify({"ok": True})

@shortener_bp.route('/api/admins/delete/<chat_id>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_admin(chat_id):
    if str(chat_id) == str(MAIN_ADMIN_ID):
        return jsonify({"ok": False, "error": "Super Admin সরানা যাবে না"}), 403
    admins_col.delete_one({"chat_id": str(chat_id)})
    return jsonify({"ok": True})

@shortener_bp.route('/api/users', methods=['GET','OPTIONS'])
@require_auth
def api_users():
    limit = int(request.args.get("limit", 50))
    search = request.args.get("q","").strip()
    query = {}
    if search:
        query = {"chat_id": {"$regex": search}}
    items = list(users_col.find(query, {"_id":0}).sort("last_active",-1).limit(limit))
    return jsonify(items)

@shortener_bp.route('/api/users/ban', methods=['POST','OPTIONS'])
@require_auth
def api_ban_user():
    data = request.json or {}
    chat_id = str(data.get("chat_id","")).strip()
    reason = data.get("reason","প্যানেল থেকে ব্যান")
    if not chat_id or chat_id == str(MAIN_ADMIN_ID):
        return jsonify({"ok": False, "error": "অবৈধ অনুরোধ"}), 400
    if not banned_col.find_one({"chat_id": chat_id}):
        banned_col.insert_one({"chat_id": chat_id, "reason": reason, "banned_at": datetime.now().isoformat()})
    return jsonify({"ok": True})

@shortener_bp.route('/api/files', methods=['GET','OPTIONS'])
@require_auth
def api_files():
    limit = int(request.args.get("limit", 50))
    search = request.args.get("q","").strip()
    query = {}
    if search:
        query = {"file_key": {"$regex": search}}
    items = list(files_col.find(query, {"_id":0}).sort("uploaded_at",-1).limit(limit))
    return jsonify(items)

@shortener_bp.route('/api/forcesub', methods=['GET','OPTIONS'])
@require_auth
def api_forcesub():
    return jsonify(list(force_sub_col.find({}, {"_id":0})))

@shortener_bp.route('/api/forcesub/toggle/<fs_id>', methods=['POST','OPTIONS'])
@require_auth
def api_toggle_forcesub(fs_id):
    fs = force_sub_col.find_one({"fs_id": fs_id})
    if not fs:
        return jsonify({"ok": False}), 404
    new_status = "off" if fs.get("status") == "on" else "on"
    force_sub_col.update_one({"fs_id": fs_id}, {"$set": {"status": new_status}})
    return jsonify({"ok": True, "status": new_status})

@shortener_bp.route('/api/forcesub/delete/<fs_id>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_forcesub(fs_id):
    force_sub_col.delete_one({"fs_id": fs_id})
    return jsonify({"ok": True})

@shortener_bp.route('/api/settings', methods=['GET','POST','OPTIONS'])
@require_auth
def api_settings():
    if request.method == 'POST':
        data = request.json or {}
        key = data.get("key","")
        value = data.get("value", 0)
        if key:
            set_setting(key, value)
            return jsonify({"ok": True, "key": key, "value": value})
        return jsonify({"ok": False, "error": "key দিন"}), 400
    keys = ['protect_content','short_link_on','force_sub_on','btn_download','btn_download_1','btn_download_2','btn_share','btn_tutorial']
    return jsonify({k: get_setting(k, 0) for k in keys})

@shortener_bp.route('/api/files/delete/<file_key>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_file(file_key):
    files_col.delete_one({"file_key": file_key})
    return jsonify({"ok": True})

@shortener_bp.route('/api/channels/add', methods=['POST','OPTIONS'])
@require_auth
def api_add_channel():
    data = request.json or {}
    name = data.get("name", "").strip()
    channel_id = str(data.get("channel_id", "")).strip()
    type_ = data.get("type", "ad").strip()
    lang = data.get("lang", "en").strip()
    tutorial_url_1 = data.get("tutorial_url_1", "").strip()
    tutorial_url_2 = data.get("tutorial_url_2", "").strip()
    if not name or not channel_id:
        return jsonify({"ok": False, "error": "নাম এবং আইডি দিন"}), 400
    ch_id = str(uuid.uuid4().hex)[:8]
    auto_channels_col.insert_one({
        "ch_id": ch_id,
        "type": type_,
        "name": name,
        "channel_id": channel_id,
        "lang": lang if lang in LANG_TEXTS else "en",
        "tutorial_url_1": tutorial_url_1,
        "tutorial_url_2": tutorial_url_2,
        "status": "on"
    })
    return jsonify({"ok": True})

@shortener_bp.route('/api/channels/update/<ch_id>', methods=['POST','OPTIONS'])
@require_auth
def api_update_channel(ch_id):
    data = request.json or {}
    ch = get_auto_channel(ch_id)
    if not ch:
        return jsonify({"ok": False, "error": "চ্যানেল পাওয়া যায়নি"}), 404
    
    update_fields = {}
    if "name" in data: update_fields["name"] = data["name"].strip()
    if "channel_id" in data: update_fields["channel_id"] = str(data["channel_id"]).strip()
    if "type" in data: update_fields["type"] = data["type"].strip()
    if "lang" in data:
        l_val = data["lang"].strip()
        update_fields["lang"] = l_val if l_val in LANG_TEXTS else "en"
    if "tutorial_url_1" in data: update_fields["tutorial_url_1"] = data["tutorial_url_1"].strip()
    if "tutorial_url_2" in data: update_fields["tutorial_url_2"] = data["tutorial_url_2"].strip()
    if "status" in data: update_fields["status"] = data["status"].strip()

    if update_fields:
        auto_channels_col.update_one({"_id": ch["_id"]}, {"$set": update_fields})
        # Sync updates to any category channel lists
        for cat in categories_col.find():
            chs = cat.get("channels", [])
            changed = False
            for c in chs:
                if c.get("channel_id") == ch.get("channel_id"):
                    for k, v in update_fields.items():
                        c[k] = v
                    changed = True
            if changed:
                categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": chs}})
    return jsonify({"ok": True})

@shortener_bp.route('/api/categories/add', methods=['POST','OPTIONS'])
@require_auth
def api_add_category():
    data = request.json or {}
    name = data.get("name", "").strip()
    lang = data.get("lang", "en").strip()
    tutorial_url_1 = data.get("tutorial_url_1", "").strip()
    tutorial_url_2 = data.get("tutorial_url_2", "").strip()
    if not name:
        return jsonify({"ok": False, "error": "নাম দিন"}), 400
    cat_id = str(uuid.uuid4().hex)[:8]
    categories_col.insert_one({
        "cat_id": cat_id,
        "name": name,
        "lang": lang if lang in LANG_TEXTS else "en",
        "tutorial_url_1": tutorial_url_1,
        "tutorial_url_2": tutorial_url_2,
        "channels": [],
        "created_at": datetime.now().isoformat()
    })
    sync_categories_to_firebase()
    return jsonify({"ok": True})

@shortener_bp.route('/api/lang_tutorials', methods=['GET','OPTIONS'])
@require_auth
def api_get_lang_tutorials():
    return jsonify({
        "bn_1": get_lang_tutorial("bn", 1),
        "bn_2": get_lang_tutorial("bn", 2),
        "hi_1": get_lang_tutorial("hi", 1),
        "hi_2": get_lang_tutorial("hi", 2),
        "en_1": get_lang_tutorial("en", 1),
        "en_2": get_lang_tutorial("en", 2),
    })

@shortener_bp.route('/api/lang_tutorials', methods=['POST','OPTIONS'])
@require_auth
def api_set_lang_tutorials():
    data = request.json or {}
    for lang in ["bn", "hi", "en"]:
        for num in [1, 2]:
            k = f"{lang}_{num}"
            if k in data:
                set_lang_tutorial(lang, num, str(data[k]).strip())
    return jsonify({"ok": True})

@shortener_bp.route('/api/categories/update/<cat_id>', methods=['POST','OPTIONS'])
@require_auth
def api_update_category(cat_id):
    data = request.json or {}
    cat = get_category(cat_id)
    if not cat:
        return jsonify({"ok": False, "error": "ক্যাটাগরি পাওয়া যায়নি"}), 404
    
    update_fields = {}
    if "name" in data: update_fields["name"] = data["name"].strip()
    if "lang" in data:
        l_val = data["lang"].strip()
        update_fields["lang"] = l_val if l_val in LANG_TEXTS else "en"
    if "tutorial_url_1" in data: update_fields["tutorial_url_1"] = data["tutorial_url_1"].strip()
    if "tutorial_url_2" in data: update_fields["tutorial_url_2"] = data["tutorial_url_2"].strip()

    for l in ["bn", "hi", "en"]:
        for n in [1, 2]:
            k = f"tutorial_url_{n}_{l}"
            if k in data:
                update_fields[k] = str(data[k]).strip()

    if "channels" in data:
        update_fields["channels"] = data["channels"]

    if update_fields:
        categories_col.update_one({"_id": cat["_id"]}, {"$set": update_fields})
        sync_categories_to_firebase()
    return jsonify({"ok": True})

@shortener_bp.route('/api/categories/delete/<cat_id>', methods=['DELETE','OPTIONS'])
@require_auth
def api_delete_category(cat_id):
    cat = get_category(cat_id)
    if cat:
        categories_col.delete_one({"_id": cat["_id"]})
    else:
        categories_col.delete_one({"cat_id": cat_id})
    sync_categories_to_firebase()
    return jsonify({"ok": True})

@shortener_bp.route('/api/categories/update_channels/<cat_id>', methods=['POST','OPTIONS'])
@require_auth
def api_update_category_channels(cat_id):
    data = request.json or {}
    channels = data.get("channels", [])
    cat = get_category(cat_id)
    if cat:
        categories_col.update_one({"_id": cat["_id"]}, {"$set": {"channels": channels}})
    else:
        categories_col.update_one({"cat_id": cat_id}, {"$set": {"channels": channels}})
    return jsonify({"ok": True})

@shortener_bp.route('/api/forcesub/add', methods=['POST','OPTIONS'])
@require_auth
def api_add_forcesub():
    data = request.json or {}
    name = data.get("name", "").strip()
    channel_id = str(data.get("channel_id", "")).strip()
    url = data.get("url", "").strip()
    if not name or not channel_id or not url:
        return jsonify({"ok": False, "error": "সব তথ্য পূরণ করুন"}), 400
    fs_id = str(uuid.uuid4().hex)[:8]
    force_sub_col.insert_one({
        "fs_id": fs_id,
        "name": name,
        "channel_id": channel_id,
        "url": url,
        "status": "on"
    })
    return jsonify({"ok": True})

def run_bot():
    if not BOT_TOKEN or BOT_TOKEN == "DUMMY_TOKEN":
        logger.error("❌ SHORTENER_BOT_TOKEN or BOT_TOKEN is not set in environment variables! Polling aborted.")
        return
    logger.info(f"🚀 Shortener Bot Polling started (v{BOT_VERSION})...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Shortener Bot Polling error: {e}")
            time.sleep(5)
