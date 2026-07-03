import os
import time
import threading
import requests
import telebot
import logging
from flask import Blueprint, send_from_directory, abort

# ══════════════════════════════════════════════════
#  লগিং
# ══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("webbot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════
#  কনফিগারেশন
# ══════════════════════════════════════════════════
WEBBOT_TOKEN     = os.environ.get("WEBBOT_TOKEN", "আপনার_ওয়েববট_টোকেন")
WEBBOT_USERNAME  = os.environ.get("WEBBOT_USERNAME", "StreamXVideoBot")
FIREBASE_DB_URL  = os.environ.get("FIREBASE_DB_URL", "https://telegram-bot-ca2a6-default-rtdb.firebaseio.com/")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "@yourchannel")
MAIN_ADMIN_ID    = os.environ.get("MAIN_ADMIN_ID", "5991854507")

webbot = telebot.TeleBot(WEBBOT_TOKEN, parse_mode="HTML")
webbot_bp = Blueprint('webbot', __name__)

# ══════════════════════════════════════════════════
#  Flask Web Routes (HTML Serving)
# ══════════════════════════════════════════════════

@webbot_bp.route('/')
@webbot_bp.route('/index.html')
def index():
    """ভিডিও পোর্টাল ওয়েব পেজ সার্ভ করে"""
    import os
    for p in ['Web bot/video.html', 'video.html', '../video.html']:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                headers = {
                    'Content-Type': 'text/html; charset=utf-8',
                    'Cache-Control': 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
                return f.read(), 200, headers
    return '<h2>video.html ফাইলটি পাওয়া যায়নি।</h2>', 404

@webbot_bp.route('/admin')
@webbot_bp.route('/admin.html')
def admin():
    """ভিডিও পোর্টাল এডমিন পেজ সার্ভ করে"""
    import os
    for p in ['Web bot/video_admin.html', 'video_admin.html', '../video_admin.html']:
        if os.path.exists(p):
            with open(p, encoding='utf-8') as f:
                headers = {
                    'Content-Type': 'text/html; charset=utf-8',
                    'Cache-Control': 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0',
                    'Pragma': 'no-cache',
                    'Expires': '0'
                }
                return f.read(), 200, headers
    return '<h2>video_admin.html ফাইলটি পাওয়া যায়নি।</h2>', 404

# ══════════════════════════════════════════════════
#  টেলিগ্রাম বট কমান্ড হ্যান্ডলার
# ══════════════════════════════════════════════════

@webbot.message_handler(commands=['start'])
def handle_start(message):
    cid = str(message.chat.id)
    text = message.text or ""
    pts = text.split(" ")
    
    markup = telebot.types.InlineKeyboardMarkup()
    
    if len(pts) > 1:
        # স্টার্টাপ প্যারামিটার আছে (ভিডিও আইডি)
        video_id = pts[1]
        web_app_url = f"https://t.me/{WEBBOT_USERNAME}/app?startapp={video_id}"
        markup.add(telebot.types.InlineKeyboardButton("🎬 Watch Video Portal", url=web_app_url))
        
        webbot.send_message(
            cid,
            "👋 <b>StreamX প্রিমিয়াম ভিডিও পোর্টালে স্বাগতম!</b>\n\n"
            "আপনার কাঙ্ক্ষিত ভিডিওটি দেখতে নিচের বোতামে ক্লিক করুন।",
            reply_markup=markup
        )
    else:
        # সাধারণ স্টার্ট
        web_app_url = f"https://t.me/{WEBBOT_USERNAME}/app"
        markup.add(telebot.types.InlineKeyboardButton("🎬 Open Video Portal", url=web_app_url))
        
        webbot.send_message(
            cid,
            "👋 <b>StreamX প্রিমিয়াম ভিডিও পোর্টালে স্বাগতম!</b>\n\n"
            "আমাদের ভিডিও পোর্টাল ওপেন করতে নিচের বোতামে ক্লিক করুন এবং নতুন নতুন ভিডিও ও কালেকশন উপভোগ করুন।",
            reply_markup=markup
        )

@webbot.message_handler(commands=['stats'])
def handle_stats(message):
    cid = str(message.chat.id)
    # চেক করুন যে রিকোয়েস্টকারী এডমিন কি না
    if cid != str(MAIN_ADMIN_ID):
        webbot.send_message(cid, "⛔ এডমিন অ্যাক্সেস প্রয়োজন!")
        return
        
    webbot.send_message(cid, "⏳ ফায়ারবেস থেকে লাইভ স্ট্যাটস সংগ্রহ করা হচ্ছে...")
    
    try:
        # fetch videos
        r_vids = requests.get(f"{FIREBASE_DB_URL.rstrip('/')}/videos.json", timeout=10).json()
        vids_count = len(r_vids) if r_vids else 0
        views_count = sum(v.get('views', 0) for v in r_vids.values()) if r_vids else 0
        likes_count = sum(v.get('likes', 0) for v in r_vids.values()) if r_vids else 0
        
        # fetch online users
        r_users = requests.get(f"{FIREBASE_DB_URL.rstrip('/')}/online_users.json", timeout=10).json()
        users_count = len(r_users) if r_users else 0
        
        msg = (
            f"📊 <b>Web Portal স্ট্যাটিস্টিক্স</b>\n"
            f"{'─'*26}\n"
            f"👥 লাইভ ইউজার অনলাইনে : <b>{users_count}</b> জন\n"
            f"🎬 মোট ভিডিও আপলোড : <b>{vids_count}</b> টি\n"
            f"👁️ মোট ভিডিও ভিউস : <b>{views_count}</b> টি\n"
            f"👍 মোট লাইক সংখ্যা : <b>{likes_count}</b> টি\n"
            f"{'─'*26}\n"
            f"🕐 লাইভ রিয়েলটাইম ফায়ারবেস ডেটা"
        )
        webbot.send_message(cid, msg)
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        webbot.send_message(cid, f"❌ ডেটা লোড করতে সমস্যা হয়েছে।\n<code>{e}</code>")

# ══════════════════════════════════════════════════
#  চ্যানেল অটো-পোস্ট ওয়ার্কার (Firebase Polling)
# ══════════════════════════════════════════════════

def auto_post_worker():
    logger.info("📡 Auto-post worker thread started...")
    
    if not TELEGRAM_CHANNEL_ID or not FIREBASE_DB_URL:
        logger.warning("⚠️ Channel ID or Firebase URL not configured. Auto-post disabled.")
        return
        
    while True:
        try:
            # প্রতি ১৫ সেকেন্ড পর পর ডাটাবেজ চেক করবে
            time.sleep(15)
            
            clean_db_url = FIREBASE_DB_URL.rstrip('/')
            r = requests.get(f"{clean_db_url}/videos.json", timeout=10)
            if r.status_code != 200:
                continue
                
            videos = r.json() or {}
            
            for vid_id, v in videos.items():
                # যদি ভিডিওটি এখনো চ্যানেলে পোস্ট করা না হয়ে থাকে
                if not v.get("posted"):
                    title = v.get("title", "No Title")
                    category = v.get("category", "Others")
                    thumb = v.get("thumb", "")
                    
                    # চ্যানেলে পাঠানোর মেসেজ ফরম্যাট
                    caption = (
                        f"🎬 <b>{title}</b>\n\n"
                        f"📂 Category: <b>{category}</b>\n"
                        f"👁️ Views: 0\n\n"
                        f"👉 Watch now inside Telegram! 👇"
                    )
                    
                    # টেলিগ্রাম মিনি অ্যাপ ওপেন লিংক
                    web_app_url = f"https://t.me/{WEBBOT_USERNAME}/app?startapp={vid_id}"
                    
                    markup = telebot.types.InlineKeyboardMarkup()
                    markup.add(telebot.types.InlineKeyboardButton("🎬 Watch Video", url=web_app_url))
                    
                    msg = None
                    try:
                        if thumb and (thumb.startswith("http://") or thumb.startswith("https://")):
                            msg = webbot.send_photo(TELEGRAM_CHANNEL_ID, thumb, caption=caption, reply_markup=markup)
                        else:
                            msg = webbot.send_message(TELEGRAM_CHANNEL_ID, caption, reply_markup=markup)
                    except Exception as pe:
                        logger.error(f"Send photo failed, attempting text fallback: {pe}")
                        try:
                            msg = webbot.send_message(TELEGRAM_CHANNEL_ID, caption, reply_markup=markup)
                        except Exception as pe2:
                            logger.error(f"Fallback message post failed: {pe2}")
                            
                    if msg:
                        # Firebase-এ স্ট্যাটাস পোস্ট আপডেট করা
                        patch_url = f"{clean_db_url}/videos/{vid_id}.json"
                        patch_data = {"posted": True, "channel_message_id": msg.message_id}
                        requests.patch(patch_url, json=patch_data, timeout=10)
                        logger.info(f"✅ Video '{title}' ({vid_id}) successfully auto-posted to channel.")
                        
        except Exception as e:
            logger.error(f"Error in auto post worker: {e}")

# ══════════════════════════════════════════════════
#  বট রানার ফাংশন
# ══════════════════════════════════════════════════

def run_bot():
    logger.info("🚀 Web Bot Polling started...")
    # অটো-পোস্টার থ্রেড চালু করুন
    threading.Thread(target=auto_post_worker, daemon=True).start()
    
    while True:
        try:
            webbot.polling(none_stop=True, timeout=60, long_polling_timeout=60)
        except Exception as e:
            logger.error(f"Web Bot Polling error: {e}")
            time.sleep(5)
