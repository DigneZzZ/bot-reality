from typing import Optional
import asyncio
from aiogram import Bot, Dispatcher, Router, types
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from aiogram.enums import ChatType
import os
import redis.asyncio as redis
from redis_queue import enqueue
from collections import defaultdict
from time import time
import re
from urllib.parse import urlparse, quote, unquote
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Импортируем новые модули (если доступны)
try:
    from retry_logic import retry_with_backoff, DOMAIN_CHECK_RETRY, REDIS_RETRY, TELEGRAM_RETRY
    RETRY_AVAILABLE = True
except ImportError:
    RETRY_AVAILABLE = False
    
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

# Настройка логирования
log_dir = "/app"
log_file = os.path.join(log_dir, "bot.log")
fallback_log_file = "/tmp/bot.log"
os.makedirs(log_dir, exist_ok=True)
log_handlers = []

try:
    with open(log_file, "a") as f:
        f.write("")
    # Уменьшаем размер файла логов и количество бэкапов
    file_handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    log_handlers.append(file_handler)
except Exception as e:
    logging.warning(f"Failed to initialize logging to {log_file}: {str(e)}. Falling back to {fallback_log_file}")
    os.makedirs("/tmp", exist_ok=True)
    # Для fallback файла тоже уменьшаем размеры
    file_handler = RotatingFileHandler(fallback_log_file, maxBytes=5*1024*1024, backupCount=2)
    log_handlers.append(file_handler)

log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.WARNING,  # Изменено с INFO на WARNING
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SAVE_APPROVED_DOMAINS = os.getenv("SAVE_APPROVED_DOMAINS", "false").lower() == "true"
BOT_USERNAME = os.getenv("BOT_USERNAME", "bot")

# Настройки автоочистки сообщений в группах
AUTO_DELETE_GROUP_MESSAGES = os.getenv("AUTO_DELETE_GROUP_MESSAGES", "true").lower() == "true"
AUTO_DELETE_TIMEOUT = int(os.getenv("AUTO_DELETE_TIMEOUT", "300"))  # 5 минут по умолчанию

# Лимиты для групповых чатов (на пользователя в каждой группе)
GROUP_RATE_LIMIT_MINUTES = int(os.getenv("GROUP_RATE_LIMIT_MINUTES", "5"))  # Минут между запросами
GROUP_DAILY_LIMIT = int(os.getenv("GROUP_DAILY_LIMIT", "50"))               # Запросов в день

# Лимиты для приватных чатов
PRIVATE_RATE_LIMIT_PER_MINUTE = int(os.getenv("PRIVATE_RATE_LIMIT_PER_MINUTE", "10"))  # Запросов в минуту
PRIVATE_DAILY_LIMIT = int(os.getenv("PRIVATE_DAILY_LIMIT", "100"))                     # Запросов в день

# Новые настройки для групп
GROUP_MODE_ENABLED = os.getenv("GROUP_MODE_ENABLED", "true").lower() == "true"
GROUP_COMMAND_PREFIX = os.getenv("GROUP_COMMAND_PREFIX", "!")  # Префикс для команд в группах
# Авторизация групп
AUTHORIZED_GROUPS_STR = os.getenv("AUTHORIZED_GROUPS", "").strip()
AUTHORIZED_GROUPS = set()
if AUTHORIZED_GROUPS_STR:
    try:
        AUTHORIZED_GROUPS = set(int(group_id.strip()) for group_id in AUTHORIZED_GROUPS_STR.split(",") if group_id.strip())
    except ValueError:
        logging.error("Invalid AUTHORIZED_GROUPS format. Should be comma-separated integers.")
AUTO_LEAVE_UNAUTHORIZED = os.getenv("AUTO_LEAVE_UNAUTHORIZED", "false").lower() == "true"

bot = Bot(token=TOKEN, parse_mode="HTML")
router = Router()
analytics_collector = None

async def get_redis():
    try:
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

async def init_analytics():
    global analytics_collector
    if ANALYTICS_AVAILABLE:
        try:
            redis_client = await get_redis()
            analytics_collector = AnalyticsCollector(redis_client)
            logging.info("✅ Analytics initialized successfully")
        except Exception as e:
            logging.warning(f"❌ Failed to initialize analytics: {e}")
    else:
        logging.warning("❌ Analytics module not available")

