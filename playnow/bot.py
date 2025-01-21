import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv
import re
from datetime import datetime
from typing import List, Dict
import asyncio
import sys
import aiosqlite
from telethon import TelegramClient, events
from telethon.tl.functions.messages import GetHistoryRequest
import json

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Create logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Create console handler with a higher log level
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Freepik URL pattern
FREEPIK_PATTERN = re.compile(r'https?://(?:www\.)?freepik\.com/[^\s]+')

# Database path
DB_PATH = "bot_data.db"

# Telethon client for accessing groups
api_id = os.getenv('API_ID')
api_hash = os.getenv('API_HASH')
client = TelegramClient('bot_session', api_id, api_hash)

async def init_db():
    """Initialize the database."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS groups (
                group_id TEXT PRIMARY KEY,
                group_name TEXT,
                added_by TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS links (
                link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                url TEXT,
                found_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (group_id) REFERENCES groups (group_id)
            )
        ''')
        await db.commit()

def get_main_keyboard():
    """Get the main keyboard markup."""
    keyboard = [
        [
            InlineKeyboardButton("🔍 البحث في الروابط", callback_data='search'),
            InlineKeyboardButton("📋 آخر الروابط", callback_data='recent')
        ],
        [
            InlineKeyboardButton("💾 حفظ الروابط", callback_data='save'),
            InlineKeyboardButton("↗️ مشاركة رابط", callback_data='share')
        ],
        [
            InlineKeyboardButton("👥 إدارة المجموعات", callback_data='manage_groups')
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the command /start is issued."""
    logger.info(f"Start command received from user {update.effective_user.id}")
    welcome_message = (
        'مرحباً! أنا بوت مساعد لجمع روابط Freepik.\n'
        'سأقوم بمراقبة المجموعات وجمع روابط Freepik التي يتم مشاركتها.\n'
        'اختر من الخيارات التالية:'
    )
    try:
        await update.message.reply_text(welcome_message, reply_markup=get_main_keyboard())
        logger.info(f"Welcome message sent to user {update.effective_user.id}")
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")
        await update.message.reply_text("حدث خطأ، الرجاء المحاولة مرة أخرى.")

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a group to monitor."""
    if not context.args:
        await update.message.reply_text(
            "الرجاء إدخال رابط المجموعة.\nمثال: /add_group https://t.me/group_name"
        )
        return

    group_link = context.args[0]
    try:
        # Extract group username from link
        group_username = group_link.split('/')[-1]
        
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'INSERT OR REPLACE INTO groups (group_id, group_name, added_by) VALUES (?, ?, ?)',
                (group_username, group_username, str(update.effective_user.id))
            )
            await db.commit()
        
        await update.message.reply_text(f"تمت إضافة المجموعة {group_username} بنجاح!")
        
        # Start monitoring the group
        await monitor_group(group_username)
        
    except Exception as e:
        logger.error(f"Error adding group: {e}")
        await update.message.reply_text("حدث خطأ أثناء إضافة المجموعة. الرجاء المحاولة مرة أخرى.")

async def remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a group from monitoring."""
    if not context.args:
        await update.message.reply_text(
            "الرجاء إدخال معرف المجموعة.\nمثال: /remove_group group_name"
        )
        return

    group_id = context.args[0]
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('DELETE FROM groups WHERE group_id = ?', (group_id,))
            await db.commit()
        
        await update.message.reply_text(f"تمت إزالة المجموعة {group_id} بنجاح!")
    except Exception as e:
        logger.error(f"Error removing group: {e}")
        await update.message.reply_text("حدث خطأ أثناء إزالة المجموعة. الرجاء المحاولة مرة أخرى.")

async def list_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all monitored groups."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT group_id, added_at FROM groups') as cursor:
                groups = await cursor.fetchall()
        
        if not groups:
            await update.message.reply_text("لا توجد مجموعات مضافة حالياً.")
            return
            
        response = "المجموعات المراقبة:\n\n"
        for group_id, added_at in groups:
            response += f"• {group_id} (أضيفت في {added_at})\n"
        
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error listing groups: {e}")
        await update.message.reply_text("حدث خطأ أثناء عرض المجموعات. الرجاء المحاولة مرة أخرى.")

async def monitor_group(group_username: str):
    """Monitor a group for Freepik links."""
    try:
        async with client:
            # Get the entity (group) information
            entity = await client.get_entity(group_username)
            
            # Get the last 100 messages
            messages = await client(GetHistoryRequest(
                peer=entity,
                limit=100,
                offset_date=None,
                offset_id=0,
                max_id=0,
                min_id=0,
                add_offset=0,
                hash=0
            ))
            
            # Process messages
            for message in messages.messages:
                if message.message:
                    freepik_urls = FREEPIK_PATTERN.findall(message.message)
                    if freepik_urls:
                        # Save links to database
                        async with aiosqlite.connect(DB_PATH) as db:
                            for url in freepik_urls:
                                await db.execute(
                                    'INSERT INTO links (group_id, url) VALUES (?, ?)',
                                    (group_username, url)
                                )
                            await db.commit()
            
            # Set up event handler for new messages
            @client.on(events.NewMessage(chats=group_username))
            async def handler(event):
                if event.message.message:
                    freepik_urls = FREEPIK_PATTERN.findall(event.message.message)
                    if freepik_urls:
                        async with aiosqlite.connect(DB_PATH) as db:
                            for url in freepik_urls:
                                await db.execute(
                                    'INSERT INTO links (group_id, url) VALUES (?, ?)',
                                    (group_username, url)
                                )
                            await db.commit()
            
    except Exception as e:
        logger.error(f"Error monitoring group {group_username}: {e}")

