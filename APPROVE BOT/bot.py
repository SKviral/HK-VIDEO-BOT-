import os
import sqlite3
import requests
import logging
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters

# ১. এনভায়রনমেন্ট ও লগিং সেটআপ
load_dotenv()
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ২. Gemini AI কনফিগারেশন
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')

DB_PATH = os.path.join(os.path.dirname(__file__), 'cine_scout.db')

# ৩. ডাটাবেজ ফাংশন (SQLite)
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_settings 
                      (user_id INTEGER PRIMARY KEY, language TEXT DEFAULT 'English')''')
    conn.commit()
    conn.close()

def save_language(user_id, lang):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO user_settings (user_id, language) VALUES (?, ?)", (user_id, lang))
    conn.commit()
    conn.close()

def get_user_language(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT language FROM user_settings WHERE user_id=?", (user_id,))
    res = cursor.fetchone()
    conn.close()
    return res[0] if res else "English"

# ৪. /start কমান্ড হ্যান্ডলার
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user_name = update.effective_user.first_name
    keyboard = [
        [InlineKeyboardButton("বাংলা 🇧🇩", callback_data='lang_Bangla'),
         InlineKeyboardButton("English 🇺🇸", callback_data='lang_English')],
        [InlineKeyboardButton("Hindi 🇮🇳", callback_data='lang_Hindi')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"হ্যালো {user_name}! আমি আপনার CineScout AI অ্যাসিস্ট্যান্ট।\nআপনার পছন্দের ভাষা বেছে নিন:",
        reply_markup=reply_markup
    )

# ৫. ভাষা পছন্দের বাটন হ্যান্ডলার
async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lang = query.data.split('_')[1]
    save_language(query.from_user.id, lang)
    await query.edit_message_text(f"আপনার ভাষা সেট করা হয়েছে: *{lang}*\nএখন যেকোনো মুভির নাম লিখুন বা আমাকে মুভি সাজেস্ট করতে বলুন।")

# ৬. AI মেসেজ হ্যান্ডলার
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_msg = update.message.text
    chat_id = update.effective_chat.id
    
    # ইউজার কী ভাষায় উত্তর চায় তা দেখা
    lang = get_user_language(chat_id)

    # Gemini AI প্রোম্পট (এখানে TMDB এর ডেটা দরকার হলে AI নিজেই ওয়েব থেকে তথ্য নিবে)
    prompt = (
        f"You are a professional movie assistant. "
        f"The user is asking: '{user_msg}'. "
        f"Please reply in {lang} language. "
        f"If they want recommendations, suggest 3-5 latest or best movies with a short description and rating."
    )

    try:
        # টাইপিং স্ট্যাটাস দেখানো
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        response = model.generate_content(prompt)
        ai_reply = response.text
        
        if not ai_reply:
            ai_reply = "দুঃখিত, আমি কিছু খুঁজে পাইনি।"
            
        await update.message.reply_text(ai_reply, parse_mode=None)
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("সার্ভারে একটু সমস্যা হচ্ছে, দয়া করে আবার চেষ্টা করুন।")

# ৭. মেইন রানার ফাংশন (থ্রেডিং সাপোর্ট সহ)
def run_bot():
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    init_db()
    
    token = os.getenv('TELEGRAM_TOKEN') or TELEGRAM_TOKEN
    if not token:
        print("Error: TELEGRAM_TOKEN environment variable not set for APPROVE BOT.")
        return
        
    application = ApplicationBuilder().token(token)\
        .connect_timeout(30)\
        .read_timeout(30)\
        .write_timeout(30)\
        .pool_timeout(30)\
        .build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(set_language, pattern='^lang_'))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    
    print("CineScout AI ইজ রানিং...")
    application.run_polling(poll_interval=1.0, timeout=30, close_loop=False)

if __name__ == '__main__':
    run_bot()
