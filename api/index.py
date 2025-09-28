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

# --- State Management & Data Helper Functions ---
def get_user_state(user_id):
    state_json = kv.get(f"state:{user_id}")
    return json.loads(state_json) if state_json else {}

def set_user_state(user_id, state_data):
    current_state = get_user_state(user_id)
    current_state.update(state_data)
    kv.set(f"state:{user_id}", json.dumps(current_state), ex=600) # Expire in 10 mins

def clear_user_state(user_id):
    kv.delete(f"state:{user_id}")
    
def parse_relative_time(time_str: str) -> timedelta or None:
    match = re.match(r"(\d+)([mhd])", time_str.lower())
    if not match: return None
    value, unit = int(match.group(1)), match.group(2)
    if unit == 'm': return timedelta(minutes=value)
    if unit == 'h': return timedelta(hours=value)
    if unit == 'd': return timedelta(days=value)
    return None

def parse_datetime_eat(datetime_str: str) -> datetime or None:
    now_utc = datetime.utcnow()
    now_eat = now_utc + timedelta(hours=3)
    
    cleaned_str = datetime_str.strip()
    
    target_date = now_eat.date()
    date_provided = False

    date_patterns = [
        (r"(\d{1,2})/(\d{1,2})/(\d{4})", "%m/%d/%Y"),
        (r"(\d{4})-(\d{1,2})-(\d{1,2})", "%Y-%m-%d"),
    ]
    
    for pattern, date_format in date_patterns:
        match = re.search(pattern, cleaned_str)
        if match:
            date_str = match.group(0)
            try:
                target_date = datetime.strptime(date_str, date_format).date()
                cleaned_str = cleaned_str.replace(date_str, "").strip()
                date_provided = True
                break
            except ValueError:
                continue

    if not cleaned_str: return None
    
    time_str_cleaned = cleaned_str.lower().replace(" ", "")
    hour, minute = None, None
    
    match_ampm = re.match(r"(\d{1,2}):(\d{2})(am|pm)", time_str_cleaned)
    if match_ampm:
        h, m, period = int(match_ampm.group(1)), int(match_ampm.group(2)), match_ampm.group(3)
        if not (1 <= h <= 12 and 0 <= m <= 59): return None
        if period == "pm" and h != 12: hour = h + 12
        elif period == "am" and h == 12: hour = 0
        else: hour = h
        minute = m
    else:
        match_24h = re.match(r"(\d{1,2}):(\d{2})", time_str_cleaned)
        if not match_24h: return None
        h, m = int(match_24h.group(1)), int(match_24h.group(2))
        if not (0 <= h <= 23 and 0 <= m <= 59): return None
        hour, minute = h, m
            
    if hour is None: return None

    try:
        schedule_datetime_eat = datetime.combine(target_date, datetime.min.time()).replace(hour=hour, minute=minute)
    except ValueError:
        return None

    if not date_provided and schedule_datetime_eat <= now_eat:
        schedule_datetime_eat += timedelta(days=1)
    elif schedule_datetime_eat <= now_eat:
        return None

    return schedule_datetime_eat - timedelta(hours=3)


def get_channels() -> list:
    channels_json = kv.get("wavebot:channels")
    return json.loads(channels_json) if channels_json else []

def save_channels(channels: list):
    kv.set("wavebot:channels", json.dumps(channels))
    
def extract_message_data(message):
    reply_markup_json = message.reply_markup.to_json() if message.reply_markup else None
    return {
        'text': message.text_html,
        'caption': message.caption_html,
        'photo_file_id': message.photo[-1].file_id if message.photo else None,
        'video_file_id': message.video.file_id if message.video else None,
        'document_file_id': message.document.file_id if message.document else None,
        'reply_markup_json': reply_markup_json
    }