async def search_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Search for links in the database."""
    if not context.args:
        await update.message.reply_text(
            "الرجاء إدخال كلمة البحث.\nمثال: /search logo"
        )
        return
        
    search_term = ' '.join(context.args).lower()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                'SELECT url, group_id, found_at FROM links WHERE url LIKE ? ORDER BY found_at DESC LIMIT 10',
                (f'%{search_term}%',)
            ) as cursor:
                results = await cursor.fetchall()
        
        if not results:
            await update.message.reply_text(f"لم يتم العثور على نتائج لـ '{search_term}'")
            return
            
        response = f"نتائج البحث عن '{search_term}':\n\n"
        for url, group_id, found_at in results:
            response += f"🔗 {url}\n📱 من مجموعة: {group_id}\n⏰ في: {found_at}\n\n"
        
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error searching links: {e}")
        await update.message.reply_text("حدث خطأ أثناء البحث. الرجاء المحاولة مرة أخرى.")

async def recent_links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent links from all groups."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                'SELECT url, group_id, found_at FROM links ORDER BY found_at DESC LIMIT 5'
            ) as cursor:
                results = await cursor.fetchall()
        
        if not results:
            await update.message.reply_text("لا توجد روابط محفوظة بعد.")
            return
            
        response = "آخر الروابط المضافة:\n\n"
        for url, group_id, found_at in results:
            response += f"🔗 {url}\n📱 من مجموعة: {group_id}\n⏰ في: {found_at}\n\n"
        
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error fetching recent links: {e}")
        await update.message.reply_text("حدث خطأ أثناء جلب الروابط. الرجاء المحاولة مرة أخرى.")

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks."""
    query = update.callback_query
    try:
        await query.answer()
        logger.info(f"Button click from user {update.effective_user.id}: {query.data}")
        
        if query.data == 'recent':
            await recent_links(update, context)
        
        elif query.data == 'search':
            await update.callback_query.message.reply_text(
                "للبحث في الروابط، أرسل كلمة البحث مع الأمر /search\n"
                "مثال: /search logo",
                reply_markup=get_main_keyboard()
            )
        
        elif query.data == 'save':
            await update.callback_query.message.reply_text(
                "لم يتم تحديد خيار الحفظ بعد.",
                reply_markup=get_main_keyboard()
            )
        
        elif query.data == 'share':
            await update.callback_query.message.reply_text(
                "لم يتم تحديد خيار المشاركة بعد.",
                reply_markup=get_main_keyboard()
            )
        
        elif query.data == 'manage_groups':
            await update.callback_query.message.reply_text(
                "لإدارة المجموعات، استخدم الأوامر التالية:\n"
                "/add_group <رابط المجموعة>\n"
                "/remove_group <معرف المجموعة>\n"
                "/list_groups",
                reply_markup=get_main_keyboard()
            )
            
    except Exception as e:
        logger.error(f"Error handling button click: {e}")
        await query.message.reply_text("حدث خطأ، الرجاء المحاولة مرة أخرى.")

async def main():
    """Start the bot."""
    try:
        # Initialize database
        await init_db()
        
        # Get the token from environment variable
        token = os.getenv('TELEGRAM_TOKEN')
        if not token:
            logger.error("No token found! Please set the TELEGRAM_TOKEN environment variable.")
            return

        logger.info(f"Starting bot with token: {token[:10]}...")

        # Create the Application
        application = Application.builder().token(token).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("add_group", add_group))
        application.add_handler(CommandHandler("remove_group", remove_group))
        application.add_handler(CommandHandler("list_groups", list_groups))
        application.add_handler(CommandHandler("search", search_links))
        application.add_handler(CommandHandler("recent", recent_links))
        application.add_handler(CallbackQueryHandler(button_click))
        
        # Start monitoring existing groups
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute('SELECT group_id FROM groups') as cursor:
                groups = await cursor.fetchall()
                for group_id, in groups:
                    await monitor_group(group_id)

        # Start the bot
        logger.info("Starting polling...")
        await application.initialize()
        await application.start()
        await application.run_polling(allowed_updates=Update.ALL_TYPES)
        
    except Exception as e:
        logger.error(f"Critical error: {e}")
    finally:
        logger.info("Stopping bot...")
        await application.stop()

def run_bot():
    """Run the bot with proper asyncio handling."""
    try:
        if sys.platform == "win32" and sys.version_info[0] == 3 and sys.version_info[1] >= 8:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        logger.info("Starting bot process...")
        asyncio.run(main())
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Error running bot: {e}")

if __name__ == '__main__':
    run_bot()
