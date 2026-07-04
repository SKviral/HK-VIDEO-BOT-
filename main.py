import os
import sys
import time
import threading
import logging
import requests
import importlib.util
from flask import Flask, jsonify

# ══════════════════════════════════════════════════
#  লগিং সেটিংস
# ══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler("main.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

logger.info("🎬 Initializing Unified Server for Shortener Bot & Web Bot...")

# ══════════════════════════════════════════════════
#  ডাইনামিক মডিউল ইমপোর্ট (ফোল্ডার নেমে স্পেস থাকার কারণে)
# ══════════════════════════════════════════════════

def load_module_from_path(module_name, file_paths):
    """স্পেসযুক্ত ডিরেক্টরি থেকে পাইথন ফাইল ইমপোর্ট করার জন্য (মাল্টিপল পাথ সাপোর্টসহ)"""
    import os
    if isinstance(file_paths, str):
        file_paths = [file_paths]
        
    selected_path = None
    for p in file_paths:
        if os.path.exists(p):
            selected_path = p
            break
            
    if not selected_path:
        logger.error(f"❌ Failed to find module {module_name} in any of these paths: {file_paths}")
        sys.exit(1)
        
    try:
        spec = importlib.util.spec_from_file_location(module_name, selected_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        logger.info(f"✅ Successfully loaded module: {module_name} from {selected_path}")
        return module
    except Exception as e:
        logger.error(f"❌ Failed to load module {module_name} from {selected_path}: {e}")
        sys.exit(1)

# Shortener Bot, Web Bot এবং Approve Bot মডিউল লোড করা
shortenerbot = load_module_from_path("shortenerbot", ["Shortener bot/shortenerbot.py", "shortener bot/shortenerbot.py"])
webbot = load_module_from_path("webbot", ["Web bot/webbot.py", "web bot/webbot.py"])
approvebot = load_module_from_path("approvebot", [
    "APPROVE/telegram_bot.py",
    "approve/telegram_bot.py",
    "Approve/telegram_bot.py",
    "APPROVE BOT/telegram_bot.py",
    "APPROVE BOT/bot.py",
    "APPROVE/bot.py",
    "approve/bot.py",
    "Approve/bot.py"
])

# ══════════════════════════════════════════════════
#  Flask অ্যাপ কনফিগারেশন এবং ব্লুপ্রিন্ট রেজিস্ট্রেশন
# ══════════════════════════════════════════════════

app = Flask(__name__)

# দুটি বটের Flask রাউটগুলোকে একটি সিঙ্গেল অ্যাপে রেজিস্টার করা
app.register_blueprint(shortenerbot.shortener_bp)
app.register_blueprint(webbot.webbot_bp)

# রুট লেভেল হেলথ চেক রাউট
@app.route('/global_health', methods=['GET'])
def global_health():
    return jsonify({
        "status": "healthy",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bots": {
            "shortener_bot": "running",
            "web_bot": "running",
            "approve_bot": "running"
        }
    })

# ══════════════════════════════════════════════════
#  রেন্ডার কিপ-অ্যালাইভ (Render Keep-Alive)
# ══════════════════════════════════════════════════

PORT = int(os.environ.get('PORT', 8080))

def keep_alive_worker():
    """রেন্ডার ফ্রি হোস্টিং স্লিপিং এড়াতে প্রতি ১০ মিনিটে সেলফ-পিং করবে"""
    time.sleep(20) # সার্ভার বুট হওয়ার জন্য অপেক্ষা
    
    # রেন্ডার স্বয়ংক্রিয়ভাবে RENDER_EXTERNAL_URL ভেরিয়েবল প্রদান করে
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    ping_url = f"{external_url}/global_health" if external_url else f"http://localhost:{PORT}/global_health"
    
    logger.info(f"🔄 Keep-Alive ping worker active. Target: {ping_url}")
    
    while True:
        try:
            # ১০ মিনিট (৬০০ সেকেন্ড) পর পর পিং করবে
            r = requests.get(ping_url, timeout=10)
            if r.status_code == 200:
                logger.info("💓 Self keep-alive ping sent successfully.")
            else:
                logger.warning(f"⚠️ Self-ping status code: {r.status_code}")
        except Exception as e:
            logger.warning(f"⚠️ Self keep-alive ping failed: {e}")
        time.sleep(600)

# ══════════════════════════════════════════════════
#  মেন রানার
# ══════════════════════════════════════════════════

if __name__ == "__main__":
    logger.info("🤖 Starting Telegram Bot polling threads...")
    
    # ১. শর্টনার বটের পোলিং থ্রেড চালু করা
    shortener_thread = threading.Thread(target=shortenerbot.run_bot, daemon=True)
    shortener_thread.start()
    logger.info("👉 Shortener Bot thread started.")
    
    # ২. ওয়েব বটের পোলিং এবং অটো-পোস্টার থ্রেড চালু করা
    web_thread = threading.Thread(target=webbot.run_bot, daemon=True)
    web_thread.start()
    logger.info("👉 Web Bot thread started.")
    
    # ৩. অ্যাপ্রুভ বটের পোলিং থ্রেড চালু করা
    approve_thread = threading.Thread(target=approvebot.run_bot, daemon=True)
    approve_thread.start()
    logger.info("👉 Approve Bot thread started.")
    
    # ৪. সেলফ কিপ-অ্যালাইভ থ্রেড চালু করা
    threading.Thread(target=keep_alive_worker, daemon=True).start()
    
    # ৫. Flask ওয়েব সার্ভার চালু করা ( Render এর রিকোয়েস্ট এক্সেপ্ট করার জন্য)
    logger.info(f"🚀 Starting Unified Flask server on port {PORT}...")
    app.run(host='0.0.0.0', port=PORT, debug=False)