def is_group_chat(message: types.Message) -> bool:
    return message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]

def is_authorized_group(chat_id: int) -> bool:
    return not AUTHORIZED_GROUPS or chat_id in AUTHORIZED_GROUPS

async def handle_unauthorized_group(message: types.Message) -> bool:
    chat_id = message.chat.id
    if not is_authorized_group(chat_id):
        logging.warning(f"Unauthorized group access attempt: {chat_id} ({message.chat.title})")
        if AUTO_LEAVE_UNAUTHORIZED:
            try:
                await message.answer(
                    f"⚠️ <b>Бот не авторизован для работы в этой группе.</b>\nID группы: <code>{chat_id}</code>\nБот покинет группу."
                )
                await asyncio.sleep(10)
                await bot.leave_chat(chat_id)
                logging.info(f"Left unauthorized group: {chat_id}")
            except Exception as e:
                logging.error(f"Failed to leave unauthorized group {chat_id}: {e}")
        else:
            await message.answer(f"⚠️ <b>Бот не авторизован для работы в этой группе.</b>\nID группы: <code>{chat_id}</code>")
        return True
    return False

async def should_respond_in_group(message: types.Message) -> bool:
    if not GROUP_MODE_ENABLED or not message.text or not message.chat.id:
        return False
    if await handle_unauthorized_group(message):
        return False
    
    text = message.text
    if text.startswith(GROUP_COMMAND_PREFIX):
        return True
    
    bot_info = await bot.get_me()
    if f"@{bot_info.username}" in text:
        return True
        
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_info.id:
        return True
        
    return False

async def schedule_message_deletion(chat_id: int, message_id: int, delay: int = AUTO_DELETE_TIMEOUT):
    if not AUTO_DELETE_GROUP_MESSAGES:
        return
    asyncio.create_task(delete_message_after_delay(chat_id, message_id, delay))

async def delete_message_after_delay(chat_id: int, message_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logging.info(f"Auto-deleted message {message_id} in chat {chat_id}")
    except Exception as e:
        logging.debug(f"Could not delete message {message_id} in chat {chat_id}: {e}")

def get_topic_thread_id(message: types.Message) -> Optional[int]:
    return message.message_thread_id if message.is_topic_message else None

async def send_topic_aware_message(message: types.Message, text: str, reply_markup=None) -> types.Message:
    thread_id = get_topic_thread_id(message)
    try:
        sent_message = await bot.send_message(
            chat_id=message.chat.id,
            text=text,
            message_thread_id=thread_id,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        if is_group_chat(message):
            await schedule_message_deletion(message.chat.id, sent_message.message_id)
        return sent_message
    except Exception as e:
        logging.warning(f"Failed to send topic-aware message: {e}, falling back.")
        sent_message = await message.answer(text, reply_markup=reply_markup)
        if is_group_chat(message):
            await schedule_message_deletion(message.chat.id, sent_message.message_id)
        return sent_message

async def log_analytics(action: str, user_id: int, **kwargs):
    if analytics_collector:
        try:
            await analytics_collector.log_user_activity(user_id=user_id, action=action, details=kwargs)
        except Exception as e:
            logging.warning(f"Failed to log analytics: {e}")

def get_main_keyboard(is_admin: bool):
    buttons = [
        [InlineKeyboardButton(text="Смена вывода full / short", callback_data="mode")],
        [InlineKeyboardButton(text="История запросов", callback_data="history")]
    ]
    if is_admin:
        admin_buttons = [
            [InlineKeyboardButton(text="Сбросить очередь", callback_data="reset_queue")],
            [InlineKeyboardButton(text="Очистить кэш", callback_data="clearcache")]
        ]
        if SAVE_APPROVED_DOMAINS:
            admin_buttons.extend([
                [InlineKeyboardButton(text="Список пригодных", callback_data="approved")],
                [InlineKeyboardButton(text="Очистить пригодные", callback_data="clear_approved")],
                [InlineKeyboardButton(text="Экспорт пригодных", callback_data="export_approved")]
            ])
        buttons.extend(admin_buttons)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_full_report_button(domain: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Полный отчёт", callback_data=f"full_report:{domain}")]
    ])

def get_group_full_report_button(domain: str):
    encoded_domain = quote(domain, safe='')
    deep_link = f"https://t.me/{BOT_USERNAME}?start=full_{encoded_domain}"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Полный отчёт в ЛС", url=deep_link)]
    ])

