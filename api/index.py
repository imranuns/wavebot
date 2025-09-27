import os
import json
import logging
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Dispatcher, MessageHandler, Filters, CommandHandler, CallbackContext

# Flask app መፍጠር
app = Flask(__name__)

# Environment variables መውሰድ
# እነዚህን በ Vercel ላይ እናስገባቸዋለን
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))

# Bot እና Dispatcher ማዘጋጀት
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# -----------------------------------------------------------------------------
# ማሳሰቢያ፡ Vercel ላይ ፋይል ማስቀመጥ ስለማይቻል፣ 
# ለጊዜው የቻናል ዝርዝርን እዚሁ ኮዱ ላይ እናስቀምጣለን።
# በሚቀጥለው ደረጃ ይህንን በዳታቤዝ እንተካዋለን።
# -----------------------------------------------------------------------------
temp_data = {
    "channels": [],
    "stats": {"broadcasts": 0}
}

# Helper functions (የድሮ ኮዳችን)
def is_admin(update: Update) -> bool:
    """ተጠቃሚው አስተዳዳሪ መሆኑን ቼክ ያደርጋል"""
    return update.effective_user.id == ADMIN_USER_ID

# Command Handlers
def start(update: Update, context: CallbackContext):
    """የ /start ትዕዛዝ ሲላክ"""
    if not is_admin(update):
        return
    update.message.reply_text("👋 እንኳን ደህና መጡ! የቻናል ማስተዳደሪያ ቦት።\n/help ብለው በመጻፍ ትዕዛዞችን ይመልከቱ።")

def add_channel(update: Update, context: CallbackContext):
    """አዲስ ቻናል ለመጨመር"""
    if not is_admin(update):
        return
    try:
        channel_name = context.args[0]
        if channel_name not in temp_data["channels"]:
            temp_data["channels"].append(channel_name)
            update.message.reply_text(f"✅ ቻናል '{channel_name}' በተሳካ ሁኔታ ተመዝግቧል።")
        else:
            update.message.reply_text(f"⚠️ ቻናል '{channel_name}' ከዚህ በፊት ተመዝግቧል።")
    except (IndexError, ValueError):
        update.message.reply_text("❌ ስህተት! እባክዎ እንዲህ ይጠቀሙ: /addchannel @channelusername")

def remove_channel(update: Update, context: CallbackContext):
    """ቻናል ለማስወገድ"""
    if not is_admin(update):
        return
    try:
        channel_name = context.args[0]
        if channel_name in temp_data["channels"]:
            temp_data["channels"].remove(channel_name)
            update.message.reply_text(f"🗑️ ቻናል '{channel_name}' በተሳካ ሁኔታ ተወግዷል።")
        else:
            update.message.reply_text(f"🤔 ቻናል '{channel_name}' አልተገኘም።")
    except (IndexError, ValueError):
        update.message.reply_text("❌ ስህተት! እባክዎ እንዲህ ይጠቀሙ: /removechannel @channelusername")

def list_channels(update: Update, context: CallbackContext):
    """የተመዘገቡ ቻናሎችን ለማየት"""
    if not is_admin(update):
        return
    if temp_data["channels"]:
        message = "📜 የተመዘገቡ ቻናሎች ዝርዝር:\n\n"
        for channel in temp_data["channels"]:
            message += f"- {channel}\n"
        update.message.reply_text(message)
    else:
        update.message.reply_text("🤷‍♂️ ምንም የተመዘገበ ቻናል የለም። /addchannel ይጠቀሙ።")

def stats(update: Update, context: CallbackContext):
    """ስታቲስቲክስ ለማየት"""
    if not is_admin(update):
        return
    b_count = temp_data["stats"]["broadcasts"]
    update.message.reply_text(f"📊 ስታቲስቲክስ:\n- የተላኩ መልዕክቶች ብዛት: {b_count}")

# Message Handler for Broadcasting
def broadcast_message(update: Update, context: CallbackContext):
    """የተላከን ማንኛውንም መልዕክት ለሁሉም ቻናሎች መላክ"""
    if not is_admin(update):
        return
    
    if not temp_data["channels"]:
        update.message.reply_text("⚠️ ምንም የተመዘገበ ቻናል ስለሌለ መልዕክቱን መላክ አልተቻለም።")
        return

    message = update.effective_message
    sent_count = 0
    failed_count = 0

    for channel in temp_data["channels"]:
        try:
            # በቀጥታ መልዕክቱን ኮፒ አድርጎ ይልካል (ፎቶ፣ ቪዲዮ፣ በተን ሁሉንም ያካትታል)
            message.copy(chat_id=channel)
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send to {channel}: {e}")
            failed_count += 1
    
    # ስራው ሲጠናቀቅ ለአስተዳዳሪው ሪፖርት መላክ
    update.message.reply_text(
        f"📡 መልዕክቱ ተልኳል!\n\n"
        f"✅ ለ {sent_count} ቻናሎች ተልኳል።\n"
        f"❌ ለ {failed_count} ቻናሎች አልተላከም።"
    )
    temp_data["stats"]["broadcasts"] += 1


# Dispatcher ላይ ትዕዛዞችን መመዝገብ
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("addchannel", add_channel))
dispatcher.add_handler(CommandHandler("removechannel", remove_channel))
dispatcher.add_handler(CommandHandler("listchannels", list_channels))
dispatcher.add_handler(CommandHandler("stats", stats))
dispatcher.add_handler(MessageHandler(Filters.all & ~Filters.command, broadcast_message))

# -----------------------------------------------------------------------------
# ለ Vercel የሚያስፈልግ ዋናው ክፍል (Webhook Handler)
# -----------------------------------------------------------------------------
@app.route('/api', methods=['POST'])
def webhook_handler():
    """ይህ ፈንክሽን ከቴሌግራም መልዕክት ሲመጣ ይጠራል"""
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/')
def index():
    return 'Hello, I am your bot!'
