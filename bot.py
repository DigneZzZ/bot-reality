from typing import Optional, Any, Union, List
import asyncio
import json
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, FSInputFile, Message, CallbackQuery
from aiogram.enums import ChatType
import os
import redis.asyncio as redis
from redis_queue import enqueue
from time import time
import re
from urllib.parse import urlparse, quote, unquote
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta

# --- Conditional Imports ---
try:
    from progress_tracker import BatchProcessor
    PROGRESS_AVAILABLE = True
except ImportError:
    PROGRESS_AVAILABLE = False

try:
    from analytics import AnalyticsCollector
    ANALYTICS_AVAILABLE = True
except ImportError:
    ANALYTICS_AVAILABLE = False
    class AnalyticsCollector:
        """Dummy class for when the analytics module is not available."""
        def __init__(self, *args, **kwargs):
            logging.warning("Using DummyAnalyticsCollector because 'analytics' module was not found.")
        async def log_user_activity(self, *args, **kwargs):
            pass
        async def generate_analytics_report(self, *args, **kwargs):
            return "Аналитика недоступна."

# --- Logging Setup ---
log_dir = os.getenv("LOG_DIR", "/tmp")
log_file = os.path.join(log_dir, "bot.log")
os.makedirs(log_dir, exist_ok=True)
log_handlers: List[logging.Handler] = [logging.StreamHandler()]
try:
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    log_handlers.append(file_handler)
except Exception as e:
    logging.warning(f"Failed to initialize file logging to {log_file}: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)

# --- Configuration ---
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SAVE_APPROVED_DOMAINS = os.getenv("SAVE_APPROVED_DOMAINS", "false").lower() == "true"
BOT_USERNAME = os.getenv("BOT_USERNAME")
AUTO_DELETE_GROUP_MESSAGES = os.getenv("AUTO_DELETE_GROUP_MESSAGES", "true").lower() == "true"
AUTO_DELETE_TIMEOUT = int(os.getenv("AUTO_DELETE_TIMEOUT", "300"))
GROUP_RATE_LIMIT_MINUTES = int(os.getenv("GROUP_RATE_LIMIT_MINUTES", "5"))
GROUP_DAILY_LIMIT = int(os.getenv("GROUP_DAILY_LIMIT", "50"))
PRIVATE_RATE_LIMIT_PER_MINUTE = int(os.getenv("PRIVATE_RATE_LIMIT_PER_MINUTE", "10"))
PRIVATE_DAILY_LIMIT = int(os.getenv("PRIVATE_DAILY_LIMIT", "100"))
GROUP_MODE_ENABLED = os.getenv("GROUP_MODE_ENABLED", "true").lower() == "true"
GROUP_COMMAND_PREFIX = os.getenv("GROUP_COMMAND_PREFIX", "!");
AUTHORIZED_GROUPS_STR = os.getenv("AUTHORIZED_GROUPS", "").strip()
AUTHORIZED_GROUPS = set()
if AUTHORIZED_GROUPS_STR:
    try:
        AUTHORIZED_GROUPS = set(int(gid) for gid in AUTHORIZED_GROUPS_STR.split(",") if gid.strip())
    except ValueError:
        logging.error("Invalid AUTHORIZED_GROUPS format.")
AUTO_LEAVE_UNAUTHORIZED = os.getenv("AUTO_LEAVE_UNAUTHORIZED", "false").lower() == "true"

# --- Globals ---
if not TOKEN:
    logging.critical("BOT_TOKEN is not set. Exiting.")
    exit()
if not BOT_USERNAME:
    logging.warning("BOT_USERNAME is not set. Deep links from groups may not work.")

bot = Bot(token=TOKEN, parse_mode="HTML")
router = Router()
analytics_collector: Optional[AnalyticsCollector] = None

# --- Redis Connection ---
async def get_redis_connection() -> redis.Redis:
    try:
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {e}")
        raise

# --- Analytics ---
async def init_analytics():
    global analytics_collector
    try:
        redis_client = await get_redis_connection()
        analytics_collector = AnalyticsCollector(redis_client)
        if ANALYTICS_AVAILABLE:
            logging.info("✅ Real Analytics initialized")
        else:
            logging.info("✅ Dummy analytics initialized")
    except Exception as e:
        logging.error(f"❌ Failed to initialize analytics: {e}")
        analytics_collector = AnalyticsCollector() # Fallback to dummy without redis

async def log_analytics(action: str, user_id: int, **kwargs: Any):
    if analytics_collector:
        try:
            details_str = json.dumps(kwargs) if kwargs else None
            await analytics_collector.log_user_activity(user_id=user_id, action=action, details=details_str)
        except Exception as e:
            logging.warning(f"Failed to log analytics: {e}")

# --- Helper Functions ---
def is_group_chat(message: Message) -> bool:
    return message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]

