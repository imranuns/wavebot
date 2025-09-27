import os
import json
import logging
import redis
import uuid
import re
from datetime import datetime, timedelta
from flask import Flask, request
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Dispatcher, MessageHandler, Filters, CommandHandler, CallbackContext, CallbackQueryHandler

# --- Environment Variables & Basic Setup ---
app = Flask(__name__)
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
KV_URL = os.getenv("KV_URL")
CRON_SECRET = os.getenv("CRON_SECRET", "default-secret-for-testing") # For security

# --- Database Connection (Vercel KV) ---
try:
    if not KV_URL:
        logging.error("KV_URL is not set!")
        kv = None
    else:
        kv = redis.from_url(KV_URL)
        logging.info("Successfully connected to Vercel KV.")
except Exception as e:
    logging.error(f"Failed to connect to Redis: {e}")
    kv = None

bot = Bot(token=TOKEN)
dispatcher = Dispatcher(bot, None, use_context=True)

# --- Helper Functions ---
def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_USER_ID

def get_user_state(user_id):
    state_json = kv.get(f"state:{user_id}")
    return json.loads(state_json) if state_json else None

def set_user_state(user_id, state_data):
    kv.set(f"state:{user_id}", json.dumps(state_data), ex=300) # State expires in 5 mins

def clear_user_state(user_id):
    kv.delete(f"state:{user_id}")
    
def parse_time(time_str: str) -> timedelta or None:
    match = re.match(r"(\d+)([mhd])", time_str.lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm':
        return timedelta(minutes=value)
    if unit == 'h':
        return timedelta(hours=value)
    if unit == 'd':
        return timedelta(days=value)
    return None

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    user_name = update.effective_user.first_name
    
    text = f"ሰላም {user_name}! ወደ ቦትህ እንኳን በደህና መጣህ።\n\n" \
           "**ዋና ዋና ትዕዛዞች:**\n" \
           "📢 `ማስታወቂያ ለመላክ:` ማንኛውንም መልዕክት በቀጥታ ላክልኝ።\n\n" \
           "**ቻናል ማስተዳደሪያ:**\n" \
           "➕ `/addchannel @username` - አዲስ ቻናል ለመጨመር።\n" \
           "➖ `/removechannel @username` - ቻናል ለማስወገድ።\n" \
           "📋 `/listchannels` - የተመዘገቡ ቻናሎችን ለማየት።\n\n" \
           "**የጊዜ ሰሌዳ ማስተዳደሪያ:**\n" \
           "⏰ `/schedule 10m` - መልዕክትን በሰዓት መርሐግብር ለማስያዝ።\n" \
           "🗒️ `/scheduledposts` - የታዘዙ መልዕክቶችን ለማየትና ለመሰረዝ።\n\n" \
           "**ተጨማሪ ትዕዛዞች:**\n" \
           "📊 `/stats` - የተላኩ መልዕክቶች ብዛት ለማየት።\n" \
           "ℹ️ `/help` - ይህንን መልዕክት እንደገና ለማየት።"
           
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def schedule_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    try:
        time_str = context.args[0]
        delay = parse_time(time_str)
        if delay is None:
            update.message.reply_text("❌ የተሳሳተ የጊዜ አጻጻፍ! እባክዎ እንዲህ ይጠቀሙ: `10m` (ለደቂቃ), `2h` (ለሰዓት), `1d` (ለቀን)።")
            return
        
        future_time = datetime.utcnow() + delay
        set_user_state(update.effective_user.id, {
            "action": "schedule",
            "schedule_time_utc": future_time.isoformat()
        })
        update.message.reply_text(f"👍 ጥሩ! አሁን እንዲላክልህ የምትፈልገውን መልዕክት ላክልኝ። በ **{time_str}** ውስጥ ይላካል።")
    except IndexError:
        update.message.reply_text("❌ እባክዎ ከትዕዛዙ ቀጥሎ ጊዜውን ያስገቡ።\nምሳሌ: `/schedule 10m`")

def scheduled_posts_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    scheduled_posts_json = kv.get("wavebot:scheduled_posts")
    posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []

    if not posts:
        update.message.reply_text("🤷‍♂️ ምንም በጊዜ ቀጠሮ የተያዘ መልዕክት የለም።")
        return

    message = "🗒️ **የታዘዙ መልዕክቶች ዝርዝር:**\n\n"
    for post in posts:
        post_time_utc = datetime.fromisoformat(post['schedule_time_utc'])
        # Convert UTC to local time (assuming EAT which is UTC+3)
        post_time_local = post_time_utc + timedelta(hours=3)
        message += f"- 🕒 **በ `{post_time_local.strftime('%Y-%m-%d %H:%M')}`** ይላካል.\n"
    update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)

