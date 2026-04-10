import os
import time
import json
import uuid
import threading
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from pymongo import MongoClient
from flask import Flask
from urllib.parse import quote

# ================= কনফিগারেশন (Environment Variables) =================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "আপনার_বট_টোকেন_এখানে_দিন")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "Shohag_download_test_bot")
MAIN_ADMIN_ID = os.environ.get("MAIN_ADMIN_ID", "5991854507")
TERABOX_TOKEN = os.environ.get("TERABOX_TOKEN", "71b16be6b48d01937bfe7d2c3043cbc0b6363c82")
MONGO_URL = os.environ.get("MONGO_URL", "আপনার_MongoDB_URL_এখানে_দিন")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ================= MongoDB ডাটাবেস সেটআপ =================
client = MongoClient(MONGO_URL)
db = client['telegram_bot_db']

users_col = db['users']
files_col = db['files']
queue_col = db['queue']
admins_col = db['admins']
channels_col = db['update_channels']
tutorials_col = db['tutorials']
auto_channels_col = db['auto_channels']

# মূল এডমিন ডাটাবেসে যুক্ত করা
if not admins_col.find_one({"chat_id": str(MAIN_ADMIN_ID)}):
    admins_col.insert_one({"chat_id": str(MAIN_ADMIN_ID)})

# ================= ডাটাবেস হেল্পার ফাংশন =================
def get_user(chat_id):
    chat_id = str(chat_id)
    user = users_col.find_one({"chat_id": chat_id})
    if not user:
        user = {
            "chat_id": chat_id, "header": "", "footer": "", "post_header": "", "post_footer": "",
            "auto_delete": 0, "pending_link": "", "pending_short_link": "", "step": "none",
            "batch_id": "", "post_link_toggle": 1, "post_tutorial_toggle": 1, "link_repeat_count": 1,
            "custom_buttons":[], "temp_media_id": "", "temp_media_type": ""
        }
        users_col.insert_one(user)
    
    # পুরনো ডাটাবেসের সাথে নতুন ফিচার অ্যাডজাস্ট করা
    updates = {}
    if "custom_buttons" not in user: updates["custom_buttons"] =[]
    if "temp_media_id" not in user: updates["temp_media_id"] = ""
    if "temp_media_type" not in user: updates["temp_media_type"] = ""
    
    if updates:
        users_col.update_one({"chat_id": chat_id}, {"$set": updates})
        user.update(updates)
        
    return user

def update_user(chat_id, updates):
    users_col.update_one({"chat_id": str(chat_id)}, {"$set": updates})

def update_step(chat_id, step):
    update_user(chat_id, {"step": step})

def is_admin(chat_id):
    return bool(admins_col.find_one({"chat_id": str(chat_id)}))

# ================= অটো-ডিলিট সিস্টেম =================
def auto_delete_worker():
    while True:
        try:
            now = int(time.time())
            expired_items = list(queue_col.find({"delete_at": {"$lte": now}}))
            if expired_items:
                ch_list = list(channels_col.find())
                markup = InlineKeyboardMarkup()
                for ch in ch_list:
                    markup.add(InlineKeyboardButton(text=f"📢 {ch['name']}", url=ch['url']))
                
                for item in expired_items:
                    try:
                        bot.delete_message(item['chat_id'], item['message_id'])
                        bot.send_message(
                            item['chat_id'], 
                            "⚠️ <b>সময় শেষ!</b>\nআপনার ফাইলটি ডিলিট করা হয়েছে। আরও সব ফাইল অথবা ভিডিও পেতে আমাদের আপডেট চ্যানেলে জয়েন করুন।",
                            reply_markup=markup if ch_list else None
                        )
                    except: pass
                    queue_col.delete_one({"_id": item["_id"]})
        except Exception as e: print("Auto delete error:", e)
        time.sleep(10)

threading.Thread(target=auto_delete_worker, daemon=True).start()

# ================= Terabox Link Shortener =================
def get_terabox_short_link(long_url):
    try:
        api_url = f"https://teraboxlinks.com/api?api={TERABOX_TOKEN}&url={quote(long_url)}"
        res = requests.get(api_url, timeout=5).json()
        if res and res.get("status") != "error": return res.get("shortenedUrl")
    except: pass
    return long_url

