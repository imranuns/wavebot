import os
import json
import logging
import redis
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Dispatcher, MessageHandler, Filters, CommandHandler, CallbackContext

# Flask app መፍጠር
app = Flask(__name__)

# --- Environment Variables ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
KV_URL = os.getenv("KV_URL")

# --- ዳታቤዝ ማገናኘት ---
try:
    if not KV_URL:
        logging.error("KV_URL is not set!")
        kv = None
    else:
        # Vercel KV (Redis) ዳታቤዝን ማገናኘት
        kv = redis.from_url(KV_URL)
        logging.info("Successfully connected to Vercel KV.")
except Exception as e:
    logging.error(f"Failed to connect to Redis: {e}")
    kv = None

# Bot እና Dispatcher ማዘጋጀት
bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# --- Helper Functions ---
def is_admin(update: Update) -> bool:
    """ተጠቃሚው አስተዳዳሪ መሆኑን ቼክ ያደርጋል"""
    return update.effective_user.id == ADMIN_USER_ID

def get_channels() -> list:
    """የተመዘገቡ ቻናሎችን ከዳታቤዝ ያመጣል"""
    channels_json = kv.get("wavebot:channels")
    if channels_json:
        return json.loads(channels_json)
    return []

def save_channels(channels: list):
    """የቻናሎችን ዝርዝር በዳታቤዝ ላይ ያስቀምጣል"""
    kv.set("wavebot:channels", json.dumps(channels))

# --- Command Handlers (ከዳታቤዝ ጋር እንዲሰሩ ተሻሽለዋል) ---
def start(update: Update, context: CallbackContext):
    """የ /start ትዕዛዝ ሲላክ"""
    if not is_admin(update): return
    if not kv:
        update.message.reply_text("⚠️ ዳታቤዝ አልተገናኘም። እባክዎ የ Vercel ሴቲንግን ይመልከቱ።")
        return
    update.message.reply_text("👋 እንኳን ደህና መጡ! ቦቱ አሁን ከዳታቤዝ ጋር ተገናኝቷል።\n/help ብለው በመጻፍ ትዕዛዞችን ይመልከቱ።")

def add_channel(update: Update, context: CallbackContext):
    """አዲስ ቻናል ለመጨመር"""
    if not is_admin(update) or not kv: return
    try:
        channel_name = context.args[0]
        channels = get_channels()
        if channel_name not in channels:
            channels.append(channel_name)
            save_channels(channels)
            update.message.reply_text(f"✅ ቻናል '{channel_name}' በቋሚነት ተመዝግቧል።")
        else:
            update.message.reply_text(f"⚠️ ቻናል '{channel_name}' ከዚህ በፊት ተመዝግቧል።")
    except (IndexError, ValueError):
        update.message.reply_text("❌ ስህተት! እባክዎ እንዲህ ይጠቀሙ: /addchannel @channelusername")

def remove_channel(update: Update, context: CallbackContext):
    """ቻናል ለማስወገድ"""
    if not is_admin(update) or not kv: return
    try:
        channel_name = context.args[0]
        channels = get_channels()
        if channel_name in channels:
            channels.remove(channel_name)
            save_channels(channels)
            update.message.reply_text(f"🗑️ ቻናል '{channel_name}' በቋሚነት ተወግዷል።")
        else:
            update.message.reply_text(f"🤔 ቻናል '{channel_name}' አልተገኘም።")
    except (IndexError, ValueError):
        update.message.reply_text("❌ ስህተት! እባክዎ እንዲህ ይጠቀሙ: /removechannel @channelusername")

def list_channels(update: Update, context: CallbackContext):
    """የተመዘገቡ ቻናሎችን ለማየት"""
    if not is_admin(update) or not kv: return
    channels = get_channels()
    if channels:
        message = "📜 በዳታቤዝ የተመዘገቡ ቻናሎች:\n\n"
        for channel in channels:
            message += f"- {channel}\n"
        update.message.reply_text(message)
    else:
        update.message.reply_text("🤷‍♂️ ምንም የተመዘገበ ቻናል የለም። /addchannel ይጠቀሙ።")

def stats(update: Update, context: CallbackContext):
    """ስታቲስቲክስ ለማየት"""
    if not is_admin(update) or not kv: return
    b_count = kv.get("wavebot:broadcasts") or 0
    update.message.reply_text(f"📊 ስታቲስቲክስ:\n- የተላኩ መልዕክቶች ብዛት: {int(b_count)}")

# --- Message Handler for Broadcasting ---
def broadcast_message(update: Update, context: CallbackContext):
    """የተላከን ማንኛውንም መልዕክት ለሁሉም ቻናሎች መላክ"""
    if not is_admin(update) or not kv: return
    
    channels = get_channels()
    if not channels:
        update.message.reply_text("⚠️ ምንም የተመዘገበ ቻናል ስለሌለ መልዕክቱን መላክ አልተቻለም።")
        return

    message = update.effective_message
    sent_count = 0
    failed_count = 0

    for channel in channels:
        try:
            message.copy(chat_id=channel)
            sent_count += 1
        except Exception as e:
            logging.error(f"Failed to send to {channel}: {e}")
            failed_count += 1
    
    update.message.reply_text(
        f"📡 መልዕክቱ ተልኳል!\n\n✅ ለ {sent_count} ቻናሎች ተልኳል።\n❌ ለ {failed_count} ቻናሎች አልተላከም።"
    )
    # ስታቲስቲክስን በዳታቤዝ ላይ መጨመር
    kv.incr("wavebot:broadcasts")

# Dispatcher ላይ ትዕዛዞችን መመዝገብ
dispatcher.add_handler(CommandHandler("start", start))
dispatcher.add_handler(CommandHandler("addchannel", add_channel))
dispatcher.add_handler(CommandHandler("removechannel", remove_channel))
dispatcher.add_handler(CommandHandler("listchannels", list_channels))
dispatcher.add_handler(CommandHandler("stats", stats))
dispatcher.add_handler(MessageHandler(Filters.all & ~Filters.command, broadcast_message))

# --- Webhook Handler for Vercel ---
@app.route('/api', methods=['POST'])
def webhook_handler():
    if not kv:
        logging.error("Webhook triggered but no KV connection.")
        return 'error: database not configured', 500

    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/')
def index():
    return 'Hello, I am your bot and I am connected to a database!'

