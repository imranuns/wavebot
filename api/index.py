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
CRON_SECRET = os.getenv("CRON_SECRET", "default-secret-for-testing") 

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

# --- State Management Helper Functions ---
def get_user_state(user_id):
    state_json = kv.get(f"state:{user_id}")
    return json.loads(state_json) if state_json else {}

def set_user_state(user_id, state_data):
    current_state = get_user_state(user_id)
    current_state.update(state_data)
    kv.set(f"state:{user_id}", json.dumps(current_state), ex=600) # Expire in 10 mins

def clear_user_state(user_id):
    kv.delete(f"state:{user_id}")
    
def parse_time(time_str: str) -> timedelta or None:
    match = re.match(r"(\d+)([mhd])", time_str.lower())
    if not match: return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm': return timedelta(minutes=value)
    if unit == 'h': return timedelta(hours=value)
    if unit == 'd': return timedelta(days=value)
    return None

def get_channels() -> list:
    channels_json = kv.get("wavebot:channels")
    return json.loads(channels_json) if channels_json else []

def save_channels(channels: list):
    kv.set("wavebot:channels", json.dumps(channels))

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    user_name = update.effective_user.first_name
    clear_user_state(update.effective_user.id) # Clear any previous state
    
    text = (f"ሰላም {user_name}! ወደ ቦትህ እንኳን በደህና መጣህ።\n\n"
            "**ዋና ዋና ትዕዛዞች:**\n"
            "📢 `ማስታወቂያ ለመላክ:` ማንኛውንም መልዕክት በቀጥታ ላክልኝ።\n\n"
            "**ቻናል ማስተዳደሪያ:**\n"
            "➕ `/addchannel @username`\n"
            "➖ `/removechannel @username`\n"
            "📋 `/listchannels`\n\n"
            "**የጊዜ ሰሌዳ ማስተዳደሪያ:**\n"
            "⏰ `/schedule` - መልዕክትን በሰዓት ለማዘዝ።\n"
            "🗒️ `/scheduledposts` - የታዘዙትን ለማየትና ለመሰረዝ።\n\n"
            "**ተጨማሪ ትዕዛዞች:**\n"
            "📊 `/stats`\n"
            "ℹ️ `/help` - ይህንን መልዕክት እንደገና ለማየት።")
           
    update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

def cancel_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    clear_user_state(update.effective_user.id)
    update.message.reply_text("✅ የጀመርከው ስራ ተሰርዟል።")

def add_channel_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    try:
        channel_name = context.args[0]
        if not channel_name.startswith('@'):
            update.message.reply_text("❌ ስህተት! የቻናል ስም በ '@' መጀመር አለበት።")
            return
        channels = get_channels()
        if channel_name not in channels:
            channels.append(channel_name)
            save_channels(channels)
            update.message.reply_text(f"✅ ቻናል '{channel_name}' ተመዝግቧል።")
        else:
            update.message.reply_text(f"⚠️ ቻናል '{channel_name}' ከዚህ በፊት ተመዝግቧል።")
    except IndexError:
        update.message.reply_text("❌ እባክዎ እንዲህ ይጠቀሙ: /addchannel @username")

def remove_channel_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    try:
        channel_name = context.args[0]
        channels = get_channels()
        if channel_name in channels:
            channels.remove(channel_name)
            save_channels(channels)
            update.message.reply_text(f"🗑️ ቻናል '{channel_name}' ተወግዷል።")
        else:
            update.message.reply_text(f"🤔 ቻናል '{channel_name}' አልተገኘም።")
    except IndexError:
        update.message.reply_text("❌ እባክዎ እንዲህ ይጠቀሙ: /removechannel @username")

def list_channels_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    channels = get_channels()
    if channels:
        message = "📜 የተመዘገቡ ቻናሎች:\n\n" + "\n".join(f"- {ch}" for ch in channels)
        update.message.reply_text(message)
    else:
        update.message.reply_text("🤷‍♂️ ምንም የተመዘገበ ቻናል የለም።")
        
def stats_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    broadcast_count = kv.get("wavebot:broadcasts") or 0
    update.message.reply_text(f"📊 ስታቲስቲክስ:\n- የተላኩ መልዕክቶች ብዛት: {int(broadcast_count)}")

def schedule_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    set_user_state(update.effective_user.id, {"action": "awaiting_schedule_time"})
    update.message.reply_text(
        "👍 መልዕክቱ ከስንት ጊዜ በኋላ ይላክ?\n"
        "ምሳሌ: `10m` (ለ 10 ደቂቃ), `2h` (ለ 2 ሰዓት)\n\n"
        "ለመሰረዝ /cancel ብለው ይጻፉ።",
        parse_mode=ParseMode.MARKDOWN
    )