# --- Command Handlers ---
def start_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    user_name = update.effective_user.first_name
    clear_user_state(update.effective_user.id)
    
    text = (f"ሰላም {user_name}! ወደ ቦትህ እንኳን በደህና መጣህ።\n\n"
            "**ዋና ዋና ትዕዛዞች:**\n"
            "📢 `ማስታወቂያ ለመላክ:` ማንኛውንም መልዕክት በቀጥታ ላክልኝ።\n\n"
            "**ቻናል ማስተዳደሪያ:**\n"
            "➕ `/addchannel @username`\n"
            "➖ `/removechannel @username`\n"
            "📋 `/listchannels`\n\n"
            "**Watermark (የምርት ምልክት):**\n"
            "✍️ `/set_watermark ጽሑፍ`\n"
            "👀 `/view_watermark`\n"
            "🗑️ `/remove_watermark`\n\n"
            "**የጊዜ ሰሌዳ ማስተዳደሪያ:**\n"
            "⏰ `/schedule`\n"
            "🗒️ `/scheduledposts`\n\n"
            "**ተጨማሪ ትዕዛዞች:**\n"
            "📊 `/stats`\n"
            "ℹ️ `/help`")
           
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

def set_watermark_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    if not context.args:
        update.message.reply_text("❌ እባክዎ እንዲህ ይጠቀሙ: /set_watermark የእርስዎ ጽሑፍ እዚህ")
        return
    watermark_text = " ".join(context.args)
    kv.set("wavebot:watermark", watermark_text)
    update.message.reply_text(f"✅ Watermark በተሳካ ሁኔታ ተቀምጧል:\n\n`{watermark_text}`", parse_mode=ParseMode.MARKDOWN)

def view_watermark_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    watermark = kv.get("wavebot:watermark")
    if watermark:
        update.message.reply_text(f"👀 አሁን ያለው Watermark:\n\n`{watermark.decode('utf-8')}`", parse_mode=ParseMode.MARKDOWN)
    else:
        update.message.reply_text("🤷‍♂️ ምንም የተቀመጠ Watermark የለም።")

