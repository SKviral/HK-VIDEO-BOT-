"""
╔══════════════════════════════════════════════════════════════╗
║           🚀 PREMIUM FILE SHARE BOT v5.0                     ║
║   Beautiful UI · Share Button · Post Buttons ON/OFF          ║
║   Link/Text Filter · Protect Content · Force Subscribe       ║
╚══════════════════════════════════════════════════════════════╝
"""

import os, re, time, json, uuid, threading, requests, telebot, logging
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from flask import Flask
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
BOT_TOKEN     = os.environ.get("BOT_TOKEN",     "আপনার_বট_টোকেন")
BOT_USERNAME  = os.environ.get("BOT_USERNAME",  "YourBotUsername")
MAIN_ADMIN_ID = os.environ.get("MAIN_ADMIN_ID", "5991854507")
TERABOX_TOKEN = os.environ.get("TERABOX_TOKEN", "71b16be6b48d01937bfe7d2c3043cbc0b6363c82")
MONGO_URL     = os.environ.get("MONGO_URL",     "আপনার_MongoDB_URL")
BOT_VERSION   = "5.0.0"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

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

users_col.create_index("chat_id", unique=True, background=True)
files_col.create_index("file_key", background=True)
files_col.create_index("batch_id", background=True)
queue_col.create_index("delete_at", background=True)

if not admins_col.find_one({"chat_id": str(MAIN_ADMIN_ID)}):
    admins_col.insert_one({"chat_id": str(MAIN_ADMIN_ID), "role": "super_admin", "added_at": datetime.now().isoformat()})

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
    """1 → 🟢  0 → 🔴"""
    return "🟢" if val else "🔴"

# ══════════════════════════════════════════════════
#  ফিল্টার ইউটিলিটি
# ══════════════════════════════════════════════════
URL_RE = re.compile(r'(https?://[^\s]+|t\.me/[^\s]+|@[A-Za-z0-9_]{5,})', re.IGNORECASE)

def filter_links(text):
    if not text: return text
    return re.sub(r'\n{3,}', '\n\n', URL_RE.sub('', text)).strip()

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
    "auto_delete": 0, "pending_link": "", "pending_short_link": "",
    "step": "none", "batch_id": "",
    # Post button toggles (নতুন)
    "btn_download": 1,      # 📥 ডাউনলোড বাটন
    "btn_share": 1,         # 🔗 শেয়ার বাটন
    "btn_tutorial": 1,      # 📽️ টিউটোরিয়াল বাটন
    "btn_link_in_caption": 1,  # ক্যাপশনে লিংক
    "link_repeat_count": 1,
    "custom_buttons": [],
    "temp_media_id": "", "temp_media_type": "",
    "joined_at": "", "last_active": "",
    "total_downloads": 0, "total_uploads": 0,
    "link_filter": 0, "text_filter": 0,
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

def is_admin(chat_id):  return bool(admins_col.find_one({"chat_id": str(chat_id)}))
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
    mk.add(InlineKeyboardButton("✅ Join করেছি — যাচাই করুন",
                                callback_data=f"check_sub_{file_key or 'none'}"))
    bot.send_message(chat_id,
        "🔒 <b>ফাইল পেতে নিচের চ্যানেলগুলোতে Join করুন!</b>\n\n"
        "Join করার পর ✅ বাটনে ক্লিক করুন।", reply_markup=mk)

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
                    bot.send_message(item['chat_id'],
                        "⚠️ <b>সময় শেষ! ফাইলটি মুছে গেছে।</b>\n🔁 আবার পেতে লিংকে ক্লিক করুন।",
                        reply_markup=mk if mk.keyboard else None)
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
    try: bot.send_message(admin_id,
        f"✅ <b>ব্রডকাস্ট সম্পন্ন!</b>\n📨 মোট: <b>{total}</b>\n✅ <b>{ok}</b> | ❌ <b>{fail}</b>")
    except: pass

# ══════════════════════════════════════════════════
#  শর্ট লিংক
# ══════════════════════════════════════════════════
def get_short_link(url):
    try:
        r = requests.get(
            f"https://teraboxlinks.com/api?api={TERABOX_TOKEN}&url={quote(url)}", timeout=8
        ).json()
        if r and r.get("status") != "error" and r.get("shortenedUrl"):
            return r["shortenedUrl"]
    except Exception as e: logger.warning(f"ShortLink: {e}")
    return url

# ══════════════════════════════════════════════════
#  পোস্ট মার্কআপ বিল্ডার  (নতুন — বাটন ON/OFF সহ)
# ══════════════════════════════════════════════════
def _build_post_markup(user, dl_link, share_text):
    """
    পোস্টের বাটন মার্কআপ তৈরি করে।
    share_text → Telegram-এর inline share URL-এ যাবে।
    """
    mk = InlineKeyboardMarkup()

    # ── টিউটোরিয়াল বাটন ──
    if user.get("btn_tutorial", 1):
        for tut in tutorials_col.find():
            mk.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))

    # ── কাস্টম বাটন ──
    for btn in user.get("custom_buttons", []):
        if btn.get("status") == "on":
            mk.add(InlineKeyboardButton(btn['name'], url=btn['url']))

    # ── ডাউনলোড + শেয়ার (একই সারিতে) ──
    row = []
    if user.get("btn_download", 1):
        row.append(InlineKeyboardButton("📥 ডাউনলোড", url=dl_link))
    if user.get("btn_share", 1):
        # Telegram share URL — ইউজারের contacts/groups-এ শেয়ার অপশন খুলে দেয়
        encoded = quote(share_text, safe='')
        share_url = f"https://t.me/share/url?url={encoded}"
        row.append(InlineKeyboardButton("🔗 শেয়ার করুন", url=share_url))
    if row:
        mk.row(*row)

    return mk