# ================= চ্যানেলে পোস্ট করার মেইন ফাংশন =================
def execute_channel_post(chat_id, user, media_type, media_id):
    d_link = user.get("pending_link", "")
    s_link = user.get("pending_short_link", "")
    
    p_head = f"{user.get('post_header', '')}\n\n" if user.get('post_header') else ""
    p_foot = f"\n\n{user.get('post_footer', '')}" if user.get('post_footer') else ""
    
    ad_caption = prem_caption = f"{p_head}{p_foot}"
    ad_markup, prem_markup = InlineKeyboardMarkup(), InlineKeyboardMarkup()
    
    if user.get("post_link_toggle", 1) == 1:
        repeats = max(1, user.get("link_repeat_count", 1))
        
        # লিংক রিপিট ফিক্স (শুধু লিংকগুলো রিপিট হবে)
        ad_links_text = "\n".join([s_link] * repeats)
        prem_links_text = "\n".join([d_link] * repeats)
        
        ad_text = f"🔗 <b>Download Link:</b>\n{ad_links_text}"
        prem_text = f"🔗 <b>Download Link (No Ads):</b>\n{prem_links_text}"
        
        ad_caption = f"{p_head}{ad_text}{p_foot}"
        prem_caption = f"{p_head}{prem_text}{p_foot}"
        
        if user.get("post_tutorial_toggle", 1) == 1:
            for tut in tutorials_col.find(): 
                ad_markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
        
        # কাস্টম বাটন অ্যাড করা
        custom_buttons = user.get("custom_buttons",[])
        for btn in custom_buttons:
            if btn.get("status") == "on":
                ad_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))
                prem_markup.add(InlineKeyboardButton(btn['name'], url=btn['url']))

        ad_markup.add(InlineKeyboardButton("📥 ফাইলটি ডাউনলোড করুন", url=s_link))
        prem_markup.add(InlineKeyboardButton("💎 ডাইরেক্ট ডাউনলোড করুন", url=d_link))

    # Ad চ্যানেলে পোস্ট
    for ch in auto_channels_col.find({"type": "ad"}):
        if ch.get("status", "on") == "on":
            try:
                if media_type == 'photo': bot.send_photo(ch['channel_id'], media_id, caption=ad_caption, reply_markup=ad_markup)
                elif media_type == 'video': bot.send_video(ch['channel_id'], media_id, caption=ad_caption, reply_markup=ad_markup)
            except: pass
    
    # Premium চ্যানেলে পোস্ট
    for ch in auto_channels_col.find({"type": "premium"}):
        if ch.get("status", "on") == "on":
            try:
                if media_type == 'photo': bot.send_photo(ch['channel_id'], media_id, caption=prem_caption, reply_markup=prem_markup)
                elif media_type == 'video': bot.send_video(ch['channel_id'], media_id, caption=prem_caption, reply_markup=prem_markup)
            except: pass
    
    # Log চ্যানেলে ব্যাকআপ
    for ch in auto_channels_col.find({"type": "log"}):
        if ch.get("status", "on") == "on":
            try:
                if media_type == 'photo': bot.send_photo(ch['channel_id'], media_id, caption="🖼️ <b>Media Backup</b>")
                elif media_type == 'video': bot.send_video(ch['channel_id'], media_id, caption="🎥 <b>Media Backup</b>")
            except: pass

    bot.send_message(chat_id, "✅ <b>অসাধারণ!</b> থাম্বনেইল ও ফাইলগুলো চালু থাকা চ্যানেলগুলোতে পোস্ট করা হয়েছে।")
    update_user(chat_id, {"step": "none", "pending_link": "", "pending_short_link": "", "temp_media_id": "", "temp_media_type": ""})

