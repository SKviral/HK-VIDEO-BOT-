import os
import telebot
from pymongo import MongoClient
from flask import Flask
from threading import Thread

# Render এর Environment Variable থেকে টোকেন এবং URL নেওয়া (কোডে সরাসরি URL দেবেন না)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGO_URL = os.environ.get("MONGO_URL")

bot = telebot.TeleBot(BOT_TOKEN)

# MongoDB কানেকশন
client = MongoClient(MONGO_URL)
db = client['bot_database']
users_collection = db['users']

# /start কমান্ড হ্যান্ডলার
@bot.message_handler(commands=['start'])
def send_welcome(message):
    chat_id = message.chat.id
    # ডাটাবেসে ইউজার সেভ করা
    users_collection.update_one(
        {"chat_id": chat_id}, 
        {"$set": {"chat_id": chat_id}}, 
        upsert=True
    )
    bot.reply_to(message, "স্বাগতম! আপনার ডাটা MongoDB তে সেভ হয়েছে।")

# ---- Render-এর জন্য ডামি Flask সার্ভার (যাতে বট অফ না হয়ে যায়) ----
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running perfectly!"

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    # Flask সার্ভার আলাদা থ্রেডে চালানো
    server_thread = Thread(target=run_server)
    server_thread.start()
    
    # বট পোলিং চালু করা
    print("Bot Started...")
    bot.infinity_polling()