def is_authorized_group(chat_id: int) -> bool:
    return not AUTHORIZED_GROUPS or chat_id in AUTHORIZED_GROUPS

def extract_domain(text: Optional[str]) -> Optional[str]:
    if not isinstance(text, str):
        return None
    text = re.sub(r':\d+$', '', text.strip())
    if text.startswith(("http://", "https")):
        try:
            hostname = urlparse(text).hostname
            return hostname.lower() if hostname else None
        except:
            return None
    if re.match(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$", text):
        return text.lower()
    return None

# --- Message Handling ---
async def send_topic_aware_message(message: Message, text: str, reply_markup=None) -> Optional[Message]:
    thread_id = message.message_thread_id if message.is_topic_message else None
    try:
        sent_message = await bot.send_message(
            chat_id=message.chat.id,
            text=text,
            message_thread_id=thread_id,
            reply_markup=reply_markup
        )
        if is_group_chat(message) and AUTO_DELETE_GROUP_MESSAGES:
            asyncio.create_task(delete_message_after_delay(sent_message.chat.id, sent_message.message_id))
        return sent_message
    except Exception as e:
        logging.error(f"Failed to send message to chat {message.chat.id}: {e}")
        return None

async def delete_message_after_delay(chat_id: int, message_id: int, delay: int = AUTO_DELETE_TIMEOUT):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

# --- Keyboards ---
def get_main_keyboard(is_admin: bool):
    buttons = [
        [InlineKeyboardButton(text="Смена вывода full / short", callback_data="mode")],
        [InlineKeyboardButton(text="История запросов", callback_data="history")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text="Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Сбросить очередь", callback_data="reset_queue"), InlineKeyboardButton(text="Очистить кэш", callback_data="clearcache")],
        [InlineKeyboardButton(text="Список пригодных", callback_data="approved"), InlineKeyboardButton(text="Очистить пригодные", callback_data="clear_approved")],
        [InlineKeyboardButton(text="Экспорт пригодных", callback_data="export_approved")],
        [InlineKeyboardButton(text="Аналитика", callback_data="analytics"), InlineKeyboardButton(text="Управление группами", callback_data="groups")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_full_report_button(domain: str):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 Полный отчёт", callback_data=f"full_report:{domain}")]])

def get_group_full_report_button(domain: str):
    if not BOT_USERNAME: return None
    encoded_domain = quote(domain)
    deep_link = f"https://t.me/{BOT_USERNAME}?start=full_{encoded_domain}"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📄 Полный отчёт в ЛС", url=deep_link)]])

# --- Limit Checks ---
async def check_limits(user_id: int, is_group: bool, chat_id: Optional[int]) -> bool:
    r = await get_redis_connection()
    try:
        # Rate limit
        if is_group:
            rate_limit_count = 1
            rate_limit_period = GROUP_RATE_LIMIT_MINUTES * 60
        else:
            rate_limit_count = PRIVATE_RATE_LIMIT_PER_MINUTE
            rate_limit_period = 60

        rate_key_suffix = f":{chat_id}" if is_group and chat_id else ""
        rate_key = f"rate:{user_id}{rate_key_suffix}:{int(time() / rate_limit_period)}"
        
        current_rate = await r.incr(rate_key)
        if current_rate == 1:
            await r.expire(rate_key, rate_limit_period)
        
        if current_rate > rate_limit_count:
            logging.warning(f"Rate limit of {rate_limit_count} exceeded for user {user_id} in chat {chat_id or 'private'}")
            return False

        # Daily limit
        daily_limit = GROUP_DAILY_LIMIT if is_group else PRIVATE_DAILY_LIMIT
        daily_key_suffix = f":{chat_id}" if is_group and chat_id else ""
        daily_key = f"daily:{user_id}{daily_key_suffix}:{datetime.now().strftime('%Y%m%d')}
        
        current_daily = await r.incr(daily_key)
        if current_daily == 1:
            await r.expire(daily_key, 86400)
        
        if current_daily > daily_limit:
            logging.warning(f"Daily limit of {daily_limit} exceeded for user {user_id} in chat {chat_id or 'private'}")
            return False
            
        return True
    finally:
        await r.aclose()

# --- Core Logic ---
async def handle_domain_logic(message: Message, text: str, short_mode: bool):
    if not message.from_user: return
    user_id = message.from_user.id
    is_group = is_group_chat(message)
    chat_id = message.chat.id if is_group else None

    if not await check_limits(user_id, is_group, chat_id):
        await send_topic_aware_message(message, "🚫 Превышен лимит запросов. Попробуйте позже.")
        return

    domains = re.split(r'[\s,]+', text)
    valid_domains = {d for d in (extract_domain(d) for d in domains) if d}

    if not valid_domains:
        await send_topic_aware_message(message, "❌ Не найдено ни одного корректного домена для проверки.")
        return

    r = await get_redis_connection()
    try:
        user_mode_is_short = (await r.get(f"mode:{user_id}")) != "full"
        final_short_mode = short_mode and user_mode_is_short

        for domain in valid_domains:
            cached_result = await r.get(f"result:{domain}")
            if cached_result and (not final_short_mode or "краткий" in cached_result.lower()):
                keyboard = get_full_report_button(domain) if final_short_mode and not is_group else None
                if is_group: keyboard = get_group_full_report_button(domain)
                await send_topic_aware_message(message, cached_result, reply_markup=keyboard)
            else:
                await enqueue(domain, user_id, final_short_mode, message.chat.id, message.message_id, message.message_thread_id)
                await send_topic_aware_message(message, f"✅ Домен <b>{domain}</b> добавлен в очередь на проверку.")
            await log_analytics("domain_check", user_id, domain=domain, mode="short" if final_short_mode else "full")
    finally:
        await r.aclose()

# --- Message Handlers ---
@router.message(CommandStart())
async def cmd_start(message: Message, command: Optional[CommandObject] = None):
    if not message.from_user: return
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID

    if command and command.args:
        param = command.args
        logging.info(f"Deep link from {user_id}: '{param}'")
        try:
            decoded_param = unquote(param)
        except Exception:
            decoded_param = param

        if decoded_param.startswith("full_"):
            domain = extract_domain(decoded_param[5:])
            if domain:
                await send_topic_aware_message(message, f"📄 <b>Получаю полный отчет для {domain}...</b>")
                await handle_domain_logic(message, domain, short_mode=False)
            else:
                await send_topic_aware_message(message, f"❌ Некорректный домен в ссылке: {decoded_param[5:]}")
        else:
            domain = extract_domain(decoded_param)
            if domain:
                await send_topic_aware_message(message, f"🔍 <b>Получаю результат для {domain}...</b>")
                await handle_domain_logic(message, domain, short_mode=True)
            else:
                await send_topic_aware_message(message, f"Неизвестный параметр deep-link: {decoded_param}")
        return

    welcome_message = (
        "👋 <b>Привет!</b> Я бот для проверки доменов.\n\n"
        "Отправь мне домен для проверки, например: <code>google.com</code>\n"
        "Или несколько доменов через запятую/пробел/новую строку.\n\n"
        "Используй /help для просмотра всех команд."
    )
    await send_topic_aware_message(message, welcome_message, reply_markup=get_main_keyboard(is_admin))

@router.message(Command("help"))
async def cmd_help(message: Message):
    if not message.from_user: return
    is_admin = message.from_user.id == ADMIN_ID
    
    help_text = (
        "<b>Основные команды:</b>\n"
        "/start - Начало работы\n"
        "/mode - Сменить режим вывода\n"
        "/history - Последние 10 проверок\n"
        "/check [домен] - Краткая проверка\n"
        "/full [домен] - Полная проверка\n"
    )
    if is_admin:
        help_text += "\n<b>Админ-команды:</b> /admin"
    await send_topic_aware_message(message, help_text)

@router.message(Command("mode"))
async def cmd_mode(message: Message):
    if not message.from_user: return
    user_id = message.from_user.id
    r = await get_redis_connection()
    try:
        current_mode = await r.get(f"mode:{user_id}") or "short"
        new_mode = "full" if current_mode == "short" else "short"
        await r.set(f"mode:{user_id}", new_mode)
        await send_topic_aware_message(message, f"✅ Режим вывода изменён на: <b>{new_mode}</b>")
    finally:
        await r.aclose()

@router.message(Command("history"))
async def cmd_history(message: Message):
    if not message.from_user: return
    user_id = message.from_user.id
    r = await get_redis_connection()
    try:
        history = await r.lrange(f"history:{user_id}", 0, 9)
        if not history:
            await send_topic_aware_message(message, "📜 Ваша история проверок пуста.")
            return
        response = "📜 <b>Ваши последние 10 проверок:</b>\n" + "\n".join(f"{i}. {entry}" for i, entry in enumerate(history, 1))
        await send_topic_aware_message(message, response)
    finally:
        await r.aclose()

@router.message(Command("check", "full"))
async def cmd_check(message: Message):
    if not message.from_user or not message.text: return
    
    command_parts = message.text.split(maxsplit=1)
    command = command_parts[0]
    args = command_parts[1] if len(command_parts) > 1 else ""
    
    if not args:
        await send_topic_aware_message(message, f"⛔ Укажите домен, например: {command} example.com")
        return
        
    short_mode = command.startswith("/check")
    await handle_domain_logic(message, args, short_mode=short_mode)

@router.message(F.text)
async def handle_text(message: Message):
    if not message.from_user or not message.text or message.text.startswith('/'): return
    
    if is_group_chat(message):
        if not GROUP_MODE_ENABLED: return
        bot_info = await bot.get_me()
        is_mention = bot_info.username and f"@{bot_info.username}" in message.text
        is_command = message.text.startswith(GROUP_COMMAND_PREFIX)
        if not (is_mention or is_command):
            return

    r = await get_redis_connection()
    try:
        user_mode_is_short = (await r.get(f"mode:{message.from_user.id}")) != "full"
        await handle_domain_logic(message, message.text, short_mode=user_mode_is_short)
    finally:
        await r.aclose()

# --- Admin Handlers ---
async def is_admin_check(query_or_message: Union[Message, CallbackQuery]) -> bool:
    user = query_or_message.from_user
    if not user or user.id != ADMIN_ID:
        text = "⛔ Эта команда доступна только администратору."
        if isinstance(query_or_message, Message):
            await send_topic_aware_message(query_or_message, text)
        else:
            await query_or_message.answer(text, show_alert=True)
        return False
    return True

@router.message(Command("admin"))
async def admin_panel_command(message: Message):
    if not await is_admin_check(message): return
    await send_topic_aware_message(message, "Добро пожаловать в админ-панель.", reply_markup=get_admin_keyboard())

@router.message(Command("approved"))
async def cmd_approved(message: types.Message):
    if not await is_admin_check(message): return
    if not SAVE_APPROVED_DOMAINS:
        await message.reply("⛔ Функция сохранения доменов отключена.")
        return
    r = await get_redis_connection()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пригодных доменов пуст.")
            return
        response = "📜 <b>Пригодные домены:</b>\n" + "\n".join(f"{i}. {d}" for i, d in enumerate(sorted(domains), 1))
        await message.reply(response)
    finally:
        await r.aclose()

@router.message(Command("clear_approved"))
async def cmd_clear_approved(message: types.Message):
    if not await is_admin_check(message): return
    if not SAVE_APPROVED_DOMAINS: return
    r = await get_redis_connection()
    try:
        await r.delete("approved_domains")
        await message.reply("✅ Список пригодных доменов очищен.")
    finally:
        await r.aclose()

@router.message(Command("export_approved"))
async def cmd_export_approved(message: types.Message):
    if not await is_admin_check(message): return
    if not SAVE_APPROVED_DOMAINS: return
    r = await get_redis_connection()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пуст.")
            return
        file_path = os.path.join(os.getenv("LOG_DIR", "/tmp"), "approved_domains.txt")
        with open(file_path, "w") as f:
            f.write("\n".join(sorted(domains)))
        await message.reply_document(types.FSInputFile(file_path))
    except Exception as e:
        await message.reply(f"❌ Ошибка экспорта: {e}")
    finally:
        await r.aclose()

@router.message(Command("reset_queue"))
async def reset_queue_command(message: types.Message):
    if not await is_admin_check(message): return
    r = await get_redis_connection()
    try:
        q_len = await r.llen("queue:domains")
        p_keys = await r.keys("pending:*")
        if q_len > 0: await r.delete("queue:domains")
        if p_keys: await r.delete(*p_keys)
        await message.reply(f"✅ Очередь сброшена. Удалено задач: {q_len}, ключей pending: {len(p_keys)}.")
    finally:
        await r.aclose()

@router.message(Command("clearcache"))
async def clear_cache_command(message: types.Message):
    if not await is_admin_check(message): return
    r = await get_redis_connection()
    try:
        keys = await r.keys("result:*")
        if keys:
            await r.delete(*keys)
            await message.reply(f"✅ Кэш очищен. Удалено {len(keys)} записей.")
        else:
            await message.reply("✅ Кэш уже пуст.")
    finally:
        await r.aclose()

@router.message(Command("analytics"))
async def analytics_command(message: types.Message):
    if not await is_admin_check(message): return
    if not message.from_user: return
    
    if not analytics_collector:
        await message.reply("❌ Аналитика не инициализирована.")
        return
    try:
        report = await analytics_collector.generate_analytics_report(message.from_user.id)
        await message.reply(report)
    except Exception as e:
        await message.reply(f"❌ Ошибка генерации отчета: {e}")

@router.message(Command("groups"))
async def groups_command(message: types.Message):
    if not await is_admin_check(message): return
    
    if not AUTHORIZED_GROUPS:
        status = "🌐 <b>Режим авторизации:</b> Открытый (любые группы)\n"
    else:
        status = f"🔒 <b>Режим авторизации:</b> Ограниченный ({len(AUTHORIZED_GROUPS)} групп)\n"
        status += "<b>Авторизованные группы:</b>\n" + "\n".join(f"• <code>{gid}</code>" for gid in sorted(AUTHORIZED_GROUPS))
    
    await message.reply(status)

# --- Callback Query Handlers ---
@router.callback_query(F.data == "start_menu")
async def cq_start_menu(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    is_admin = call.from_user.id == ADMIN_ID
    await call.message.edit_text(
        "👋 <b>Привет!</b> Я бот для проверки доменов.\n\n"
        "Отправь мне домен для проверки, например: <code>google.com</code>\n"
        "Или несколько доменов через запятую/пробел/новую строку.\n\n"
        "Используй /help для просмотра всех команд.",
        reply_markup=get_main_keyboard(is_admin)
    )
    await call.answer()

@router.callback_query(F.data == "mode")
async def cq_mode(call: CallbackQuery):
    if not call.from_user: return
    user_id = call.from_user.id
    r = await get_redis_connection()
    try:
        current_mode = await r.get(f"mode:{user_id}") or "short"
        new_mode = "full" if current_mode == "short" else "short"
        await r.set(f"mode:{user_id}", new_mode)
        await call.answer(f"✅ Режим вывода изменён на: {new_mode}")
    finally:
        await r.aclose()

@router.callback_query(F.data == "history")
async def cq_history(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    user_id = call.from_user.id
    r = await get_redis_connection()
    try:
        history = await r.lrange(f"history:{user_id}", 0, 9)
        if not history:
            await call.answer("📜 Ваша история проверок пуста.", show_alert=True)
            return
        response = "📜 <b>Ваши последние 10 проверок:</b>\n" + "\n".join(f"{i}. {entry}" for i, entry in enumerate(history, 1))
        await call.message.edit_text(response, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")]
        ]))
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data.startswith("full_report:"))
async def cq_full_report(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user or not call.data: return
    domain = call.data.split(":", 1)[1]
    await call.answer(f"📄 Запрашиваю полный отчет для {domain}...")
    await handle_domain_logic(call.message, domain, short_mode=False)

@router.callback_query(F.data == "admin_panel")
async def cq_admin_panel(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    await call.message.edit_text("Панель администратора:", reply_markup=get_admin_keyboard())
    await call.answer()

# --- Group Management ---
@router.my_chat_member(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def on_group_join(update: types.ChatMemberUpdated):
    chat_id = update.chat.id
    if update.new_chat_member.status == "member":
        if AUTO_LEAVE_UNAUTHORIZED and not is_authorized_group(chat_id):
            logging.warning(f"Leaving unauthorized group {chat_id} ({update.chat.title})")
            await bot.leave_chat(chat_id)
        else:
            logging.info(f"Joined group {chat_id} ({update.chat.title})")
            await bot.send_message(ADMIN_ID, f"✅ Бота добавили в новую группу: {update.chat.title} (<code>{chat_id}</code>)")

# --- Main Execution ---
async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Начать работу с ботом"),
        BotCommand(command="help", description="Показать справку"),
        BotCommand(command="mode", description="Сменить режим вывода (full/short)"),
        BotCommand(command="history", description="Показать историю запросов"),
        BotCommand(command="check", description="Краткая проверка домена"),
        BotCommand(command="full", description="Полная проверка домена"),
        BotCommand(command="admin", description="Панель администратора"),
    ]
    await bot.set_my_commands(commands)

async def main():
    dp = Dispatcher()
    dp.include_router(router)

    await init_analytics()
    await set_bot_commands()

    try:
        logging.info("Bot starting...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logging.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot execution stopped by user.")
    except Exception as e:
        logging.error(f"Critical error in main loop: {e}", exc_info=True)
