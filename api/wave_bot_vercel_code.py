# -*- coding: utf-8 -*-
import logging
import os
import asyncio
from pathlib import Path
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
    PicklePersistence,
)
# starlette የድር ጥያቄዎችን ለማስተናገድ የሚረዳ ቀላል framework ነው
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# --- 1. Environment Variables ---
# እነዚህ በ Vercel Project Settings ውስጥ መሞላት አለባቸው
try:
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
except KeyError as e:
    raise RuntimeError(f"አስፈላጊ Environment Variable አልተገኘም: {e}")

# --- 2. Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 3. Persistence Path ---
# Vercel መጻፍ የሚፈቅደው /tmp ፎልደር ላይ ብቻ ነው
DATA_PATH = Path("/tmp") / "bot_data.pickle"
DATA_PATH.parent.mkdir(exist_ok=True)

# --- 4. Conversation States ---
ADD_CHANNEL_NAME, REMOVE_CHANNEL_NAME = range(2)

# --- 5. Bot Logic and Command Handlers ---
async def is_admin(update: Update) -> bool:
    """ተጠቃሚው አስተዳዳሪ መሆኑን ያረጋግጣል"""
    return update.effective_user and update.effective_user.id == ADMIN_USER_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    await update.message.reply_html(
        "👋 <b>ሰላም! ወደ Wave Bot እንኳን በደህና መጣህ።</b>\n\n"
        "ይህ ቦት ለ Vercel ዝግጁ ነው። ለመጀመር /help የሚለውን ተጠቀም።"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    help_text = (
        "<b>Wave Bot የትዕዛዝ ዝርዝር</b>\n\n"
        "<b>የቻናል አስተዳደር:</b>\n"
        "/addchannel - አዲስ ቻናል ለመጨመር\n"
        "/removechannel - ቻናል ለማስወገድ\n"
        "/listchannels - የተመዘገቡ ቻናሎችን ለማየት\n\n"
        "<b>ማስታወቂያ መላክ:</b>\n"
        "ማንኛውንም መልዕክት (ጽሑፍ, ፎቶ, ወዘተ) በቀጥታ ለቦቱ በመላክ በሁሉም ቻናሎች ላይ እንዲለጠፍ ማድረግ ትችላለህ።"
    )
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
    context.bot_data.setdefault('channels', []).append(channel)
    context.bot_data['channels'] = sorted(list(set(context.bot_data['channels']))) # ድግግሞሽን ለማስወገድ
    await update.message.reply_text(f"✅ '{channel}' በተሳካ ሁኔታ ተመዝግቧል።")
    return ConversationHandler.END

async def remove_channel_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not await is_admin(update): return ConversationHandler.END
    await update.message.reply_text("እባክህ ለማስወገድ የምትፈልገውን የቻናል username አስገባ (ለምሳሌ፡ @mychannel)።")
    return REMOVE_CHANNEL_NAME

async def remove_channel_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    channel_to_remove = update.message.text.strip()
    channels = context.bot_data.get('channels', [])
    if channel_to_remove in channels:
        channels.remove(channel_to_remove)
        await update.message.reply_text(f"🗑️ '{channel_to_remove}' ከዝርዝሩ ተወግዷል።")
    else:
        await update.message.reply_text(f"'{channel_to_remove}' በዝርዝሩ ውስጥ የለም።")
    return ConversationHandler.END

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update): return
    channels = context.bot_data.get('channels', [])
    if not channels:
        await update.message.reply_text("ምንም የተመዘገበ ቻናል የለም።")
        return
    message = "<b>የተመዘገቡ ቻናሎች ዝርዝር:</b>\n\n" + "\n".join(channels)
    await update.message.reply_html(message)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("ሂደቱ ተሰርዟል።")
    return ConversationHandler.END

async def handle_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await is_admin(update) or not update.message: return
    channels = context.bot_data.get('channels', [])
    if not channels:
        await update.message.reply_text("ምንም የተመዘገበ ቻናል የለም። /addchannel ተጠቀም።")
        return
    sent_count, failed_count = 0, 0
    status_message = await update.message.reply_text(f"📡 Broadcasting to {len(channels)} channels...")
    for channel in channels:
        try:
            await update.message.copy(chat_id=channel)
            sent_count += 1
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Failed to send to '{channel}': {e}")
            failed_count += 1
    await status_message.edit_text(
        f"📡 መልዕክቱ ተልኳል!\n\n✅ በ {sent_count} ቻናሎች ላይ ተለጥፏል።\n❌ በ {failed_count} ቻናሎች ላይ አልተለጠፈም።"
    )

# --- 6. Application Setup ---
persistence = PicklePersistence(filepath=DATA_PATH)
application = (
    Application.builder()
    .token(BOT_TOKEN)
    .persistence(persistence)
    .build()
)

# Handlers
add_handler = ConversationHandler(
    entry_points=[CommandHandler("addchannel", add_channel_start)],
    states={ADD_CHANNEL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_received)]},
    fallbacks=[CommandHandler("cancel", cancel)],
)
remove_handler = ConversationHandler(
    entry_points=[CommandHandler("removechannel", remove_channel_start)],
    states={REMOVE_CHANNEL_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_channel_received)]},
    fallbacks=[CommandHandler("cancel", cancel)],
)
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(add_handler)
application.add_handler(remove_handler)
application.add_handler(CommandHandler("listchannels", list_channels))
application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_broadcast))

# --- 7. Vercel Webhook Handler ---
# Vercel ይህንን ዋና function ነው የሚያስኬደው
async def webhook(request: Request) -> JSONResponse:
    """ከቴሌግራም የሚመጡ webhook ጥያቄዎችን ያስተናግዳል"""
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return JSONResponse({'status': 'ok'})
    except Exception as e:
        logger.error(f"Error processing update: {e}", exc_info=True)
        return JSONResponse({'status': 'error'}, status_code=500)

# Vercel 'app' የሚባል function ይፈልጋል
app = Starlette(
    routes=[
        Route("/", endpoint=webhook, methods=["POST"]),
    ]
)