def process_message(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    
    # Check if this message is for scheduling
    if state and state.get("action") == "schedule":
        scheduled_posts_json = kv.get("wavebot:scheduled_posts")
        posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
        
        new_post = {
            "schedule_id": str(uuid.uuid4()),
            "admin_chat_id": update.message.chat_id,
            "message_id": update.message.message_id,
            "schedule_time_utc": state["schedule_time_utc"]
        }
        posts.append(new_post)
        kv.set("wavebot:scheduled_posts", json.dumps(posts))
        
        clear_user_state(user_id)
        update.message.reply_text("✅ መልዕክትህ በተሳካ ሁኔታ ለበኋላ እንዲላክ ታዟል።")
    else:
        # It's a normal broadcast message
        broadcast_message(update, context)

def broadcast_message(update: Update, context: CallbackContext, scheduled_job=None):
    channels_json = kv.get("wavebot:channels")
    channels = json.loads(channels_json) if channels_json else []
    
    if not channels:
        if not scheduled_job:
            update.message.reply_text("⚠️ ምንም የተመዘገበ ቻናል የለም።")
        return

    message = update.effective_message if not scheduled_job else scheduled_job['message']
    broadcast_id = str(uuid.uuid4())
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ ሁሉንም አጥፋ", callback_data=f"delete_{broadcast_id}")]])
    
    sent_messages = []
    failed_count = 0
    
    for channel in channels:
        try:
            sent_msg = message.copy(chat_id=channel, reply_markup=keyboard)
            sent_messages.append({"chat_id": sent_msg.chat.id, "message_id": sent_msg.message_id})
        except Exception as e:
            logging.error(f"Failed to send to {channel}: {e}")
            failed_count += 1
            
    kv.set(f"broadcast:{broadcast_id}", json.dumps(sent_messages), ex=604800) # Data expires in 7 days
    kv.incr("wavebot:broadcasts")
    
    if not scheduled_job:
        update.message.reply_text(f"📡 መልዕክቱ ተልኳል!\n\n✅ ለ {len(sent_messages)} ቻናሎች ተልኳል።\n❌ ለ {failed_count} ቻናሎች አልተላከም።")

def button_callback_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    
    data = query.data
    if data.startswith("delete_"):
        broadcast_id = data.split("_")[1]
        messages_to_delete_json = kv.get(f"broadcast:{broadcast_id}")
        
        if not messages_to_delete_json:
            query.edit_message_text(text="❌ ይቅርታ፣ ይህ መልዕክት ጊዜው አልፎበታል ወይም ከዳታቤዝ ተሰርዟል።")
            return
            
        messages = json.loads(messages_to_delete_json)
        deleted_count = 0
        for msg_info in messages:
            try:
                bot.delete_message(chat_id=msg_info['chat_id'], message_id=msg_info['message_id'])
                deleted_count += 1
            except Exception as e:
                logging.error(f"Could not delete message: {e}")
        
        query.edit_message_text(text=f"🗑️ መልዕክቱ ከ {deleted_count} ቻናሎች ላይ ተሰርዟል።")
        kv.delete(f"broadcast:{broadcast_id}")

def cron_job_runner():
    scheduled_posts_json = kv.get("wavebot:scheduled_posts")
    all_posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
    
    if not all_posts:
        return "No scheduled posts to run.", 200

    now_utc = datetime.utcnow()
    posts_to_send = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) <= now_utc]
    remaining_posts = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) > now_utc]
    
    for post in posts_to_send:
        try:
            # We need a fake update object to pass to the broadcast function
            class FakeMessage:
                def __init__(self, bot, chat_id, msg_id):
                    self._bot = bot
                    self.chat_id = chat_id
                    self.message_id = msg_id
                def copy(self, chat_id, reply_markup):
                    return self._bot.copy_message(from_chat_id=self.chat_id, chat_id=chat_id, message_id=self.message_id, reply_markup=reply_markup)
            
            fake_update = {'effective_message': FakeMessage(bot, post['admin_chat_id'], post['message_id'])}
            broadcast_message(None, None, scheduled_job={'message': fake_update['effective_message']})
        except Exception as e:
            logging.error(f"Error sending scheduled post {post['schedule_id']}: {e}")

    kv.set("wavebot:scheduled_posts", json.dumps(remaining_posts))
    return f"Processed {len(posts_to_send)} scheduled posts.", 200

# --- Add handlers to dispatcher ---
# (Existing handlers for add/remove/list/stats are assumed to be here and correct)
# Re-adding them for completeness.
from api.index_helpers import add_channel, remove_channel, list_channels, stats
dispatcher.add_handler(CommandHandler("start", start_command))
dispatcher.add_handler(CommandHandler("help", start_command))
dispatcher.add_handler(CommandHandler("schedule", schedule_command))
dispatcher.add_handler(CommandHandler("scheduledposts", scheduled_posts_command))
dispatcher.add_handler(CommandHandler("addchannel", add_channel))
dispatcher.add_handler(CommandHandler("removechannel", remove_channel))
dispatcher.add_handler(CommandHandler("listchannels", list_channels))
dispatcher.add_handler(CommandHandler("stats", stats))
dispatcher.add_handler(CallbackQueryHandler(button_callback_handler))
dispatcher.add_handler(MessageHandler(Filters.all & ~Filters.command, process_message))

# --- Webhook Handler for Vercel ---
@app.route('/api', methods=['POST'])
def webhook_handler():
    if not kv: return 'error: database not configured', 500
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/api/cron', methods=['GET'])
def cron_handler():
    # Secure the cron endpoint
    auth_header = request.headers.get('authorization')
    if not auth_header or auth_header != f"Bearer {CRON_SECRET}":
        return "Unauthorized", 401
    
    if not kv: return 'error: database not configured', 500
    result, status_code = cron_job_runner()
    return result, status_code

@app.route('/')
def index():
    return 'Hello, I am your fully featured bot!'