def extract_domain(text: str) -> Optional[str]:
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

async def check_limits(user_id: int, is_group: bool, chat_id: Optional[int]) -> bool:
    """Combines rate and daily limit checks."""
    r = await get_redis()
    try:
        # Rate limit
        rate_limit, rate_period = (GROUP_RATE_LIMIT_MINUTES * 60, 60) if is_group else (PRIVATE_RATE_LIMIT_PER_MINUTE, 60)
        rate_key_suffix = f":{chat_id}" if is_group and chat_id else ""
        rate_key = f"rate:{user_id}{rate_key_suffix}:{int(time() / rate_period)}"
        
        rate_count = await r.incr(rate_key)
        if rate_count == 1:
            await r.expire(rate_key, rate_period)
        
        if rate_count > (GROUP_RATE_LIMIT_MINUTES if is_group else PRIVATE_RATE_LIMIT_PER_MINUTE):
            logging.warning(f"Rate limit exceeded for user {user_id} in chat {chat_id or 'private'}")
            return False

        # Daily limit
        daily_limit = GROUP_DAILY_LIMIT if is_group else PRIVATE_DAILY_LIMIT
        daily_key_suffix = f":{chat_id}" if is_group and chat_id else ""
        daily_key = f"daily:{user_id}{daily_key_suffix}:{datetime.now().strftime('%Y%m%d')}"
        
        daily_count = await r.incr(daily_key)
        if daily_count == 1:
            await r.expire(daily_key, 86400)

        if daily_count > daily_limit:
            logging.warning(f"Daily limit exceeded for user {user_id} in chat {chat_id or 'private'}")
            return False
            
        return True
    finally:
        await r.aclose()

@router.message(CommandStart(deep_link=True))
async def cmd_start_deep_link(message: types.Message, command: CommandObject):
    if not message.from_user:
        return
    user_id = message.from_user.id
    param = command.args

    if not param:
        await cmd_start_no_deep_link(message)
        return

    logging.warning(f"Deep link from {user_id}: '{param}'")
    try:
        decoded_param = unquote(param)
    except Exception:
        decoded_param = param

    if decoded_param.startswith("full_"):
        domain = extract_domain(decoded_param[5:])
        if domain:
            await message.answer(f"📄 <b>Получаю полный отчет для {domain}...</b>")
            await handle_domain_logic(message, domain, short_mode=False)
        else:
            await message.answer(f"❌ Некорректный домен в ссылке: {decoded_param[5:]}")
    else:
        domain = extract_domain(decoded_param)
        if domain:
            await message.answer(f"🔍 <b>Получаю результат для {domain}...</b>")
            await handle_domain_logic(message, domain, short_mode=True)
        else:
            # If param is not a domain, show default start message
            await cmd_start_no_deep_link(message)