def scheduled_posts_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    scheduled_posts_json = kv.get("wavebot:scheduled_posts")
    posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []

    if not posts:
        update.message.reply_text("🤷‍♂️ ምንም በጊዜ ቀጠሮ የተያዘ መልዕክት የለም።")
        return

    message = "🗒️ **የታዘዙ መልዕክቶች ዝርዝር:**\n\n"
    keyboard = []
    for i, post in enumerate(posts):
        post_time_utc = datetime.fromisoformat(post['schedule_time_utc'])
        post_time_local = post_time_utc + timedelta(hours=3) # EAT (UTC+3)
        message += f"**{i+1}.** 🕒 `{post_time_local.strftime('%Y-%m-%d %H:%M')}` ላይ ይላካል።\n"
        keyboard.append([InlineKeyboardButton(f"❌ {i+1}ኛውን ሰርዝ", callback_data=f"cancel_scheduled_{post['schedule_id']}")])

    update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


def process_message(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    action = state.get("action")

    if action == "awaiting_schedule_time":
        time_str = update.message.text
        delay = parse_time(time_str)
        if delay is None:
            update.message.reply_text("❌ የተሳሳተ የጊዜ አጻጻፍ! እባክዎ እንዲህ ይጠቀሙ: `10m`, `2h`።\n\nለመሰረዝ /cancel ብለው ይጻፉ።", parse_mode=ParseMode.MARKDOWN)
            return
        
        future_time = datetime.utcnow() + delay
        set_user_state(user_id, {
            "action": "awaiting_schedule_message",
            "schedule_time_utc": future_time.isoformat()
        })
        update.message.reply_text("✅ ጥሩ! አሁን እንዲላክልህ የምትፈልገውን መልዕክት ላክልኝ።")

    elif action == "awaiting_schedule_message":
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
        # Default action: Ask how to broadcast
        set_user_state(user_id, {
            "action": "confirm_broadcast",
            "message_to_send": {
                "chat_id": update.message.chat_id,
                "message_id": update.message.message_id
            }
        })
        keyboard = [
            [InlineKeyboardButton("✅ አሁኑኑ ላክ", callback_data="broadcast_now")],
            [InlineKeyboardButton("⏰ በጊዜ ቀጠሮ አስቀምጥ", callback_data="broadcast_schedule")],
            [InlineKeyboardButton("❌ ሰርዝ", callback_data="broadcast_cancel")]
        ]
        update.message.reply_text("ይህንን መልዕክት ምን ላድርገው?", reply_markup=InlineKeyboardMarkup(keyboard))


def broadcast_message(context: CallbackContext, message_info: dict):
    channels = get_channels()
    if not channels:
        context.bot.send_message(chat_id=ADMIN_USER_ID, text="⚠️ ምንም የተመዘገበ ቻናል ስለሌለ መልዕክቱ አልተላከም።")
        return

    broadcast_id = str(uuid.uuid4())
    sent_messages, failed_count = [], 0
    
    for channel in channels:
        try:
            # ይህ ክፍል መልዕክቱን ከነሙሉ ይዘቱ (ከነበተኖቹ) ኮፒ ያደርጋል
            sent_msg = context.bot.copy_message(
                from_chat_id=message_info['chat_id'],
                message_id=message_info['message_id'],
                chat_id=channel
            )
            sent_messages.append({"chat_id": sent_msg.chat.id, "message_id": sent_msg.message_id})
        except Exception as e:
            logging.error(f"Failed to send to {channel}: {e}")
            failed_count += 1
            
    if sent_messages:
        kv.set(f"broadcast:{broadcast_id}", json.dumps(sent_messages), ex=604800) # Expire in 7 days
        kv.incr("wavebot:broadcasts")
        
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ ሁሉንም አጥፋ", callback_data=f"delete_{broadcast_id}")]])
        text = f"📡 መልዕክቱ ተልኳል!\n\n✅ ለ {len(sent_messages)} ቻናሎች።\n❌ ለ {failed_count} ቻናሎች አልተላከም።"
        
        context.bot.send_message(
            chat_id=ADMIN_USER_ID, 
            text=text,
            reply_markup=keyboard
        )
    else:
        text = f"📡 መልዕክቱ አልተላከም!\n\n❌ ለ {failed_count} ቻናሎች መላክ አልተቻለም።"
        context.bot.send_message(chat_id=ADMIN_USER_ID, text=text)

def button_callback_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    state = get_user_state(user_id)
    
    if data == "broadcast_now":
        query.answer()
        if state.get("action") == "confirm_broadcast":
            query.edit_message_text(text="✅ መልዕክቱ አሁኑኑ እየተላከ ነው...")
            broadcast_message(context, state['message_to_send'])
            clear_user_state(user_id)
        else:
            query.edit_message_text(text="❌ ጊዜው አልፎበታል። እባክዎ እንደገና ይሞክሩ።")

    elif data == "broadcast_schedule":
        query.answer()
        if state.get("action") == "confirm_broadcast":
            set_user_state(user_id, {"action": "awaiting_schedule_time"})
            query.edit_message_text(
                text="👍 መልዕክቱ ከስንት ጊዜ በኋላ ይላክ?\nምሳሌ: `10m`, `2h`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(text="❌ ጊዜው አልፎበታል። እባክዎ እንደገና ይሞክሩ።")
            
    elif data == "broadcast_cancel":
        query.answer()
        clear_user_state(user_id)
        query.edit_message_text(text="✅ የመላክ ስራው ተሰርዟል።")

    elif data.startswith("delete_"):
        query.answer()
        broadcast_id = data.split("_")[1]
        messages_to_delete_json = kv.get(f"broadcast:{broadcast_id}")
        
        if not messages_to_delete_json:
            query.edit_message_text(text="❌ ይቅርታ፣ ይህ መልዕክት ጊዜው አልፎበታል ወይም ቀድሞ ተሰርዟል።")
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

    elif data.startswith("cancel_scheduled_"):
        schedule_id_to_cancel = data.split("_")[2]
        scheduled_posts_json = kv.get("wavebot:scheduled_posts")
        posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
        
        updated_posts = [p for p in posts if p['schedule_id'] != schedule_id_to_cancel]
        
        if len(updated_posts) < len(posts):
            kv.set("wavebot:scheduled_posts", json.dumps(updated_posts))
            query.answer("✅ የታዘዘው መልዕክት ተሰርዟል።", show_alert=True)
            
            new_message = "🗒️ **የታዘዙ መልዕክቶች ዝርዝር:**\n\n"
            new_keyboard = []
            if not updated_posts:
                new_message = "✅ ስኬታማ! ሁሉም የታዘዙ መልዕክቶች ተሰርዘዋል።"
            else:
                for i, post in enumerate(updated_posts):
                    post_time_utc = datetime.fromisoformat(post['schedule_time_utc'])
                    post_time_local = post_time_utc + timedelta(hours=3)
                    new_message += f"**{i+1}.** 🕒 `{post_time_local.strftime('%Y-%m-%d %H:%M')}` ላይ ይላካል።\n"
                    new_keyboard.append([InlineKeyboardButton(f"❌ {i+1}ኛውን ሰርዝ", callback_data=f"cancel_scheduled_{post['schedule_id']}")])
            
            try:
                query.edit_message_text(text=new_message, reply_markup=InlineKeyboardMarkup(new_keyboard), parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                 logging.error(f"Error editing scheduled posts message: {e}")
        else:
            query.answer("🤔 ይቅርታ, ይህ መልዕክት አስቀድሞ ተልኳል ወይም ተሰርዟል።", show_alert=True)
            try:
                query.edit_message_text(text="🤷‍♂️ ምንም በጊዜ ቀጠሮ የተያዘ መልዕክት የለም።")
            except Exception:
                pass


def cron_job_runner():
    scheduled_posts_json = kv.get("wavebot:scheduled_posts")
    all_posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
    
    if not all_posts: return "No scheduled posts.", 200

    now_utc = datetime.utcnow()
    posts_to_send = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) <= now_utc]
    remaining_posts = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) > now_utc]
    
    if posts_to_send:
        # Create a dummy context for the broadcast function
        class DummyContext:
            def __init__(self, bot_instance):
                self.bot = bot_instance
        dummy_context = DummyContext(bot)
        
        for post in posts_to_send:
            try:
                broadcast_message(dummy_context, {
                    'chat_id': post['admin_chat_id'],
                    'message_id': post['message_id']
                })
            except Exception as e:
                logging.error(f"Error sending scheduled post {post['schedule_id']}: {e}")

        kv.set("wavebot:scheduled_posts", json.dumps(remaining_posts))
    
    return f"Processed {len(posts_to_send)} posts.", 200