# ================= কলব্যাক কোয়েরি =================
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = str(call.message.chat.id)
    msg_id = call.message.message_id
    data = call.data
    user = get_user(chat_id)
    
    if not is_admin(chat_id): return

    # Video Thumbnail Confirm / Cancel
    if data == "confirm_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        execute_channel_post(chat_id, user, user.get("temp_media_type"), user.get("temp_media_id"))
        return
    elif data == "cancel_vid_thumb":
        bot.delete_message(chat_id, msg_id)
        update_user(chat_id, {"step": "wait_thumbnail", "temp_media_id": "", "temp_media_type": ""})
        bot.send_message(chat_id, "❌ বাতিল করা হয়েছে। দয়া করে নতুন থাম্বনেইল (ছবি/ভিডিও) দিন।")
        return

    # Main Menu
    if data == "main_menu":
        update_step(chat_id, "none")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📦 ব্যাচ আপলোড", callback_data="start_batch"))
        markup.add(InlineKeyboardButton("⚙️ সেটিংস (Settings)", callback_data="settings"))
        markup.add(InlineKeyboardButton("📢 ব্রডকাস্ট", callback_data="broadcast"), InlineKeyboardButton("ℹ️ হেল্প", callback_data="help_menu"))
        bot.edit_message_text("👋 <b>এডমিন প্যানেলে স্বাগতম!</b>\n\nসিঙ্গেল ফাইল আপলোড করতে সরাসরি ফাইল দিন।\nএকাধিক ফাইলের জন্য <b>'📦 ব্যাচ আপলোড'</b> ব্যবহার করুন।", chat_id, msg_id, reply_markup=markup)
        
    elif data == "broadcast":
        update_step(chat_id, "wait_broadcast")
        bot.send_message(chat_id, "📢 ব্রডকাস্টের জন্য যেকোনো টেক্সট, ছবি বা ফাইল দিন।")

    elif data == "start_batch":
        batch_id = str(uuid.uuid4().hex)[:10]
        update_user(chat_id, {"batch_id": batch_id, "step": "wait_batch"})
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)", callback_data="finish_batch"))
        bot.edit_message_text("📦 <b>ব্যাচ আপলোড শুরু হয়েছে!</b>\n\nএকটি একটি করে আপনার ফাইলগুলো দিন।", chat_id, msg_id, reply_markup=markup)
        
    elif data == "finish_batch":
        batch_id = user.get("batch_id")
        if not batch_id:
            bot.answer_callback_query(call.id, "⚠️ ব্যাচ আপলোড আগেই শেষ হয়েছে!", show_alert=True)
            return
        bot.edit_message_text("⏳ <b>লিংক তৈরি হচ্ছে... অপেক্ষা করুন।</b>", chat_id, msg_id)
        bot_deep_link = f"https://t.me/{BOT_USERNAME}?start={batch_id}"
        short_link = get_terabox_short_link(bot_deep_link)
        update_user(chat_id, {"step": "wait_thumbnail", "pending_link": bot_deep_link, "pending_short_link": short_link, "batch_id": ""})
        reply = f"✅ <b>সব ফাইল সেভ হয়েছে!</b>\n💎 ডাইরেক্ট লিংক: {bot_deep_link}\n📺 শর্ট লিংক: {short_link}\n\n🖼️ <b>চ্যানেলে পোস্ট করার জন্য একটি থাম্বনেইল (ছবি/ভিডিও) দিন।</b>\n<i>(স্কিপ করতে /skip লিখুন)</i>"
        bot.edit_message_text(reply, chat_id, msg_id, disable_web_page_preview=True)

    elif data == "settings":
        update_step(chat_id, "none")
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("📝 Header / Footer সেটআপ", callback_data="menu_texts"))
        markup.add(InlineKeyboardButton("🔗 আপডেট চ্যানেল ম্যানেজ", callback_data="menu_channels"))
        markup.add(InlineKeyboardButton("🎥 টিউটোরিয়াল ম্যানেজ", callback_data="menu_tutorials"))
        markup.add(InlineKeyboardButton("📤 অটো পোস্ট চ্যানেল ম্যানেজ", callback_data="menu_auto_post"))
        markup.add(InlineKeyboardButton("⚙️ অ্যাডভান্সড সেটিংস", callback_data="menu_advanced"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu"))
        bot.edit_message_text("⚙️ <b>বট সেটিংস:</b>", chat_id, msg_id, reply_markup=markup)

    # --- Auto Post Channels Management (New Version) ---
    elif data == "menu_auto_post":
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(f"📺 Ad Channel ({auto_channels_col.count_documents({'type':'ad'})})", callback_data="list_ch_ad"))
        markup.add(InlineKeyboardButton(f"💎 Premium Channel ({auto_channels_col.count_documents({'type':'premium'})})", callback_data="list_ch_premium"))
        markup.add(InlineKeyboardButton(f"💾 Log Channel ({auto_channels_col.count_documents({'type':'log'})})", callback_data="list_ch_log"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="settings"))
        bot.edit_message_text("📤 <b>অটো পোস্ট চ্যানেল ম্যানেজমেন্ট:</b>\nম্যানেজ করতে নিচের ক্যাটাগরিতে ক্লিক করুন:", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("list_ch_"):
        c_type = data.split("_")[2]
        channels = list(auto_channels_col.find({"type": c_type}))
        markup = InlineKeyboardMarkup()
        
        for ch in channels:
            # পুরনো ডাটা ফিক্সিং (যাতে আপনার আগের সেভ করা চ্যানেলগুলোও শো করে)
            ch_id = ch.get("ch_id")
            if not ch_id:
                ch_id = str(uuid.uuid4().hex)[:8]
                auto_channels_col.update_one({"_id": ch["_id"]}, {"$set": {"ch_id": ch_id, "name": ch.get("channel_id"), "status": "on"}})
                ch["ch_id"], ch["name"], ch["status"] = ch_id, ch.get("channel_id"), "on"

            status_ico = "🟢 ON" if ch.get("status", "on") == "on" else "🔴 OFF"
            name = ch.get("name", ch.get("channel_id"))
            
            # Toggle and Delete Buttons
            markup.row(InlineKeyboardButton(f"{name} [{status_ico}]", callback_data=f"togch_{ch['ch_id']}"),
                       InlineKeyboardButton("🗑️ Delete", callback_data=f"delch_{ch['ch_id']}"))
        
        markup.add(InlineKeyboardButton("➕ নতুন চ্যানেল যোগ করুন", callback_data=f"add_ch_{c_type}"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_auto_post"))
        type_name = "Ad" if c_type == "ad" else "Premium" if c_type == "premium" else "Log"
        bot.edit_message_text(f"📋 <b>{type_name} Channels List:</b>\nON/OFF করতে নামের উপর ক্লিক করুন।", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("togch_"):
        ch_id = data.split("_")[1]
        ch = auto_channels_col.find_one({"ch_id": ch_id})
        if ch:
            new_st = "off" if ch.get("status", "on") == "on" else "on"
            auto_channels_col.update_one({"ch_id": ch_id}, {"$set": {"status": new_st}})
            call.data = f"list_ch_{ch['type']}"
            callback_handler(call)

    elif data.startswith("delch_"):
        ch_id = data.split("_")[1]
        ch = auto_channels_col.find_one({"ch_id": ch_id})
        if ch:
            auto_channels_col.delete_one({"ch_id": ch_id})
            bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)
            call.data = f"list_ch_{ch['type']}"
            callback_handler(call)

    elif data.startswith("add_ch_"):
        c_type = data.split("_")[2]
        update_step(chat_id, f"wait_add_{c_type}")
        bot.send_message(chat_id, "📝 <b>চ্যানেল যোগ করুন:</b>\nচ্যানেলের নাম এবং ID (বা ইউজারনেম) `|` দিয়ে আলাদা করে দিন।\nউদাহরণ: `Primary Channel | -10012345678`", parse_mode="Markdown")

    # --- Custom Buttons Management ---
    elif data == "menu_custom_buttons":
        btns = user.get("custom_buttons",[])
        markup = InlineKeyboardMarkup()
        for i, btn in enumerate(btns):
            status_ico = "🟢 ON" if btn.get("status") == "on" else "🔴 OFF"
            markup.row(InlineKeyboardButton(f"{btn['name']} [{status_ico}]", callback_data=f"togbtn_{i}"),
                       InlineKeyboardButton("🗑️ Delete", callback_data=f"delbtn_{i}"))
        markup.add(InlineKeyboardButton("➕ নতুন বাটন যোগ করুন", callback_data="add_custom_btn"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="menu_advanced"))
        bot.edit_message_text("🔘 <b>কাস্টম বাটন ম্যানেজমেন্ট:</b>\nপোস্টের সাথে অতিরিক্ত বাটন যুক্ত করতে পারবেন।", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("togbtn_"):
        idx = int(data.split("_")[1])
        btns = user.get("custom_buttons", [])
        if idx < len(btns):
            btns[idx]["status"] = "off" if btns[idx].get("status") == "on" else "on"
            update_user(chat_id, {"custom_buttons": btns})
            call.data = "menu_custom_buttons"
            callback_handler(call)

    elif data.startswith("delbtn_"):
        idx = int(data.split("_")[1])
        btns = user.get("custom_buttons",[])
        if idx < len(btns):
            btns.pop(idx)
            update_user(chat_id, {"custom_buttons": btns})
            bot.answer_callback_query(call.id, "✅ বাটন মুছে ফেলা হয়েছে!")
            call.data = "menu_custom_buttons"
            callback_handler(call)

    elif data == "add_custom_btn":
        update_step(chat_id, "wait_custom_btn")
        bot.send_message(chat_id, "🔘 বাটনের নাম এবং লিংক `|` দিয়ে আলাদা করে দিন।\nউদাহরণ: `Direct Link | https://example.com`", parse_mode="Markdown")

    # --- Advanced Settings ---
    elif data == "menu_advanced":
        link_btn = "🔗 লিংক: ON 🟢" if user.get("post_link_toggle", 1) == 1 else "🔗 লিংক: OFF 🔴"
        tut_btn = "📽️ টিউটোরিয়াল: ON 🟢" if user.get("post_tutorial_toggle", 1) == 1 else "📽️ টিউটোরিয়াল: OFF 🔴"
        rep_count = user.get("link_repeat_count", 1)
        
        markup = InlineKeyboardMarkup()
        markup.row(InlineKeyboardButton(link_btn, callback_data="toggle_post_link"), InlineKeyboardButton(tut_btn, callback_data="toggle_tutorial_btn"))
        markup.add(InlineKeyboardButton(f"🔄 লিংক রিপিট: {rep_count} বার", callback_data="set_link_repeat"))
        markup.add(InlineKeyboardButton("🔘 কাস্টম বাটন ম্যানেজমেন্ট", callback_data="menu_custom_buttons"))
        markup.add(InlineKeyboardButton("⏳ Auto Delete Time", callback_data="set_autodelete"))
        markup.add(InlineKeyboardButton("👥 এডমিন ম্যানেজমেন্ট", callback_data="manage_admins"))
        markup.row(InlineKeyboardButton("💾 ব্যাকআপ", callback_data="cmd_backup"), InlineKeyboardButton("🔄 রিস্টোর", callback_data="cmd_restore"))
        markup.add(InlineKeyboardButton("🔙 ব্যাক", callback_data="settings"))
        bot.edit_message_text("⚙️ <b>অ্যাডভান্সড সেটিংস:</b>", chat_id, msg_id, reply_markup=markup)

    elif data in ["toggle_post_link", "toggle_tutorial_btn"]:
        key = "post_link_toggle" if data == "toggle_post_link" else "post_tutorial_toggle"
        new_val = 0 if user.get(key, 1) == 1 else 1
        update_user(chat_id, {key: new_val})
        user[key] = new_val 
        call.data = "menu_advanced"
        callback_handler(call)

    elif data == "cmd_backup":
        bot.send_message(chat_id, "⏳ ডাটাবেস ব্যাকআপ তৈরি করা হচ্ছে...")
        backup_data = {
            "users": list(users_col.find({}, {"_id": 0})), "files": list(files_col.find({}, {"_id": 0})),
            "tutorials": list(tutorials_col.find({}, {"_id": 0})), "channels": list(channels_col.find({}, {"_id": 0})),
            "auto_channels": list(auto_channels_col.find({}, {"_id": 0}))
        }
        with open("backup.json", "w") as f: json.dump(backup_data, f)
        with open("backup.json", "rb") as f:
            bot.send_document(chat_id, f, caption="✅ <b>সম্পূর্ণ ডাটাবেস ব্যাকআপ!</b>\nনতুন বটে <b>🔄 রিস্টোর</b> থেকে আপলোড করুন।")
        os.remove("backup.json")
        
    elif data == "cmd_restore":
        update_step(chat_id, "wait_restore")
        bot.send_message(chat_id, "🔄 <b>ডাটাবেস রিস্টোর:</b>\nআপনার সেভ করে রাখা `backup.json` ফাইলটি দিন।", parse_mode="Markdown")

    actions = {
        "menu_texts": ("📝 টেক্সট সেটিংস",[("📁 File Header", "set_file_header"), ("📁 File Footer", "set_file_footer"), ("📤 Post Header", "set_post_header"), ("📤 Post Footer", "set_post_footer"), ("🔙 ব্যাক", "settings")]),
        "menu_channels": ("📢 আপডেট চ্যানেল ম্যানেজ",[("➕ নতুন চ্যানেল", "add_channel"), ("🗑️ সব চ্যানেল মুছুন", "clear_channels"), ("🔙 ব্যাক", "settings")]),
        "menu_tutorials": ("🎥 টিউটোরিয়াল ভিডিও ম্যানেজ",[("➕ নতুন ভিডিও", "add_tutorial"), ("🗑️ সব ভিডিও মুছুন", "clear_tutorials"), ("🔙 ব্যাক", "settings")]),
        "manage_admins": ("👥 এডমিন ম্যানেজমেন্ট",[("➕ এডমিন যোগ", "add_admin"), ("➖ এডমিন রিমুভ", "remove_admin"), ("🔙 ব্যাক", "menu_advanced")])
    }
    if data in actions:
        text, btns = actions[data]
        markup = InlineKeyboardMarkup()
        for btn in btns: markup.add(InlineKeyboardButton(btn[0], callback_data=btn[1]))
        bot.edit_message_text(f"<b>{text}</b>", chat_id, msg_id, reply_markup=markup)

    elif data.startswith("clear_"):
        if data == "clear_channels": channels_col.delete_many({})
        elif data == "clear_tutorials": tutorials_col.delete_many({})
        bot.answer_callback_query(call.id, "✅ মুছে ফেলা হয়েছে!", show_alert=True)

    step_triggers = {
        "set_file_header": ("wait_file_header", "📝 ফাইলের <b>Header</b> লিখে পাঠান।"),
        "set_file_footer": ("wait_file_footer", "📝 ফাইলের <b>Footer</b> লিখে পাঠান।"),
        "set_post_header": ("wait_post_header", "📝 চ্যানেলে পোস্টের <b>Header</b> লিখে পাঠান।"),
        "set_post_footer": ("wait_post_footer", "📝 চ্যানেলে পোস্টের <b>Footer</b> লিখে পাঠান।"),
        "add_channel": ("wait_add_channel", "📢 চ্যানেলের নাম ও লিংক `|` দিয়ে পাঠান।"),
        "add_tutorial": ("wait_add_tutorial", "📽️ ভিডিওর নাম ও লিংক `|` দিয়ে পাঠান।"),
        "set_autodelete": ("wait_autodelete", "⏳ অটো-ডিলিট সময় লিখুন (মিনিটে)। অফ করতে 0।"),
        "set_link_repeat": ("wait_link_repeat", "🔄 চ্যানেলে পোস্ট করার সময় লিংকটি কতবার রিপিট হবে?"),
        "add_admin": ("wait_add_admin", "➕ নতুন এডমিনের Telegram ID লিখে পাঠান।"),
        "remove_admin": ("wait_remove_admin", "➖ যাকে রিমুভ করবেন তার Telegram ID লিখে পাঠান।")
    }
    if data in step_triggers:
        update_step(chat_id, step_triggers[data][0])
        bot.send_message(chat_id, step_triggers[data][1], parse_mode="HTML" if "Header" in step_triggers[data][1] else "Markdown")
        
    elif data == "help_menu":
        bot.edit_message_text("ℹ️ <b>Auto-Healing Technology:</b>\nআপনার লগ চ্যানেলটি সুরক্ষিত রাখুন।", chat_id, msg_id, reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 ব্যাক", callback_data="main_menu")))

# ================= মেসেজ রিসিভ এবং প্রোসেসিং =================
@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'audio', 'forward_from_chat'])
def handle_message(message):
    chat_id = str(message.chat.id)
    text = message.text or message.caption or ""
    user = get_user(chat_id)
    admin_status = is_admin(chat_id)

    if text.startswith("/start"):
        parts = text.split(" ")
        markup = InlineKeyboardMarkup()
        for tut in tutorials_col.find(): markup.add(InlineKeyboardButton(f"📽️ {tut['name']}", url=tut['url']))
        for ch in channels_col.find(): markup.add(InlineKeyboardButton(f"📢 {ch['name']}", url=ch['url']))

        if len(parts) > 1:
            file_key = parts[1]
            files = list(files_col.find({"$or":[{"file_key": file_key}, {"batch_id": file_key}]}))
            if files:
                bot.send_message(chat_id, "⏳ আপনার ফাইলগুলো পাঠানো হচ্ছে...")
                uploader = get_user(files[0]['uploader'])
                caption = f"{uploader.get('header', '')}\n\n{uploader.get('footer', '')}".strip()

                for f in files:
                    msg_id = None
                    try:
                        if f['type'] == 'document': res = bot.send_document(chat_id, f['file_id'], caption=caption, reply_markup=markup)
                        elif f['type'] == 'video': res = bot.send_video(chat_id, f['file_id'], caption=caption, reply_markup=markup)
                        elif f['type'] == 'photo': res = bot.send_photo(chat_id, f['file_id'], caption=caption, reply_markup=markup)
                        elif f['type'] == 'audio': res = bot.send_audio(chat_id, f['file_id'], caption=caption, reply_markup=markup)
                        if res: msg_id = res.message_id
                    except:
                        if f.get('log_chat_id') and f.get('log_msg_id'):
                            try:
                                res = bot.copy_message(chat_id, f['log_chat_id'], f['log_msg_id'], caption=caption, reply_markup=markup)
                                msg_id = res.message_id
                            except: continue
                    
                    if msg_id and uploader.get("auto_delete", 0) > 0:
                        delete_at = int(time.time()) + (uploader["auto_delete"] * 60)
                        queue_col.insert_one({"chat_id": chat_id, "message_id": msg_id, "delete_at": delete_at})
                    time.sleep(0.3)
                if uploader.get("auto_delete", 0) > 0:
                    bot.send_message(chat_id, f"<i>⚠️ সতর্কতা: ফাইলগুলো {uploader['auto_delete']} মিনিট পর ডিলিট হয়ে যাবে।</i>")
            else:
                bot.send_message(chat_id, "❌ ফাইল পাওয়া যায়নি।")
        else:
            if admin_status:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("📦 ব্যাচ আপলোড", callback_data="start_batch"))
                markup.add(InlineKeyboardButton("⚙️ সেটিংস (Settings)", callback_data="settings"))
                markup.add(InlineKeyboardButton("📢 ব্রডকাস্ট", callback_data="broadcast"), InlineKeyboardButton("ℹ️ হেল্প", callback_data="help_menu"))
                bot.send_message(chat_id, "👋 <b>এডমিন প্যানেলে স্বাগতম!</b>", reply_markup=markup)
            else:
                bot.send_message(chat_id, "👋 <b>স্বাগতম!</b>", reply_markup=markup if list(markup.keyboard) else None)
        return

    if not admin_status:
        try:
            bot.forward_message(MAIN_ADMIN_ID, chat_id, message.message_id)
            bot.send_message(MAIN_ADMIN_ID, f"👤 <b>New Message:</b>\nID: `{chat_id}`\n`/reply {chat_id} msg`", parse_mode="Markdown")
            bot.send_message(chat_id, "✅ <i>আপনার মেসেজটি এডমিনের কাছে পাঠানো হয়েছে।</i>")
        except: pass
        return

    if text.startswith("/reply ") and len(text.split(" ", 2)) == 3:
        _, uid, msg = text.split(" ", 2)
        try:
            bot.send_message(uid, f"👨‍💻 <b>এডমিনের উত্তর:</b>\n\n{msg}")
            bot.send_message(chat_id, "✅ মেসেজ পাঠানো হয়েছে!")
        except: bot.send_message(chat_id, "❌ মেসেজ পাঠানো যায়নি।")
        return

    if text == "/cancel":
        update_step(chat_id, "none")
        bot.send_message(chat_id, "❌ কাজ বাতিল করা হয়েছে।")
        return

    step = user.get("step", "none")

    # Add Auto Channels Parsing
    if step in["wait_add_ad", "wait_add_premium", "wait_add_log"]:
        if "|" in text:
            c_name, c_id = text.split("|", 1)
            c_type = step.split("_")[2]
            ch_uuid = str(uuid.uuid4().hex)[:8]
            auto_channels_col.insert_one({"ch_id": ch_uuid, "type": c_type, "name": c_name.strip(), "channel_id": c_id.strip(), "status": "on"})
            update_step(chat_id, "none")
            bot.send_message(chat_id, f"✅ চ্যানেল সফলভাবে অ্যাড হয়েছে: {c_name.strip()}")
        else:
            bot.send_message(chat_id, "⚠️ ভুল ফরম্যাট! সঠিক নিয়ম: `Name | Channel_ID`", parse_mode="Markdown")
        return

    # Add Custom Button Parsing
    if step == "wait_custom_btn":
        if "|" in text:
            b_name, b_link = text.split("|", 1)
            btns = user.get("custom_buttons",[])
            btns.append({"name": b_name.strip(), "url": b_link.strip(), "status": "on"})
            update_user(chat_id, {"custom_buttons": btns, "step": "none"})
            bot.send_message(chat_id, "✅ কাস্টম বাটন যুক্ত হয়েছে!")
        else:
            bot.send_message(chat_id, "⚠️ ভুল ফরম্যাট! সঠিক নিয়ম: `Name | Link`", parse_mode="Markdown")
        return

    if step == "wait_broadcast":
        update_step(chat_id, "none")
        bot.send_message(chat_id, "⏳ ব্রডকাস্ট শুরু হয়েছে...")
        count = 0
        for u in users_col.find():
            try:
                bot.copy_message(u['chat_id'], chat_id, message.message_id)
                count += 1
                time.sleep(0.05)
            except: pass
        bot.send_message(chat_id, f"✅ ব্রডকাস্ট শেষ! মোট <b>{count}</b> জনকে মেসেজ পাঠানো হয়েছে।")
        return

    if step == "wait_file_header": update_user(chat_id, {"header": text, "step": "none"}); bot.send_message(chat_id, "✅ সেভ হয়েছে!")
    elif step == "wait_file_footer": update_user(chat_id, {"footer": text, "step": "none"}); bot.send_message(chat_id, "✅ সেভ হয়েছে!")
    elif step == "wait_post_header": update_user(chat_id, {"post_header": text, "step": "none"}); bot.send_message(chat_id, "✅ সেভ হয়েছে!")
    elif step == "wait_post_footer": update_user(chat_id, {"post_footer": text, "step": "none"}); bot.send_message(chat_id, "✅ সেভ হয়েছে!")
    elif step == "wait_autodelete" and text.isdigit(): update_user(chat_id, {"auto_delete": int(text), "step": "none"}); bot.send_message(chat_id, "✅ সেট করা হয়েছে!")
    elif step == "wait_link_repeat" and text.isdigit(): update_user(chat_id, {"link_repeat_count": int(text), "step": "none"}); bot.send_message(chat_id, "✅ সেট করা হয়েছে!")
    elif step == "wait_add_channel" and "|" in text:
        n, l = text.split("|", 1)
        channels_col.insert_one({"name": n.strip(), "url": l.strip()})
        update_step(chat_id, "none"); bot.send_message(chat_id, "✅ চ্যানেল যুক্ত হয়েছে!")
    elif step == "wait_add_tutorial" and "|" in text:
        n, l = text.split("|", 1)
        tutorials_col.insert_one({"name": n.strip(), "url": l.strip()})
        update_step(chat_id, "none"); bot.send_message(chat_id, "✅ টিউটোরিয়াল যুক্ত হয়েছে!")

    if step == "wait_restore" and message.document:
        try:
            bot.send_message(chat_id, "⏳ ডাটাবেস রিস্টোর করা হচ্ছে...")
            file_info = bot.get_file(message.document.file_id)
            data = json.loads(bot.download_file(file_info.file_path))
            if "users" in data and data["users"]: users_col.insert_many(data["users"])
            if "files" in data and data["files"]: files_col.insert_many(data["files"])
            if "auto_channels" in data and data["auto_channels"]: auto_channels_col.insert_many(data["auto_channels"])
            update_step(chat_id, "none")
            bot.send_message(chat_id, "✅ <b>ডাটাবেস সফলভাবে রিস্টোর হয়েছে!</b>")
        except Exception as e: bot.send_message(chat_id, f"❌ রিস্টোর ব্যর্থ! Error: {e}")
        return

    # Thumbnail Upload & Confirm 
    if step == "wait_thumbnail":
        if text == "/skip":
            update_user(chat_id, {"step": "none", "pending_link": "", "pending_short_link": ""})
            bot.send_message(chat_id, "✅ স্কিপ করা হয়েছে।")
            return
            
        if message.video:
            update_user(chat_id, {"temp_media_id": message.video.file_id, "temp_media_type": "video"})
            markup = InlineKeyboardMarkup()
            markup.row(InlineKeyboardButton("✅ Confirm", callback_data="confirm_vid_thumb"), InlineKeyboardButton("❌ Cancel", callback_data="cancel_vid_thumb"))
            bot.send_message(chat_id, "🎥 এটি একটি ভিডিও। আপনি কি এটি থাম্বনেইল হিসেবে চ্যানেলে পোস্ট করতে চান?", reply_markup=markup)
            return

        elif message.photo:
            execute_channel_post(chat_id, user, "photo", message.photo[-1].file_id)
            return
        else:
            bot.send_message(chat_id, "⚠️ দয়া করে একটি ছবি বা ভিডিও দিন, অথবা /skip লিখুন।")
            return

    # File Upload Handler
    file_id, f_type = None, None
    if message.document: file_id, f_type = message.document.file_id, "document"
    elif message.video and step != "wait_thumbnail": file_id, f_type = message.video.file_id, "video"
    elif message.audio: file_id, f_type = message.audio.file_id, "audio"
    elif message.photo and step != "wait_thumbnail": file_id, f_type = message.photo[-1].file_id, "photo"

    if file_id:
        unique_id = str(uuid.uuid4().hex)[:10]
        log_chat, log_msg = "", ""
        log_ch = auto_channels_col.find_one({"type": "log", "status": "on"})
        if log_ch:
            try:
                res = None
                if f_type == 'document': res = bot.send_document(log_ch['channel_id'], file_id, caption=f"💾 <b>File Backup</b>\n<code>{file_id}</code>")
                elif f_type == 'video': res = bot.send_video(log_ch['channel_id'], file_id, caption=f"💾 <b>File Backup</b>\n<code>{file_id}</code>")
                elif f_type == 'photo': res = bot.send_photo(log_ch['channel_id'], file_id, caption=f"💾 <b>File Backup</b>\n<code>{file_id}</code>")
                if res: log_chat, log_msg = log_ch['channel_id'], res.message_id
            except: pass

        if step == "wait_batch":
            batch_id = user.get("batch_id")
            files_col.insert_one({"file_key": unique_id, "file_id": file_id, "type": f_type, "uploader": chat_id, "batch_id": batch_id, "log_chat_id": log_chat, "log_msg_id": log_msg})
            count = files_col.count_documents({"batch_id": batch_id})
            markup = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ আপলোড শেষ (Finish)", callback_data="finish_batch"))
            bot.send_message(chat_id, f"✅ <b>{count} নং ফাইল ব্যাচে যুক্ত হয়েছে!</b>", reply_markup=markup)
        else:
            files_col.insert_one({"file_key": unique_id, "file_id": file_id, "type": f_type, "uploader": chat_id, "batch_id": "", "log_chat_id": log_chat, "log_msg_id": log_msg})
            bot_deep_link = f"https://t.me/{BOT_USERNAME}?start={unique_id}"
            short_link = get_terabox_short_link(bot_deep_link)
            update_user(chat_id, {"step": "wait_thumbnail", "pending_link": bot_deep_link, "pending_short_link": short_link})
            reply = f"✅ <b>ফাইল সেভ হয়েছে!</b>\n💎 ডাইরেক্ট লিংক: {bot_deep_link}\n📺 শর্ট লিংক: {short_link}\n\n🖼️ <b>চ্যানেলে পোস্ট করার জন্য একটি থাম্বনেইল (ছবি/ভিডিও) দিন।</b>\n<i>(/skip লিখতে পারেন)</i>"
            bot.send_message(chat_id, reply, disable_web_page_preview=True)

# ================= Render Web Server =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running beautifully with ALL advanced features!"

def run_server():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server)
    server_thread.start()
    
    print("🤖 Bot Started Successfully...")
    while True:
        try:
            bot.polling(none_stop=True, timeout=60)
        except Exception as e:
            print(f"Bot Polling Error: {e}")
            time.sleep(5)