@router.message(CommandStart())
async def cmd_start_no_deep_link(message: types.Message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    
    welcome_message = (
        "👋 <b>Привет!</b> Я бот для проверки доменов.\n\n"
        "Отправь мне домен для проверки, например: <code>google.com</code>\n"
        "Или несколько доменов через запятую/пробел/новую строку.\n\n"
        "Используй /help для просмотра всех команд."
    )
    await message.answer(welcome_message, reply_markup=get_main_keyboard(is_admin))

@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not message.from_user:
        return
    is_admin = message.from_user.id == ADMIN_ID
    
    help_text = (
        "<b>Основные команды:</b>\n"
        "/start - Начало работы\n"
        "/mode - Сменить режим вывода (краткий/полный)\n"
        "/history - Показать последние 10 проверок\n"
        "/check [домен] - Краткая проверка домена\n"
        "/full [домен] - Полная проверка домена\n"
    )
    if is_admin:
        help_text += (
            "\n<b>Админ-команды:</b>\n"
            "/reset_queue - Сбросить очередь\n"
            "/clearcache - Очистить кэш\n"
            "/approved - Список пригодных доменов\n"
            "/clear_approved - Очистить список пригодных\n"
            "/export_approved - Экспорт пригодных\n"
            "/analytics - Показать аналитику\n"
            "/groups - Управление группами\n"
        )
    await message.answer(help_text)

async def handle_domain_logic(message: types.Message, text: str, short_mode: bool):
    if not message.from_user:
        return
    user_id = message.from_user.id
    is_group = is_group_chat(message)
    chat_id = message.chat.id if is_group else None

    if not await check_limits(user_id, is_group, chat_id):
        limit_msg = "🚫 Превышен лимит запросов. Попробуйте позже."
        await send_topic_aware_message(message, limit_msg)
        return

    domains = re.split(r'[\s,]+', text)
    valid_domains = [d for d in (extract_domain(d) for d in domains) if d]

    if not valid_domains:
        await send_topic_aware_message(message, "❌ Не найдено ни одного корректного домена для проверки.")
        return

    r = await get_redis()
    try:
        user_mode_is_short = (await r.get(f"mode:{user_id}")) != "full"
        final_short_mode = short_mode and user_mode_is_short

        if PROGRESS_AVAILABLE and len(valid_domains) > 1 and not is_group:
            batch_processor = BatchProcessor(bot, message, valid_domains, user_id, final_short_mode)
            await batch_processor.process()
        else:
            for domain in valid_domains:
                cached_result = await r.get(f"result:{domain}")
                if cached_result and (not final_short_mode or "краткий" in cached_result.lower()):
                    keyboard = get_full_report_button(domain) if final_short_mode else None
                    if is_group:
                        keyboard = get_group_full_report_button(domain)
                    await send_topic_aware_message(message, cached_result, reply_markup=keyboard)
                else:
                    await enqueue(domain, user_id, final_short_mode, message.chat.id, message.message_id, get_topic_thread_id(message))
                    await send_topic_aware_message(message, f"✅ Домен <b>{domain}</b> добавлен в очередь на проверку.")
                await log_analytics("domain_check", user_id, domain=domain, mode="short" if final_short_mode else "full")
    finally:
        await r.aclose()

# --- Admin Commands ---
async def is_admin_check(message: types.Message) -> bool:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        return False
    return True

@router.message(Command("approved"))
async def cmd_approved(message: types.Message):
    if not await is_admin_check(message): return
    if not SAVE_APPROVED_DOMAINS:
        await message.reply("⛔ Функция сохранения доменов отключена.")
        return
    r = await get_redis()
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
    r = await get_redis()
    try:
        await r.delete("approved_domains")
        await message.reply("✅ Список пригодных доменов очищен.")
    finally:
        await r.aclose()

@router.message(Command("export_approved"))
async def cmd_export_approved(message: types.Message):
    if not await is_admin_check(message): return
    if not SAVE_APPROVED_DOMAINS: return
    r = await get_redis()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пуст.")
            return
        file_path = "/tmp/approved_domains.txt"
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
    r = await get_redis()
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
    r = await get_redis()
    try:
        keys = await r.keys("result:*")
        if keys:
            await r.delete(*keys)
            await message.reply(f"✅ Кэш очищен. Удалено {len(keys)} записей.")
        else:
            await message.reply("✅ Кэш уже пуст.")
    finally:
        await r.aclose()

@router.message(Command("adminhelp"))
async def admin_help_command(message: types.Message):
    if not await is_admin_check(message): return
    await cmd_help(message)

@router.message(Command("analytics"))
async def analytics_command(message: types.Message):
    if not await is_admin_check(message): return
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

@router.message(Command("groups_add"))
async def groups_add_command(message: types.Message):
    if not await is_admin_check(message) or not message.text: return
    try:
        group_id = int(message.text.split()[1])
        AUTHORIZED_GROUPS.add(group_id)
        await message.reply(f"✅ Группа <code>{group_id}</code> добавлена. Перезапустите бота для сохранения.")
    except (ValueError, IndexError):
        await message.reply("❌ Укажите ID группы: /groups_add -100123...")

@router.message(Command("groups_remove"))
async def groups_remove_command(message: types.Message):
    if not await is_admin_check(message) or not message.text: return
    try:
        group_id = int(message.text.split()[1])
        AUTHORIZED_GROUPS.discard(group_id)
        await message.reply(f"✅ Группа <code>{group_id}</code> удалена. Перезапустите бота для сохранения.")
    except (ValueError, IndexError):
        await message.reply("❌ Укажите ID группы: /groups_remove -100123...")

@router.message(Command("groups_current"))
async def groups_current_command(message: types.Message):
    if not await is_admin_check(message): return
    if is_group_chat(message):
        await message.reply(f"ID этой группы: <code>{message.chat.id}</code>")
    else:
        await message.reply("ℹ️ Команда работает только в группах.")

@router.message(Command("check", "full"))
async def cmd_check(message: types.Message):
    if not message.from_user or not message.text:
        return
    
    command_parts = message.text.split(maxsplit=1)
    command = command_parts[0]
    args = command_parts[1] if len(command_parts) > 1 else ""
    
    if not args:
        await send_topic_aware_message(message, f"⛔ Укажите домен, например: {command} example.com")
        return
        
    short_mode = command == "/check"
    await handle_domain_logic(message, args, short_mode=short_mode)

@router.message()
async def handle_text(message: types.Message):
    if not message.from_user or not message.text or message.text.startswith('/'):
        return
    
    if is_group_chat(message) and not await should_respond_in_group(message):
        return

    r = await get_redis()
    try:
        user_mode_is_short = (await r.get(f"mode:{message.from_user.id}")) != "full"
        await handle_domain_logic(message, message.text, short_mode=user_mode_is_short)
    finally:
        await r.aclose()

@router.callback_query()
async def on_callback_query(cq: types.CallbackQuery):
    if not cq.from_user or not cq.message:
        await cq.answer("❌ Ошибка: не удалось обработать запрос.")
        return
        
    user_id = cq.from_user.id
    data = cq.data

    # Map data to functions
    actions = {
        "mode": cmd_mode,
        "history": cmd_history,
        "reset_queue": reset_queue_command,
        "clearcache": clear_cache_command,
        "approved": cmd_approved,
        "clear_approved": cmd_clear_approved,
        "export_approved": cmd_export_approved,
    }

    if data in actions:
        await actions[data](cq.message)
    elif data.startswith("full_report:"):
        domain = data.split(":", 1)[1]
        await cq.message.answer(f"📄 <b>Получаю полный отчет для {domain}...</b>")
        await handle_domain_logic(cq.message, domain, short_mode=False)
    
    await cq.answer()

async def set_bot_commands():
    commands = [
        BotCommand(command="start", description="Начать работу"),
        BotCommand(command="help", description="Помощь по командам"),
        BotCommand(command="mode", description="Сменить режим вывода"),
        BotCommand(command="history", description="История проверок"),
        BotCommand(command="check", description="Краткая проверка домена"),
        BotCommand(command="full", description="Полная проверка домена"),
    ]
    admin_commands = commands + [BotCommand(command="adminhelp", description="Команды администратора")]
    
    try:
        await bot.set_my_commands(commands)
        # Set extended commands for the admin
        await bot.set_my_commands(admin_commands, scope=types.BotCommandScopeChat(chat_id=ADMIN_ID))
        logging.info("Bot commands updated successfully.")
    except Exception as e:
        logging.error(f"Failed to set bot commands: {e}")

async def main():
    if not TOKEN:
        logging.critical("BOT_TOKEN не найден! Завершение работы.")
        return

    await init_analytics()
    
    dp = Dispatcher()
    dp.include_router(router)

    # Set commands before starting
    @dp.startup()
    async def on_startup(bot: Bot):
        await set_bot_commands()

    logging.warning("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.warning("Бот остановлен.")
    except Exception as e:
        logging.critical(f"Критическая ошибка при запуске бота: {e}", exc_info=True)