# ══════════════════════════════════════════════════
#  চ্যানেলে পোস্ট
# ══════════════════════════════════════════════════
def _send_media(ch_id, mtype, mid, caption, markup, protect):
    kw = {"caption": caption, "reply_markup": markup, "protect_content": protect}
    if mtype == 'photo': bot.send_photo(ch_id, mid, **kw)
    elif mtype == 'video': bot.send_video(ch_id, mid, **kw)

def execute_channel_post(chat_id, user, mtype, mid):
    d_link = user.get("pending_link", "")
    s_link = user.get("pending_short_link", "")

    # ফিল্টার প্রয়োগ
    ph = apply_filters(user.get('post_header',''), chat_id)
    pf = apply_filters(user.get('post_footer',''), chat_id)
    ph_t = f"{ph}\n\n" if ph else ""
    pf_t = f"\n\n{pf}" if pf else ""
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    protect = bool(get_setting("protect_content", 0))

    # ── Ad channel ──
    if user.get("btn_link_in_caption", 1):
        rpt = max(1, min(user.get("link_repeat_count", 1), 5))
        ad_links   = "\n".join([s_link]*rpt)
        ad_caption = f"{ph_t}🔗 <b>Download Link:</b>\n{ad_links}\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
    else:
        ad_caption = f"{ph_t}{pf_t}".strip()

    ad_markup = _build_post_markup(user, s_link, s_link)

    # ── Premium channel ──
    if user.get("btn_link_in_caption", 1):
        rpt = max(1, min(user.get("link_repeat_count", 1), 5))
        pr_links    = "\n".join([d_link]*rpt)
        prem_caption= f"{ph_t}🔗 <b>Direct Download:</b>\n{pr_links}\n\n<i>🕐 {now_str}</i>{pf_t}".strip()
    else:
        prem_caption = f"{ph_t}{pf_t}".strip()

    prem_markup = _build_post_markup(user, d_link, d_link)

    post_count = 0
    for ch in auto_channels_col.find({"type": "ad", "status": "on"}):
        try:  _send_media(ch['channel_id'], mtype, mid, ad_caption, ad_markup, protect); post_count += 1
        except Exception as e: logger.warning(f"Ad post {ch.get('name')}: {e}")

    for ch in auto_channels_col.find({"type": "premium", "status": "on"}):
        try:  _send_media(ch['channel_id'], mtype, mid, prem_caption, prem_markup, protect); post_count += 1
        except Exception as e: logger.warning(f"Premium post {ch.get('name')}: {e}")

    for ch in auto_channels_col.find({"type": "log", "status": "on"}):
        try:
            log_cap = f"💾 <b>Backup</b> | 📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            _send_media(ch['channel_id'], mtype, mid, log_cap, None, False)
        except Exception as e: logger.warning(f"Log post: {e}")

    _inc_stat("uploads")
    bot.send_message(chat_id,
        f"✅ <b>পোস্ট সম্পন্ন!</b>\n"
        f"📤 <b>{post_count}</b>টি চ্যানেলে পোস্ট হয়েছে।\n"
        f"🔒 Protect: {_ico(protect)} | 🔗 LF: {_ico(user.get('link_filter'))} | 📝 TF: {_ico(user.get('text_filter'))}"
    )
    update_user(chat_id, {"step":"none","pending_link":"","pending_short_link":"","temp_media_id":"","temp_media_type":""})

# ══════════════════════════════════════════════════
#  ফাইল ডেলিভারি
# ══════════════════════════════════════════════════
def _deliver_files(chat_id, file_key, user):
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
    delivered = 0

    for f in files:
        sent_id = None
        kw = {"caption": caption, "reply_markup": mk if mk.keyboard else None, "protect_content": protect}
        try:
            res = None
            if f['type']=='document': res = bot.send_document(chat_id, f['file_id'], **kw)
            elif f['type']=='video':  res = bot.send_video(chat_id,    f['file_id'], **kw)
            elif f['type']=='photo':  res = bot.send_photo(chat_id,    f['file_id'], **kw)
            elif f['type']=='audio':  res = bot.send_audio(chat_id,    f['file_id'],
                                          caption=caption, reply_markup=kw['reply_markup'], protect_content=protect)
            if res: sent_id = res.message_id; delivered += 1
        except:
            if f.get('log_chat_id') and f.get('log_msg_id'):
                try:
                    res = bot.copy_message(chat_id, f['log_chat_id'], f['log_msg_id'],
                                           caption=caption, reply_markup=mk if mk.keyboard else None,
                                           protect_content=protect)
                    sent_id = res.message_id; delivered += 1
                except: pass

        if sent_id and uploader.get("auto_delete", 0) > 0:
            queue_col.insert_one({"chat_id": chat_id, "message_id": sent_id,
                                   "delete_at": int(time.time()) + uploader["auto_delete"]*60})
        time.sleep(0.3)

    if delivered:
        _inc_stat("downloads", delivered)
        update_user(chat_id, {"total_downloads": user.get("total_downloads",0)+delivered})
        if uploader.get("auto_delete",0) > 0:
            bot.send_message(chat_id,
                f"⚠️ <i>ফাইল{'গুলো' if delivered>1 else 'টি'} "
                f"<b>{uploader['auto_delete']} মিনিট</b> পর মুছে যাবে।</i>")
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
    m.row(_btn("📢 ব্রডকাস্ট", "broadcast"), _btn("ℹ️ হেল্প", "help_menu"))
    return m

