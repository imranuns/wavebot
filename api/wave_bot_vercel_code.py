# -*- coding: utf-8 -*-
import logging
import json
import os
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse
from starlette.routing import Route

# --- Environment Variables ---
try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
    VERCEL_URL = os.environ.get("VERCEL_URL") # .get() makes it optional for local testing
except KeyError as e:
    logging.error(f"አስፈላጊ Environment Variable አልተገኘም: {e}.")
    # Set dummy values if running locally without a .env file
    if not os.environ.get("VERCEL"):
        BOT_TOKEN = "DUMMY_TOKEN"
        ADMIN_USER_ID = 123456789
        VERCEL_URL = None
    else:
        raise RuntimeError(f"Vercel ላይ Environment Variable '{e}' አልተገኘም።")

WEBHOOK_URL = f"https://{VERCEL_URL}" if VERCEL_URL else None

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Persistence Path ---
# ⚠️ Vercel's /tmp is the only writable directory and it's ephemeral.
DATA_PATH = Path("/tmp") / "bot_data.pickle" if os.environ.get("VERCEL") else Path("data") / "bot_data.pickle"
DATA_PATH.parent.mkdir(exist_ok=True)

# --- Conversation States ---
(
    ADD_CHANNEL_NAME,
    # Add other states if you revive more complex conversations
) = range(1)

# --- Helper & Command Functions ---
async def is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id == ADMIN_USER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    await update.message.reply_html(
        "👋 <b>ሰላም! ወደ Wave Bot እንኳን በደህና መጣህ።</b>\n\n"
        "ቻናሎችህን ለማስተዳደር ዝግጁ ነኝ። ለመጀመር /help የሚለውን ትዕዛዝ ተጠቀም።"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    help_text = """
    <b>Wave Bot Command List</b>

    <b>Channel Management</b>
    /addchannel - አዲስ ቻናል ለመጨመር
    /removechannel - ቻናል ለማስወገድ
    /listchannels - የተመዘገቡ ቻናሎችን ለማየት

    <b>Broadcasting</b>
    - ማንኛውንም መልዕክት (ጽሑፍ, ፎቶ, ወዘተ) በቀጥታ ለቦቱ በመላክ በሁሉም ቻናሎች ላይ እንዲለጠፍ ማድረግ ትችላለህ።
    """
    await update.message.reply_html(help_text)

async def add_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update): return ConversationHandler.END
    await update.message.reply_text("እባክህ የቻናሉን username አስገባ (ለምሳሌ፡ @mychannel)።\nለማቋረጥ /cancel ጻፍ።")
    return ADD_CHANNEL_NAME

async def add_channel_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel = update.message.text.strip()
    if not channel.startswith('@'):
        await update.message.reply_text("የተሳሳተ ፎርማት ነው። እባክህ በ '@' ጀምር።")
        return ADD_CHANNEL_NAME

    if 'channels' not in context.bot_data:
        context.bot_data['channels'] = []

    if channel in context.bot_data['channels']:
        await update.message.reply_text(f"'{channel}' አስቀድሞ ተመዝግቧል።")
    else:
        context.bot_data['channels'].append(channel)
        await update.message.reply_text(f"✅ '{channel}' በተሳካ ሁኔታ ተመዝግቧል።")

    return ConversationHandler.END

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    channels = context.bot_data.get('channels', [])
    if not channels:
        await update.message.reply_text("ምንም የተመዘገበ ቻናል የለም።")
        return
    
    message = "<b>የተመዘገቡ ቻናሎች ዝርዝር:</b>\n\n" + "\n".join(channels)
    await update.message.reply_html(message)

# This needs a conversation handler too to get which channel to remove
async def remove_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
     if not await is_admin(update): return
     # Simplified for now, a full implementation would use a conversation
     args = context.args
     if not args:
         await update.message.reply_text("እባክህ ለማስወገድ የምትፈልገውን ቻናል ስም አስገባ። \nለምሳሌ፡ /removechannel @mychannel")
         return

     channel_to_remove = args[0]
     channels = context.bot_data.get('channels', [])
     if channel_to_remove in channels:
         channels.remove(channel_to_remove)
         context.bot_data['channels'] = channels # Resave the list
         await update.message.reply_text(f"🗑️ '{channel_to_remove}' ከዝርዝሩ ተወግዷል።")
     else:
         await update.message.reply_text(f"'{channel_to_remove}' በዝርዝሩ ውስጥ የለም።")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("ሂደቱ ተሰርዟል።")
    return ConversationHandler.END

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update) or not update.message: return

    channels = context.bot_data.get('channels', [])
    if not channels:
        await update.message.reply_text("ምንም የተመዘገበ ቻናል የለም። ለመጨመር /addchannel ተጠቀም።")
        return

    sent_count = 0
    failed_count = 0
    status_message = await update.message.reply_text(" Broadcasting... 📡")

    for channel in channels:
        try:
            await update.message.copy(chat_id=channel)
            sent_count += 1
            await asyncio.sleep(0.5) # Avoid Telegram's rate limits
        except Exception as e:
            logger.error(f"'{channel}' ላይ መላክ አልተቻለም: {e}")
            failed_count += 1
    
    await status_message.edit_text(
        f"📡 መልዕክቱ ተልኳል!\n\n"
        f"✅ በ {sent_count} ቻናሎች ላይ ተለጥፏል።\n"
        f"❌ በ {failed_count} ቻናሎች ላይ አልተለጠፈም።"
    )

# --- Application and Webhook Setup ---
persistence = PicklePersistence(filepath=DATA_PATH)
application = Application.builder().token(BOT_TOKEN).persistence(persistence).build()

# Add handlers
add_channel_handler = ConversationHandler(
    entry_points=[CommandHandler("addchannel", add_channel_start)],
    states={ADD_CHANNEL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_received)]},
    fallbacks=[CommandHandler("cancel", cancel)],
)
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(add_channel_handler)
application.add_handler(CommandHandler("listchannels", list_channels))
application.add_handler(CommandHandler("removechannel", remove_channel_command))
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast))


# --- Starlette Web Server for Vercel ---
async def main():
    """Sets the webhook and prepares the application."""
    if WEBHOOK_URL:
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/api/webhook")
        logger.info(f"Webhook has been set to {WEBHOOK_URL}/api/webhook")
    else:
        logger.warning("VERCEL_URL is not set. Webhook not configured.")

async def root_path(request: Request):
    """Handles GET requests to the root URL to show a status page."""
    return PlainTextResponse("✅ Wave Bot is running. Please interact with it on Telegram.")

async def webhook_update(request: Request) -> JSONResponse:
    """Handles incoming webhook updates from Telegram."""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return JSONResponse({'status': 'ok'})
    except Exception:
        logger.exception("Error processing update:")
        return JSONResponse({'status': 'error'}, status_code=500)

# Vercel needs a callable named 'app'
app = Starlette(
    routes=[
        Route("/", endpoint=root_path, methods=["GET"]),
        Route("/api/webhook", endpoint=webhook_update, methods=["POST"]),
    ],
    on_startup=[main]
)