# --- Dispatcher Setup ---
def is_admin(update: Update) -> bool:
    return update.effective_user.id == ADMIN_USER_ID

dispatcher.add_handler(CommandHandler("start", start_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("help", start_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("cancel", cancel_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("addchannel", add_channel_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("removechannel", remove_channel_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("listchannels", list_channels_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("stats", stats_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("schedule", schedule_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("scheduledposts", scheduled_posts_command, filters=Filters.private))
dispatcher.add_handler(CallbackQueryHandler(button_callback_handler))
dispatcher.add_handler(MessageHandler(Filters.private & ~Filters.command, process_message))

# --- Webhook Handler for Vercel ---
@app.route('/api', methods=['POST'])
def webhook_handler():
    if not kv: return 'error: database not configured', 500
    update = Update.de_json(request.get_json(force=True), bot)
    dispatcher.process_update(update)
    return 'ok'

@app.route('/api/cron', methods=['GET'])
def cron_handler():
    auth_header = request.headers.get('x-vercel-cron-authorization')
    if not auth_header or auth_header != f"Bearer {CRON_SECRET}":
        logging.warning("CRON: Unauthorized access attempt.")
        return "Unauthorized", 401
    
    if not kv: return 'error: database not configured', 500
    result, status_code = cron_job_runner()
    return result, status_code

@app.route('/')
def index():
    return 'Hello, Wave Bot is active!'