# ══════════════════════════════════════════════════
#  পোস্ট বাটন সেটিংস মেনু (নতুন)
# ══════════════════════════════════════════════════
def _post_btn_menu(u):
    """পোস্টের প্রতিটি বাটন ON/OFF টগল মেনু"""
    dl  = _ico(u.get("btn_download",1))
    sh  = _ico(u.get("btn_share",1))
    tut = _ico(u.get("btn_tutorial",1))
    lc  = _ico(u.get("btn_link_in_caption",1))
    rc  = u.get("link_repeat_count",1)

    m = _mk()
    m.row(
        _btn(f"📥 ডাউনলোড বাটন {dl}",  "togbtn_download"),
        _btn(f"🔗 শেয়ার বাটন {sh}",    "togbtn_share")
    )
    m.row(
        _btn(f"📽️ টিউটোরিয়াল {tut}",   "togbtn_tutorial"),
        _btn(f"📝 লিংক ক্যাপশনে {lc}", "togbtn_link_caption")
    )
    m.add(_btn(f"🔄 লিংক রিপিট: {rc}x", "set_link_repeat"))
    m.add(_btn("🔘 কাস্টম বাটন ম্যানেজ", "menu_custom_buttons"))
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

    # ── Force Sub Check ──
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

    # ── Thumbnail Confirm/Cancel ──
    if data == "confirm_vid_thumb":
        bot.delete_message(cid, mid)
        execute_channel_post(cid, user, user.get("temp_media_type"), user.get("temp_media_id")); return
    if data == "cancel_vid_thumb":
        bot.delete_message(cid, mid)
        update_user(cid, {"step":"wait_thumbnail","temp_media_id":"","temp_media_type":""})
        bot.send_message(cid, "❌ বাতিল। নতুন থাম্বনেইল দিন।"); return

    # ════════════════════════════════════════
    #  MAIN MENU
    # ════════════════════════════════════════
    if data == "main_menu":
        update_step(cid, "none")
        s = get_stats()
        bot.edit_message_text(
            f"╔══════════════════════════╗\n"
            f"║   🤖 <b>এডমিন প্যানেল</b>   ║\n"
            f"╚══════════════════════════╝\n\n"
            f"👥 মোট ইউজার : <b>{s['total_users']}</b>\n"
            f"📁 মোট ফাইল  : <b>{s['total_files']}</b>\n"
            f"🟢 আজ সক্রিয় : <b>{s['active_today']}</b>\n"
            f"📥 আজ ডাউনলোড: <b>{s['dl_today']}</b>",
            cid, mid, reply_markup=_main_menu()
        )

    # ════════════════════════════════════════
    #  STATS
    # ════════════════════════════════════════
    elif data == "show_stats":
        s = get_stats(); m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(
            f"📊 <b>বট স্ট্যাটিস্টিক্স</b>\n"
            f"{'─'*26}\n"
            f"👥 মোট ইউজার   : <b>{s['total_users']}</b>\n"
            f"🟢 আজ সক্রিয়   : <b>{s['active_today']}</b>\n"
            f"📁 মোট ফাইল    : <b>{s['total_files']}</b>\n"
            f"📥 আজ ডাউনলোড : <b>{s['dl_today']}</b>\n"
            f"📤 আজ আপলোড   : <b>{s['ul_today']}</b>\n"
            f"👑 এডমিন       : <b>{s['total_admins']}</b>\n"
            f"🚫 ব্যানড       : <b>{s['total_banned']}</b>\n"
            f"{'─'*26}\n"
            f"🕐 {datetime.now().strftime('%d %b %Y, %I:%M %p')}",
            cid, mid, reply_markup=m
        )

    # ════════════════════════════════════════
    #  BROADCAST
    # ════════════════════════════════════════
    elif data == "broadcast":
        m = _mk()
        m.row(_btn("📡 সবাইকে", "bc_all"), _btn("🟢 সক্রিয়দের", "bc_active"))
        m.add(_back("main_menu"))
        bot.edit_message_text("📢 <b>ব্রডকাস্ট</b>\nকাদের কাছে পাঠাবেন?", cid, mid, reply_markup=m)

    elif data in ["bc_all","bc_active"]:
        tgt = "all" if data=="bc_all" else "active"
        update_user(cid, {"step": f"wait_broadcast_{tgt}"})
        bot.send_message(cid, "📢 ব্রডকাস্টের মেসেজ/ছবি/ভিডিও পাঠান:")

    # ════════════════════════════════════════
    #  BATCH UPLOAD
    # ════════════════════════════════════════
    elif data == "start_batch":
        bid = str(uuid.uuid4().hex)[:10]
        update_user(cid, {"batch_id": bid, "step": "wait_batch"})
        m = _mk(); m.add(_btn("✅ আপলোড শেষ — Finish", "finish_batch"))
        bot.edit_message_text(
            "📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n\nফাইলগুলো একে একে পাঠান।\nশেষ হলে Finish বাটনে ক্লিক করুন।",
            cid, mid, reply_markup=m
        )

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
        update_user(cid, {"step":"wait_thumbnail","pending_link":dl,"pending_short_link":sl,"batch_id":""})
        bot.edit_message_text(
            f"✅ <b>{cnt}টি ফাইল সেভ হয়েছে!</b>\n\n"
            f"💎 Direct Link:\n<code>{dl}</code>\n\n"
            f"📺 Short Link:\n<code>{sl}</code>\n\n"
            f"🖼️ থাম্বনেইল (ছবি/ভিডিও) পাঠান বা /skip লিখুন।",
            cid, mid, disable_web_page_preview=True
        )

    # ════════════════════════════════════════
    #  SETTINGS (সুন্দর লেআউট)
    # ════════════════════════════════════════
    elif data == "settings":
        update_step(cid, "none")
        m = _mk()
        m.row(
            _btn("📝 পোস্ট সেটিংস",  "menu_post_settings"),
            _btn("📁 ফাইল সেটিংস",   "menu_file_settings")
        )
        m.row(
            _btn("📢 আপডেট চ্যানেল", "menu_channels"),
            _btn("🎥 টিউটোরিয়াল",    "menu_tutorials")
        )
        m.row(
            _btn("📤 অটো পোস্ট",     "menu_auto_post"),
            _btn("🔒 Force Sub",      "menu_force_sub")
        )
        m.add(_btn("⚙️ অ্যাডভান্সড সেটিংস", "menu_advanced"))
        m.add(_back("main_menu"))
        bot.edit_message_text(
            "⚙️ <b>বট সেটিংস</b>\n\nযেকোনো সেটিং পরিবর্তন করতে বাটনে ক্লিক করুন।",
            cid, mid, reply_markup=m
        )

    # ════════════════════════════════════════
    #  📝 পোস্ট সেটিংস
    # ════════════════════════════════════════
    elif data == "menu_post_settings":
        u  = get_user(cid)
        ph = u.get("post_header","") or "—"
        pf = u.get("post_footer","") or "—"
        lf = _ico(u.get("link_filter",0))
        tf = _ico(u.get("text_filter",0))

        m = _mk()
        # Header/Footer এডিট সারি
        m.row(
            _btn("✏️ Header সেট",  "set_post_header"),
            _btn("🗑️ Header মুছুন","del_post_header")
        )
        m.row(
            _btn("✏️ Footer সেট",  "set_post_footer"),
            _btn("🗑️ Footer মুছুন","del_post_footer")
        )
        m.add(_btn("─────────────────────────", "noop"))
        # ফিল্টার
        m.row(
            _btn(f"🔗 লিংক ফিল্টার {lf}",  "toggle_link_filter"),
            _btn(f"📝 টেক্সট ফিল্টার {tf}", "toggle_text_filter")
        )
        m.add(_btn("─────────────────────────", "noop"))
        # পোস্ট বাটন কনফিগ
        m.add(_btn("🔘 পোস্ট বাটন অন/অফ ও কনফিগ", "menu_post_buttons"))
        m.add(_back("settings"))

        bot.edit_message_text(
            f"📝 <b>পোস্ট সেটিংস</b>\n"
            f"{'─'*26}\n"
            f"📌 <b>Header:</b>\n<i>{ph[:80]}</i>\n\n"
            f"📌 <b>Footer:</b>\n<i>{pf[:80]}</i>\n"
            f"{'─'*26}\n"
            f"🔗 লিংক ফিল্টার : {lf}  |  📝 টেক্সট ফিল্টার : {tf}",
            cid, mid, reply_markup=m
        )

    elif data == "del_post_header":
        update_user(cid, {"post_header":""}); bot.answer_callback_query(call.id,"✅ Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_post_settings"; cb(call)

    elif data == "del_post_footer":
        update_user(cid, {"post_footer":""}); bot.answer_callback_query(call.id,"✅ Footer মুছে ফেলা হয়েছে!", show_alert=True)
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

    # ════════════════════════════════════════
    #  🔘 পোস্ট বাটন ON/OFF (নতুন)
    # ════════════════════════════════════════
    elif data == "menu_post_buttons":
        u = get_user(cid)
        bot.edit_message_text(
            f"🔘 <b>পোস্ট বাটন কনফিগারেশন</b>\n"
            f"{'─'*26}\n"
            f"📥 ডাউনলোড বাটন   : {_ico(u.get('btn_download',1))}\n"
            f"🔗 শেয়ার বাটন      : {_ico(u.get('btn_share',1))}\n"
            f"📽️ টিউটোরিয়াল বাটন : {_ico(u.get('btn_tutorial',1))}\n"
            f"📝 ক্যাপশনে লিংক   : {_ico(u.get('btn_link_in_caption',1))}\n"
            f"🔄 লিংক রিপিট      : <b>{u.get('link_repeat_count',1)}x</b>\n"
            f"{'─'*26}\n"
            f"<i>বাটনে ক্লিক করে ON/OFF করুন।</i>",
            cid, mid, reply_markup=_post_btn_menu(u)
        )

    # পোস্ট বাটন টগল
    elif data.startswith("togbtn_"):
        key_map = {
            "togbtn_download":     "btn_download",
            "togbtn_share":        "btn_share",
            "togbtn_tutorial":     "btn_tutorial",
            "togbtn_link_caption": "btn_link_in_caption",
        }
        if data in key_map:
            k   = key_map[data]
            new = 1 - user.get(k, 1)
            update_user(cid, {k: new})
            bot.answer_callback_query(call.id, f"{_ico(new)} অপশন আপডেট হয়েছে!", show_alert=False)
            call.data = "menu_post_buttons"; cb(call)

    # ════════════════════════════════════════
    #  📁 ফাইল সেটিংস
    # ════════════════════════════════════════
    elif data == "menu_file_settings":
        u  = get_user(cid)
        fh = u.get("header","") or "—"
        ff = u.get("footer","") or "—"
        ad = u.get("auto_delete",0)
        al = f"{ad} মিনিট" if ad>0 else "বন্ধ"

        m = _mk()
        m.row(
            _btn("✏️ Header সেট",   "set_file_header"),
            _btn("🗑️ Header মুছুন", "del_file_header")
        )
        m.row(
            _btn("✏️ Footer সেট",   "set_file_footer"),
            _btn("🗑️ Footer মুছুন", "del_file_footer")
        )
        m.add(_btn(f"⏳ Auto-Delete: {al}", "set_autodelete"))
        m.add(_back("settings"))

        bot.edit_message_text(
            f"📁 <b>ফাইল সেটিংস</b>\n"
            f"{'─'*26}\n"
            f"📌 <b>Header:</b>\n<i>{fh[:80]}</i>\n\n"
            f"📌 <b>Footer:</b>\n<i>{ff[:80]}</i>\n"
            f"{'─'*26}\n"
            f"⏳ Auto-Delete: <b>{al}</b>",
            cid, mid, reply_markup=m
        )

    elif data == "del_file_header":
        update_user(cid, {"header":""}); bot.answer_callback_query(call.id,"✅ File Header মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_file_settings"; cb(call)

    elif data == "del_file_footer":
        update_user(cid, {"footer":""}); bot.answer_callback_query(call.id,"✅ File Footer মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_file_settings"; cb(call)

    # ════════════════════════════════════════
    #  🔒 Protect Content
    # ════════════════════════════════════════
    elif data == "toggle_protect_content":
        v = toggle_setting("protect_content")
        msg = "🔒 Protect Content চালু!\nইউজার ফাইল ফরোয়ার্ড/সেভ করতে পারবে না।" if v else "🔓 Protect Content বন্ধ!"
        bot.answer_callback_query(call.id, msg, show_alert=True)
        call.data="menu_advanced"; cb(call)

    # ════════════════════════════════════════
    #  FORCE SUBSCRIBE
    # ════════════════════════════════════════
    elif data == "menu_force_sub":
        fcs = list(force_sub_col.find())
        m   = _mk()
        for fc in fcs:
            m.row(
                _btn(f"{_ico(fc.get('status')=='on')} {fc['name']}", f"tog_fs_{fc['fs_id']}"),
                _btn("🗑️ মুছুন", f"del_fs_{fc['fs_id']}")
            )
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", "add_force_sub"))
        m.add(_back("settings"))
        status = f"চালু — {len(fcs)}টি চ্যানেল 🟢" if fcs else "কোনো চ্যানেল নেই 🔴"
        bot.edit_message_text(
            f"🔒 <b>ফোর্স সাবস্ক্রাইব</b>\n"
            f"{'─'*26}\n"
            f"স্ট্যাটাস: {status}\n\n"
            f"<i>ইউজার এই চ্যানেলে Join না করলে ফাইল পাবে না।</i>",
            cid, mid, reply_markup=m
        )

    elif data == "add_force_sub":
        update_step(cid, "wait_add_force_sub")
        bot.send_message(cid,
            "📢 <b>Force Subscribe চ্যানেল যোগ করুন:</b>\n\n"
            "ফরম্যাট:\n<code>নাম | চ্যানেল_আইডি | লিংক</code>\n\n"
            "উদাহরণ:\n<code>My Channel | -1001234567890 | https://t.me/mychannel</code>"
        )

    elif data.startswith("tog_fs_"):
        fc = force_sub_col.find_one({"fs_id": data[7:]})
        if fc:
            force_sub_col.update_one({"fs_id": data[7:]}, {"$set":{"status":"off" if fc.get("status")=="on" else "on"}})
            call.data="menu_force_sub"; cb(call)

    elif data.startswith("del_fs_"):
        force_sub_col.delete_one({"fs_id": data[7:]})
        bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)
        call.data="menu_force_sub"; cb(call)

    # ════════════════════════════════════════
    #  AUTO POST CHANNELS
    # ════════════════════════════════════════
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
            m.row(
                _btn(f"{_ico(ch.get('status','on')=='on')} {ch.get('name','Unknown')}", f"togch_{ch['ch_id']}"),
                _btn("🗑️", f"delch_{ch['ch_id']}")
            )
        m.add(_btn("➕ নতুন চ্যানেল যোগ করুন", f"add_ch_{ctype}"))
        m.add(_back("menu_auto_post"))
        names = {"ad":"📺 Ad","premium":"💎 Premium","log":"💾 Log"}
        bot.edit_message_text(f"<b>{names.get(ctype)} Channels</b>\nON/OFF করতে নামে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data.startswith("togch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            auto_channels_col.update_one({"ch_id":ch['ch_id']},{"$set":{"status":"off" if ch.get("status","on")=="on" else "on"}})
            call.data=f"list_ch_{ch['type']}"; cb(call)

    elif data.startswith("delch_"):
        ch = auto_channels_col.find_one({"ch_id": data[6:]})
        if ch:
            auto_channels_col.delete_one({"ch_id":ch['ch_id']})
            bot.answer_callback_query(call.id,"✅ মুছে ফেলা হয়েছে!", show_alert=True)
            call.data=f"list_ch_{ch['type']}"; cb(call)

    elif data.startswith("add_ch_"):
        update_step(cid, f"wait_add_{data[7:]}")
        bot.send_message(cid, "📝 ফরম্যাট:\n<code>নাম | চ্যানেল_আইডি</code>")

    # ════════════════════════════════════════
    #  UPDATE CHANNELS & TUTORIALS
    # ════════════════════════════════════════
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

    # ════════════════════════════════════════
    #  CUSTOM BUTTONS
    # ════════════════════════════════════════
    elif data == "menu_custom_buttons":
        btns = user.get("custom_buttons",[])
        m    = _mk()
        for i, b in enumerate(btns):
            m.row(
                _btn(f"{_ico(b.get('status')=='on')} {b['name']}", f"togbtn_cb_{i}"),
                _btn("🗑️", f"delbtn_{i}")
            )
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

    # ════════════════════════════════════════
    #  ADVANCED SETTINGS
    # ════════════════════════════════════════
    elif data == "menu_advanced":
        u  = get_user(cid)
        pc = get_setting("protect_content",0)
        m  = _mk()
        m.add(_btn(f"🔒 Protect Content: {_ico(pc)}","toggle_protect_content"))
        m.add(_btn("─────────────────────────","noop"))
        m.row(_btn("👥 এডমিন ম্যানেজ","manage_admins"), _btn("🚫 ব্যান ম্যানেজ","manage_bans"))
        m.row(_btn("💾 ব্যাকআপ","cmd_backup"), _btn("🔄 রিস্টোর","cmd_restore"))
        m.add(_back("settings"))
        bot.edit_message_text(
            f"⚙️ <b>অ্যাডভান্সড সেটিংস</b>\n"
            f"{'─'*26}\n"
            f"🔒 Protect Content: {_ico(pc)}\n"
            f"<i>{'চালু — ইউজার ফাইল ফরোয়ার্ড/সেভ করতে পারবে না।' if pc else 'বন্ধ — ইউজার ফাইল ফরোয়ার্ড করতে পারবে।'}</i>",
            cid, mid, reply_markup=m
        )

    # ════════════════════════════════════════
    #  ADMIN MANAGEMENT
    # ════════════════════════════════════════
    elif data == "manage_admins":
        all_a = list(admins_col.find()); m = _mk()
        for a in all_a:
            if a['chat_id'] != str(MAIN_ADMIN_ID):
                m.add(_btn(f"👤 {a['chat_id']} [{a.get('role','admin')}]", f"rem_adm_{a['chat_id']}"))
        m.add(_btn("➕ নতুন এডমিন যোগ করুন","add_admin"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(f"👥 <b>এডমিন ম্যানেজমেন্ট</b>\nমোট: {len(all_a)} জন\n\nসরাতে নামে ক্লিক করুন।", cid, mid, reply_markup=m)

    elif data == "add_admin":
        update_step(cid,"wait_add_admin"); bot.send_message(cid,"➕ নতুন এডমিনের Telegram ID দিন:")

    elif data.startswith("rem_adm_"):
        tid = data[8:]
        if tid == str(MAIN_ADMIN_ID):
            bot.answer_callback_query(call.id,"⛔ সুপার এডমিন সরানো যাবে না!", show_alert=True); return
        admins_col.delete_one({"chat_id":tid}); bot.answer_callback_query(call.id,f"✅ {tid} সরানো হয়েছে!", show_alert=True)
        call.data="manage_admins"; cb(call)

    # ════════════════════════════════════════
    #  BAN MANAGEMENT
    # ════════════════════════════════════════
    elif data == "manage_bans":
        bans = list(banned_col.find()); m = _mk()
        for bu in bans[:10]:
            m.add(_btn(f"🚫 {bu['chat_id']}", f"unban_{bu['chat_id']}"))
        m.add(_btn("➕ নতুন ব্যান করুন","add_ban"))
        m.add(_back("menu_advanced"))
        bot.edit_message_text(
            f"🚫 <b>ব্যান ম্যানেজমেন্ট</b>\nমোট: {len(bans)} জন\n\nআনব্যান করতে আইডিতে ক্লিক করুন।",
            cid, mid, reply_markup=m
        )

    elif data == "add_ban":
        update_step(cid,"wait_ban_user"); bot.send_message(cid,"🚫 ব্যান করতে ID দিন (কারণ লিখতে পারেন):\n<code>1234567890 কারণ</code>")

    elif data.startswith("unban_"):
        tid = data[6:]; banned_col.delete_one({"chat_id":tid})
        bot.answer_callback_query(call.id,f"✅ {tid} আনব্যান হয়েছে!", show_alert=True)
        call.data="manage_bans"; cb(call)

    # ════════════════════════════════════════
    #  BACKUP & RESTORE
    # ════════════════════════════════════════
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
                bot.send_document(cid,f,caption=
                    f"✅ <b>ব্যাকআপ সম্পন্ন!</b>\n"
                    f"📅 {datetime.now().strftime('%d %b %Y, %H:%M')}\n"
                    f"👥 ইউজার: {len(bk['users'])} | 📁 ফাইল: {len(bk['files'])}\n"
                    f"🔖 v{BOT_VERSION}")
        except Exception as e: bot.send_message(cid,f"❌ ব্যাকআপ ব্যর্থ:\n<code>{e}</code>")
        finally:
            if os.path.exists("backup.json"): os.remove("backup.json")

    elif data == "cmd_restore":
        update_step(cid,"wait_restore"); bot.send_message(cid,"🔄 <code>backup.json</code> ফাইলটি দিন।")

    # ════════════════════════════════════════
    #  STEP TRIGGER BUTTONS
    # ════════════════════════════════════════
    _step_map = {
        "set_post_header": ("wait_post_header","📝 পোস্টের <b>Header</b> লিখুন:"),
        "set_post_footer": ("wait_post_footer","📝 পোস্টের <b>Footer</b> লিখুন:"),
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

    # ════════════════════════════════════════
    #  HELP
    # ════════════════════════════════════════
    elif data == "help_menu":
        m = _mk(); m.add(_back("main_menu"))
        bot.edit_message_text(
            f"ℹ️ <b>হেল্প — Bot v{BOT_VERSION}</b>\n"
            f"{'─'*26}\n"
            f"📝 <b>পোস্ট সেটিংস:</b> Header/Footer সেট,এডিট,ডিলিট\n"
            f"🔗 <b>লিংক ফিল্টার:</b> লিংক সরিয়ে টেক্সট রাখে\n"
            f"📝 <b>টেক্সট ফিল্টার:</b> পুরো ক্যাপশন সরায়\n"
            f"🔘 <b>পোস্ট বাটন:</b> ডাউনলোড/শেয়ার/টিউটোরিয়াল ON/OFF\n"
            f"🔗 <b>শেয়ার বাটন:</b> contacts/group-এ শেয়ার অপশন\n"
            f"🔒 <b>Protect Content:</b> ফরোয়ার্ড/সেভ বন্ধ\n"
            f"🔒 <b>Force Subscribe:</b> চ্যানেল Join বাধ্যতামূলক\n"
            f"📦 <b>Batch Upload:</b> একাধিক ফাইল একসাথে\n"
            f"⏳ <b>Auto-Delete:</b> নির্দিষ্ট সময়ে ফাইল মুছে\n"
            f"💾 <b>Backup/Restore:</b> ডাটাবেস সুরক্ষিত রাখুন\n"
            f"{'─'*26}\n"
            f"<b>Commands:</b>\n"
            f"/stats | /ban ID কারণ | /unban ID\n/reply ID মেসেজ | /cancel",
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
    adm   = is_admin(cid)

    if is_banned(cid) and not adm:
        try: bot.send_message(cid,"🚫 আপনাকে এই বট ব্যবহার থেকে ব্যান করা হয়েছে।")
        except: pass
        return

    # ── /start ──
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
                    f"╔══════════════════════════╗\n"
                    f"║   🤖 <b>এডমিন প্যানেল</b>   ║\n"
                    f"╚══════════════════════════╝\n\n"
                    f"👥 মোট ইউজার : <b>{s['total_users']}</b>\n"
                    f"📁 মোট ফাইল  : <b>{s['total_files']}</b>\n"
                    f"🟢 আজ সক্রিয় : <b>{s['active_today']}</b>",
                    reply_markup=_main_menu()
                )
            else:
                m = InlineKeyboardMarkup()
                for tut in tutorials_col.find(): m.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
                for ch  in channels_col.find():  m.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))
                bot.send_message(cid,"👋 <b>স্বাগতম!</b>", reply_markup=m if m.keyboard else None)
        return

    # ── /stats ──
    if text=="/stats" and adm:
        s = get_stats()
        bot.send_message(cid,
            f"📊 <b>স্ট্যাটিস্টিক্স</b>\n"
            f"{'─'*24}\n"
            f"👥 ইউজার: <b>{s['total_users']}</b> | 🟢 সক্রিয়: <b>{s['active_today']}</b>\n"
            f"📁 ফাইল: <b>{s['total_files']}</b>\n"
            f"📥 ডাউনলোড: <b>{s['dl_today']}</b> | 📤 আপলোড: <b>{s['ul_today']}</b>"
        ); return

    # ── /ban, /unban, /reply, /cancel ──
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

    if text=="/cancel":
        update_step(cid,"none"); bot.send_message(cid,"❌ বাতিল করা হয়েছে।"); return

    # ── নন-এডমিন ──
    if not adm:
        try:
            bot.forward_message(MAIN_ADMIN_ID, cid, message.message_id)
            bot.send_message(MAIN_ADMIN_ID,f"📩 নতুন মেসেজ\n👤 <code>{cid}</code>\n💬 <code>/reply {cid} মেসেজ</code>")
            bot.send_message(cid,"✅ এডমিনের কাছে পাঠানো হয়েছে।")
        except: pass
        return

    step = user.get("step","none")

    # ── Broadcast ──
    if step.startswith("wait_broadcast"):
        tgt = step.replace("wait_broadcast_","") if "_" in step else "all"
        update_step(cid,"none"); bot.send_message(cid,"⏳ ব্রডকাস্ট background-এ শুরু হচ্ছে...")
        threading.Thread(target=_broadcast_worker,daemon=True,args=(cid,cid,message.message_id,tgt)).start(); return

    # ── Force Sub ──
    if step=="wait_add_force_sub":
        if "|" in text:
            pts=[p.strip() for p in text.split("|")]
            if len(pts)>=3:
                force_sub_col.insert_one({"fs_id":str(uuid.uuid4().hex)[:8],"name":pts[0],"channel_id":pts[1],"url":pts[2],"status":"on"})
                update_step(cid,"none"); bot.send_message(cid,f"✅ Force Subscribe চ্যানেল যোগ: <b>{pts[0]}</b>")
            else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি | লিংক</code>")
        return

    # ── Auto Channels ──
    if step in ["wait_add_ad","wait_add_premium","wait_add_log"]:
        if "|" in text:
            cn,ci=[p.strip() for p in text.split("|",1)]; ct=step.split("_")[2]
            auto_channels_col.insert_one({"ch_id":str(uuid.uuid4().hex)[:8],"type":ct,"name":cn,"channel_id":ci,"status":"on"})
            update_step(cid,"none"); bot.send_message(cid,f"✅ চ্যানেল যোগ: <b>{cn}</b>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | চ্যানেল_আইডি</code>")
        return

    # ── Custom Button ──
    if step=="wait_custom_btn":
        if "|" in text:
            bn,bl=[p.strip() for p in text.split("|",1)]
            btns=user.get("custom_buttons",[]); btns.append({"name":bn,"url":bl,"status":"on"})
            update_user(cid,{"custom_buttons":btns,"step":"none"}); bot.send_message(cid,f"✅ বাটন যোগ: <b>{bn}</b>")
        else: bot.send_message(cid,"⚠️ ফরম্যাট: <code>নাম | লিংক</code>")
        return

    # ── Ban ──
    if step=="wait_ban_user":
        pts=text.strip().split(" ",1); tid=pts[0]; rsn=pts[1] if len(pts)>1 else "এডমিন কর্তৃক ব্যান"
        if tid==str(MAIN_ADMIN_ID): bot.send_message(cid,"⛔ সুপার এডমিন ব্যান করা যাবে না!")
        elif not banned_col.find_one({"chat_id":tid}):
            banned_col.insert_one({"chat_id":tid,"reason":rsn,"banned_at":datetime.now().isoformat()})
            bot.send_message(cid,f"🚫 <code>{tid}</code> ব্যান হয়েছে!")
        else: bot.send_message(cid,"⚠️ আগেই ব্যান।")
        update_step(cid,"none"); return

    # ── Admin Add ──
    if step=="wait_add_admin":
        tid=text.strip()
        if not admins_col.find_one({"chat_id":tid}):
            admins_col.insert_one({"chat_id":tid,"role":"admin","added_at":datetime.now().isoformat()})
            bot.send_message(cid,f"✅ <code>{tid}</code> এডমিন হয়েছে!")
            try: bot.send_message(tid,"🎉 আপনাকে এডমিন করা হয়েছে!")
            except: pass
        else: bot.send_message(cid,"⚠️ এই আইডি আগেই এডমিন।")
        update_step(cid,"none"); return

    # ── Text Settings ──
    ts = {"wait_post_header":("post_header","📝 Post Header"),"wait_post_footer":("post_footer","📝 Post Footer"),
          "wait_file_header":("header","📁 File Header"),"wait_file_footer":("footer","📁 File Footer")}
    if step in ts and message.text:
        k,lbl=ts[step]; update_user(cid,{k:text,"step":"none"}); bot.send_message(cid,f"✅ <b>{lbl}</b> সেট হয়েছে!"); return

    if step=="wait_autodelete" and text.isdigit():
        v=int(text); update_user(cid,{"auto_delete":v,"step":"none"})
        bot.send_message(cid,f"✅ Auto-Delete <b>{v} মিনিট</b>!" if v>0 else "✅ Auto-Delete <b>বন্ধ</b>!"); return

    if step=="wait_link_repeat" and text.isdigit():
        v=max(1,min(int(text),5)); update_user(cid,{"link_repeat_count":v,"step":"none"})
        bot.send_message(cid,f"✅ লিংক রিপিট <b>{v}x</b>!"); return

    if step=="wait_add_channel" and "|" in text:
        n,l=[p.strip() for p in text.split("|",1)]; channels_col.insert_one({"name":n,"url":l})
        update_step(cid,"none"); bot.send_message(cid,f"✅ চ্যানেল যোগ: <b>{n}</b>"); return

    if step=="wait_add_tutorial" and "|" in text:
        n,l=[p.strip() for p in text.split("|",1)]; tutorials_col.insert_one({"name":n,"url":l})
        update_step(cid,"none"); bot.send_message(cid,f"✅ টিউটোরিয়াল যোগ: <b>{n}</b>"); return

    # ── Restore ──
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

    # ── Thumbnail ──
    if step=="wait_thumbnail":
        if text=="/skip":
            update_user(cid,{"step":"none","pending_link":"","pending_short_link":""}); bot.send_message(cid,"✅ স্কিপ করা হয়েছে।"); return
        if message.video:
            update_user(cid,{"temp_media_id":message.video.file_id,"temp_media_type":"video"})
            m=InlineKeyboardMarkup()
            m.row(InlineKeyboardButton("✅ Confirm",callback_data="confirm_vid_thumb"),InlineKeyboardButton("❌ বাতিল",callback_data="cancel_vid_thumb"))
            bot.send_message(cid,"🎥 এই ভিডিওটি পোস্ট করবেন?",reply_markup=m); return
        elif message.photo:
            execute_channel_post(cid,user,"photo",message.photo[-1].file_id); return
        else:
            bot.send_message(cid,"⚠️ ছবি বা ভিডিও দিন অথবা /skip লিখুন।"); return

    # ── File Upload ──
    fid=ftype=None
    if message.document:                           fid,ftype=message.document.file_id,"document"
    elif message.video and step!="wait_thumbnail": fid,ftype=message.video.file_id,"video"
    elif message.audio:                            fid,ftype=message.audio.file_id,"audio"
    elif message.photo and step!="wait_thumbnail": fid,ftype=message.photo[-1].file_id,"photo"

    if fid:
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

        doc={"file_key":uid,"file_id":fid,"type":ftype,"uploader":cid,
             "log_chat_id":lch,"log_msg_id":lmid,"uploaded_at":datetime.now().isoformat()}

        if step=="wait_batch":
            doc["batch_id"]=user.get("batch_id"); files_col.insert_one(doc)
            cnt=files_col.count_documents({"batch_id":user.get("batch_id")})
            m=InlineKeyboardMarkup(); m.add(InlineKeyboardButton("✅ আপলোড শেষ — Finish",callback_data="finish_batch"))
            bot.send_message(cid,f"✅ <b>#{cnt} ফাইল ব্যাচে যোগ হয়েছে!</b>",reply_markup=m)
        else:
            doc["batch_id"]=""; files_col.insert_one(doc)
            dl=f"https://t.me/{BOT_USERNAME}?start={uid}"; sl=get_short_link(dl)
            update_user(cid,{"step":"wait_thumbnail","pending_link":dl,"pending_short_link":sl,"total_uploads":user.get("total_uploads",0)+1})
            _inc_stat("uploads")
            bot.send_message(cid,
                f"✅ <b>ফাইল সেভ হয়েছে!</b>\n\n"
                f"💎 Direct Link:\n<code>{dl}</code>\n\n"
                f"📺 Short Link:\n<code>{sl}</code>\n\n"
                f"🖼️ থাম্বনেইল দিন বা /skip লিখুন।",
                disable_web_page_preview=True
            )

# ══════════════════════════════════════════════════
#  Flask Server
# ══════════════════════════════════════════════════
app = Flask(__name__)

@app.route('/')
def home():
    s = get_stats()
    return {"status":"running","version":BOT_VERSION,"users":s['total_users'],"files":s['total_files']}

@app.route('/health')
def health():
    return {"status":"ok","time":datetime.now().isoformat()}

def _run_server():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',8080)), debug=False)

# ══════════════════════════════════════════════════
#  মেইন
# ══════════════════════════════════════════════════
if __name__ == "__main__":
    logger.info(f"🚀 Premium Bot v{BOT_VERSION} Starting...")
    threading.Thread(target=_run_server, daemon=True).start()
    logger.info("✅ Web server started")
    while True:
        try:
            logger.info("🤖 Polling started...")
            bot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