def remove_watermark_command(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    if kv.exists("wavebot:watermark"):
        kv.delete("wavebot:watermark")
        update.message.reply_text("🗑️ Watermark በተሳካ ሁኔታ ተወግዷል።")
    else:
        update.message.reply_text("🤷‍♂️ ምንም የተቀመጠ Watermark የለም።")

def schedule_command(update: Update, context: CallbackContext):
    if not is_admin(update): return
    set_user_state(update.effective_user.id, {"action": "awaiting_schedule_time"})
    update.message.reply_text(
        "👍 መልዕክቱ መቼ ይላክ?\n"
        "ምሳሌ: `9/28/2025 1:30 pm`, `10:00`, or `2h`\n\n"
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
        message += f"**{i+1}.** 🕒 `{post_time_local.strftime('%Y-%m-%d %I:%M %p')}` ላይ ይላካል።\n"
        keyboard.append([InlineKeyboardButton(f"❌ {i+1}ኛውን ሰርዝ", callback_data=f"cancel_scheduled_{post['schedule_id']}")])
    update.message.reply_text(message, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


def process_message(update: Update, context: CallbackContext):
    if not is_admin(update) or not kv: return
    user_id = update.effective_user.id
    state = get_user_state(user_id)
    action = state.get("action")

    if action == "awaiting_schedule_time":
        time_str = update.message.text
        future_time = parse_datetime_eat(time_str)
        
        if not future_time:
            delay = parse_relative_time(time_str)
            if delay:
                future_time = datetime.utcnow() + delay
        
        if not future_time:
            update.message.reply_text("❌ የተሳሳተ የጊዜ አጻጻፍ! እባክዎ እንዲህ ይጠቀሙ: `9/28/2025 1:30 pm`, `10:00`, or `2h`።", parse_mode=ParseMode.MARKDOWN)
            return

        set_user_state(user_id, {"action": "awaiting_schedule_message", "schedule_time_utc": future_time.isoformat()})
        update.message.reply_text("✅ ጥሩ! አሁን እንዲላክልህ የምትፈልገውን መልዕክት ላክልኝ።")

    elif action == "awaiting_schedule_message":
        scheduled_posts_json = kv.get("wavebot:scheduled_posts")
        posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
        new_post = {
            "schedule_id": str(uuid.uuid4()),
            "message_data": extract_message_data(update.message),
            "schedule_time_utc": state["schedule_time_utc"]
        }
        posts.append(new_post)
        kv.set("wavebot:scheduled_posts", json.dumps(posts))
        clear_user_state(user_id)
        update.message.reply_text("✅ መልዕክትህ በተሳካ ሁኔታ ለበኋላ እንዲላክ ታዟል።")

    else:
        set_user_state(user_id, {
            "action": "confirm_broadcast",
            "message_to_send": extract_message_data(update.message)
        })
        keyboard = [[InlineKeyboardButton("✅ አሁኑኑ ላክ", callback_data="broadcast_now")],
                    [InlineKeyboardButton("⏰ በጊዜ ቀጠሮ አስቀምጥ", callback_data="broadcast_schedule")],
                    [InlineKeyboardButton("❌ ሰርዝ", callback_data="broadcast_cancel")]]
        update.message.reply_text("ይህንን መልዕክት ምን ላድርገው?", reply_markup=InlineKeyboardMarkup(keyboard))


def broadcast_message(context: CallbackContext, message_data: dict):
    channels = get_channels()
    if not channels:
        context.bot.send_message(chat_id=ADMIN_USER_ID, text="⚠️ ምንም የተመዘገበ ቻናል ስለሌለ መልዕክቱ አልተላከም።")
        return

    # --- Apply Watermark ---
    watermark_bytes = kv.get("wavebot:watermark")
    if watermark_bytes:
        watermark_text = f"\n\n{watermark_bytes.decode('utf-8')}"
        
        # We need to handle HTML entities in the watermark
        # A simple replacement, more complex parsing might be needed for full support
        watermark_text_html = watermark_text.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")

        if message_data['caption']:
            message_data['caption'] += watermark_text_html
        elif message_data['text']:
             message_data['text'] += watermark_text_html
        else: # For media with no caption
            message_data['caption'] = watermark_text_html.strip()

    broadcast_id = str(uuid.uuid4())
    sent_messages, failed_channels = [], []
    
    reply_markup = None
    if message_data.get('reply_markup_json'):
        try:
            reply_markup = InlineKeyboardMarkup.de_json(json.loads(message_data['reply_markup_json']), context.bot)
        except Exception as e:
            logging.error(f"Error deserializing reply_markup: {e}")

    for channel in channels:
        try:
            sent_msg = None
            if message_data.get('photo_file_id'):
                sent_msg = context.bot.send_photo(chat_id=channel, photo=message_data['photo_file_id'], caption=message_data['caption'], reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            elif message_data.get('video_file_id'):
                sent_msg = context.bot.send_video(chat_id=channel, video=message_data['video_file_id'], caption=message_data['caption'], reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            elif message_data.get('document_file_id'):
                sent_msg = context.bot.send_document(chat_id=channel, document=message_data['document_file_id'], caption=message_data['caption'], reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            elif message_data.get('text'):
                sent_msg = context.bot.send_message(chat_id=channel, text=message_data['text'], reply_markup=reply_markup, parse_mode=ParseMode.HTML)
            
            if sent_msg:
                sent_messages.append({"chat_id": sent_msg.chat.id, "message_id": sent_msg.message_id})
            else:
                logging.warning(f"Message type not supported for channel {channel}")
                failed_channels.append(channel)
        except Exception as e:
            logging.error(f"Failed to send to {channel}: {e}")
            failed_channels.append(channel)
            
    if sent_messages:
        kv.set(f"broadcast:{broadcast_id}", json.dumps(sent_messages), ex=604800) # Keep for 7 days
        kv.incr("wavebot:broadcasts")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🗑️ ሁሉንም አጥፋ", callback_data=f"delete_{broadcast_id}")]])
        
        text = f"📡 **መልዕክቱ ተልኳል!**\n\n✅ ለ `{len(sent_messages)}` ቻናሎች።"
        if failed_channels:
            text += f"\n❌ ለ `{len(failed_channels)}` ቻናሎች አልተላከም።"
            text += "\n\n**ያልተላከባቸው ዝርዝር:**\n" + "\n".join(f"- `{ch}`" for ch in failed_channels)

        context.bot.send_message(chat_id=ADMIN_USER_ID, text=text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    else:
        text = f"📡 **መልዕክቱ አልተላከም!**"
        if failed_channels:
             text += f"\n\n❌ ለ `{len(failed_channels)}` ቻናሎች መላክ አልተቻለም።"
             text += "\n\n**ያልተላከባቸው ዝርዝር:**\n" + "\n".join(f"- `{ch}`" for ch in failed_channels)
        context.bot.send_message(chat_id=ADMIN_USER_ID, text=text, parse_mode=ParseMode.MARKDOWN)


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
                text="👍 መልዕክቱ መቼ ይላክ?\nምሳሌ: `9/28/2025 1:30 pm`, `10:00`, or `2h`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            query.edit_message_text(text="❌ ጊዜው አልፎበታል። እባክዎ እንደገና ይሞክሩ።")
            
    elif data == "broadcast_cancel":
        query.answer()
        clear_user_state(user_id)
        query.edit_message_text(text="✅ የመላክ ስራው ተሰርዟል።")

    elif data.startswith("delete_"):
        query.answer("ትዕዛዝዎ እየተፈጸመ ነው...", show_alert=False)
        broadcast_id = data.split("_")[1]
        messages_to_delete_json = kv.get(f"broadcast:{broadcast_id}")
        
        if not messages_to_delete_json:
            query.edit_message_text(text="❌ ይቅርታ፣ ይህ መልዕክት ጊዜው አልፎበታል ወይም ቀድሞ ተሰрዟል።")
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
                    new_message += f"**{i+1}.** 🕒 `{post_time_local.strftime('%Y-%m-%d %I:%M %p')}` ላይ ይላካል።\n"
                    new_keyboard.append([InlineKeyboardButton(f"❌ {i+1}ኛውን ሰርዝ", callback_data=f"cancel_scheduled_{post['schedule_id']}")])
            try:
                query.edit_message_text(text=new_message, reply_markup=InlineKeyboardMarkup(new_keyboard), parse_mode=ParseMode.MARKDOWN)
            except Exception: pass
        else:
            query.answer("🤔 ይቅርታ, ይህ መልዕክት አስቀድሞ ተልኳል ወይም ተሰርዟል።", show_alert=True)
            try: query.edit_message_text(text="🤷‍♂️ ምንም በጊዜ ቀጠሮ የተያዘ መልዕክት የለም።")
            except Exception: pass


def cron_job_runner():
    scheduled_posts_json = kv.get("wavebot:scheduled_posts")
    all_posts = json.loads(scheduled_posts_json) if scheduled_posts_json else []
    
    if not all_posts: return "No scheduled posts.", 200

    now_utc = datetime.utcnow()
    posts_to_send = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) <= now_utc]
    remaining_posts = [p for p in all_posts if datetime.fromisoformat(p['schedule_time_utc']) > now_utc]
    
    if posts_to_send:
        class DummyContext:
            def __init__(self, bot_instance): self.bot = bot_instance
        dummy_context = DummyContext(bot)
        
        for post in posts_to_send:
            try:
                broadcast_message(dummy_context, post['message_data'])
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
dispatcher.add_handler(CommandHandler("set_watermark", set_watermark_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("view_watermark", view_watermark_command, filters=Filters.private))
dispatcher.add_handler(CommandHandler("remove_watermark", remove_watermark_command, filters=Filters.private))
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
