import asyncio
from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatType
import os
import redis.asyncio as redis
from redis_queue import enqueue, get_redis
from collections import defaultdict
from time import time
import re
from urllib.parse import urlparse
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

# Настройки автоочистки сообщений в группах
AUTO_DELETE_GROUP_MESSAGES = os.getenv("AUTO_DELETE_GROUP_MESSAGES", "true").lower() == "true"
AUTO_DELETE_TIMEOUT = int(os.getenv("AUTO_DELETE_TIMEOUT", "300"))  # 5 минут по умолчанию

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

# Инициализация аналитики (если доступна)
analytics_collector = None

async def init_analytics():
    """Инициализирует аналитику"""
    global analytics_collector
    if ANALYTICS_AVAILABLE:
        try:
            redis_client = await get_redis()
            analytics_collector = AnalyticsCollector(redis_client)
            logging.info("✅ Analytics initialized successfully")
        except Exception as e:
            logging.warning(f"❌ Failed to initialize analytics: {e}")
            logging.warning("💡 Check Redis connection and settings")
    else:
        logging.warning("❌ Analytics module not available - check dependencies")

def is_group_chat(message: types.Message) -> bool:
    """Проверяет, является ли чат групповым"""
    return message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]

def is_authorized_group(chat_id: int) -> bool:
    """Проверяет, авторизована ли группа"""
    # Если список авторизованных групп пуст, разрешаем все группы
    if not AUTHORIZED_GROUPS:
        return True
    
    # Проверяем, есть ли группа в списке авторизованных
    return chat_id in AUTHORIZED_GROUPS

async def handle_unauthorized_group(message: types.Message) -> bool:
    """Обрабатывает неавторизованную группу. Возвращает True если нужно остановить обработку"""
    chat_id = message.chat.id
    
    if not is_authorized_group(chat_id):
        # Логируем попытку использования в неавторизованной группе
        logging.warning(f"Unauthorized group access attempt: {chat_id} ({message.chat.title})")
        
        if AUTO_LEAVE_UNAUTHORIZED:
            try:
                # Отправляем предупреждение перед выходом
                await message.answer(
                    "⚠️ <b>Бот не авторизован для работы в этой группе</b>\n\n"
                    "Если вы администратор бота, добавьте ID группы в переменную AUTHORIZED_GROUPS.\n"
                    f"ID этой группы: <code>{chat_id}</code>\n\n"
                    "Бот покинет группу через 10 секунд."
                )
                
                # Ждем 10 секунд и покидаем группу
                await asyncio.sleep(10)
                await bot.leave_chat(chat_id)
                logging.info(f"Left unauthorized group: {chat_id}")
                
            except Exception as e:
                logging.error(f"Failed to leave unauthorized group {chat_id}: {e}")
        else:
            # Просто игнорируем сообщения в неавторизованных группах
            await message.answer(
                "⚠️ <b>Бот не авторизован для работы в этой группе</b>\n\n"
                f"ID группы: <code>{chat_id}</code>\n"
                "Обратитесь к администратору бота для авторизации."
            )
        
        return True  # Останавливаем дальнейшую обработку
    
    return False  # Группа авторизована, продолжаем обработку

async def should_respond_in_group(message: types.Message) -> bool:
    """Определяет, должен ли бот отвечать в группе"""
    if not GROUP_MODE_ENABLED:
        return False
    
    # Проверяем авторизацию группы
    if not is_authorized_group(message.chat.id):
        await handle_unauthorized_group(message)
        return False
    
    # В группах отвечаем только на:
    # 1. Команды с префиксом (!check, !full)
    # 2. Упоминания бота (@botname)
    # 3. Ответы на сообщения бота
    
    text = message.text or ""
    
    # Команды с префиксом
    if text.startswith(GROUP_COMMAND_PREFIX):
        return True
    
    # Упоминание бота
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention":
                mention = text[entity.offset:entity.offset + entity.length]
                bot_info = await bot.get_me()
                if bot_info.username and mention.lower().replace("@", "") == bot_info.username.lower():
                    return True
    
    # Ответ на сообщение бота
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == (await bot.get_me()).id:
        return True
        
    return False

async def schedule_message_deletion(chat_id: int, message_id: int, delay: int = AUTO_DELETE_TIMEOUT):
    """Планирует удаление сообщения через заданное время"""
    if not AUTO_DELETE_GROUP_MESSAGES:
        return
        
    async def delete_after_delay():
        try:
            await asyncio.sleep(delay)
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logging.info(f"Auto-deleted message {message_id} in chat {chat_id}")
        except Exception as e:
            # Сообщение могло быть уже удалено или бот потерял права
            logging.debug(f"Could not delete message {message_id} in chat {chat_id}: {e}")
    
    # Запускаем задачу в фоне
    asyncio.create_task(delete_after_delay())

def get_topic_thread_id(message: types.Message) -> int | None:
    """Получает ID темы (топика) из сообщения"""
    # Если это супергруппа с темами
    if message.chat.type == ChatType.SUPERGROUP and hasattr(message, 'message_thread_id'):
        return message.message_thread_id
    return None

async def send_topic_aware_message(message: types.Message, text: str, reply_markup=None) -> types.Message:
    """Отправляет сообщение с учетом темы (топика)"""
    thread_id = get_topic_thread_id(message)
    
    try:
        if thread_id:
            # Отправляем в определенную тему
            sent_message = await bot.send_message(
                chat_id=message.chat.id,
                text=text,
                message_thread_id=thread_id,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            # Обычная отправка (или группа без тем)
            sent_message = await message.answer(text, reply_markup=reply_markup)
        
        # Автоматически планируем удаление для групповых сообщений
        if message.chat.type in ['group', 'supergroup'] and AUTO_DELETE_GROUP_MESSAGES:
            await schedule_message_deletion(message.chat.id, sent_message.message_id)
        
        return sent_message
        
    except Exception as e:
        # Fallback: пробуем отправить обычным способом
        logging.warning(f"Failed to send topic-aware message: {e}, falling back to regular message")
        sent_message = await message.answer(text, reply_markup=reply_markup)
        
        # Автоматически планируем удаление для групповых сообщений
        if message.chat.type in ['group', 'supergroup'] and AUTO_DELETE_GROUP_MESSAGES:
            await schedule_message_deletion(message.chat.id, sent_message.message_id)
        
        return sent_message

async def log_analytics(action: str, user_id: int, **kwargs):
    """Логирует событие в аналитику"""
    if analytics_collector:
        try:
            if action == "domain_check":
                await analytics_collector.log_domain_check(
                    user_id=user_id,
                    domain=kwargs.get("domain", ""),
                    check_type=kwargs.get("check_type", "short"),
                    result_status=kwargs.get("result_status", "unknown"),
                    execution_time=kwargs.get("execution_time")
                )
            else:
                await analytics_collector.log_user_activity(
                    user_id=user_id,
                    action=action,
                    details=kwargs.get("details")
                )
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
            [InlineKeyboardButton(text="Очистить кэш результатов", callback_data="clearcache")]
        ]
        # Добавляем кнопки управления доменами только если включена опция
        if SAVE_APPROVED_DOMAINS:
            admin_buttons.extend([
                [InlineKeyboardButton(text="Список пригодных доменов", callback_data="approved")],
                [InlineKeyboardButton(text="Очистить список доменов", callback_data="clear_approved")],
                [InlineKeyboardButton(text="Экспортировать домены", callback_data="export_approved")]
            ])
        buttons.extend(admin_buttons)
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_full_report_button(domain: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Полный отчёт", callback_data=f"full_report:{domain}")]
    ])
    return keyboard

def get_group_full_report_button(domain: str, user_id: int):
    """Создаёт кнопку с deep link для получения полного отчёта в ЛС из группового чата"""
    bot_username = os.getenv("BOT_USERNAME", "bot")  # Замените на актуальное имя бота
    deep_link = f"https://t.me/{bot_username}?start=full_{domain}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Полный отчёт в ЛС", url=deep_link)]
    ])
    return keyboard

async def get_redis():
    try:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
        return redis_client
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

user_requests = defaultdict(list)
user_violations = {}

def extract_domain(text: str):
    text = text.strip()
    text = re.sub(r':\d+$', '', text)
    if text.startswith("http://") or text.startswith("https://"):
        try:
            parsed = urlparse(text)
            if parsed.hostname:
                return parsed.hostname
        except:
            return None
    if re.match(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$", text):
        return text
    return None

def rate_limited(user_id):
    now = time()
    user_requests[user_id] = [ts for ts in user_requests[user_id] if now - ts < 30]
    if len(user_requests[user_id]) >= 10:
        return True
    user_requests[user_id].append(now)
    return False

def get_penalty(user_id):
    record = user_violations.get(user_id, {"count": 0, "until": 0})
    now = time()
    if record["count"] < 5:
        return 0, False
    if now < record["until"]:
        return int(record["until"] - now), True
    return 0, False

def register_violation(user_id):
    record = user_violations.get(user_id, {"count": 0, "until": 0})
    record["count"] += 1
    duration = [60, 300, 900, 3600]
    if record["count"] >= 5:
        stage = record["count"] - 5
        timeout = duration[min(stage, len(duration) - 1)]
        record["until"] = time() + timeout
    user_violations[user_id] = record
    return int(record["until"] - time()) if record["count"] >= 5 else 0

async def check_rate_limit(user_id: int) -> bool:
    r = await get_redis()
    try:
        key = f"rate:{user_id}:{datetime.now().strftime('%Y%m%d%H%M')}"
        count = await r.get(key)
        count = int(count) if count else 0
        if count >= 10:
            logging.warning(f"Rate limit exceeded for user {user_id}: {count} requests")
            return False
        await r.incr(key)
        await r.expire(key, 60)
        return True
    finally:
        await r.aclose()

async def check_daily_limit(user_id: int) -> bool:
    r = await get_redis()
    try:
        key = f"daily:{user_id}:{datetime.now().strftime('%Y%m%d')}"
        count = await r.get(key)
        count = int(count) if count else 0
        if count >= 100:
            logging.warning(f"Daily limit exceeded for user {user_id}: {count} requests")
            return False
        await r.incr(key)
        await r.expire(key, 86400)
        return True
    finally:
        await r.aclose()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    
    # Проверяем простые параметры после /start
    if message.text and len(message.text.split()) > 1:
        param = message.text.split()[1]
        
        # Простая проверка - если это выглядит как домен, проверяем его
        if "." in param and len(param) > 3:
            # Это похоже на домен - просто запускаем проверку в ЛС
            domain = extract_domain(param)
            if domain:
                await message.answer(f"🔍 <b>Проверяю {domain}...</b>")
                await handle_domain_logic(message, domain, short_mode=True)
                return
            else:
                await message.answer(f"❌ Некорректный домен: {param}")
                return
    
    welcome_message = (
        "👋 <b>Привет!</b> Я бот для проверки доменов на пригодность для Reality.\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "/mode — Переключить режим вывода (краткий/полный)\n"
        "/history — Показать последние 10 проверок\n"

    )
    if is_admin:
        admin_commands = [
            "/reset_queue — Сбросить очередь (только для админа)",
            "/clearcache — Очистить кэш результатов",
            "/adminhelp — Показать список админских команд"
        ]
        if SAVE_APPROVED_DOMAINS:
            admin_commands.extend([
                "/approved — Показать список пригодных доменов",
                "/clear_approved — Очистить список пригодных доменов", 
                "/export_approved — Экспортировать список доменов в файл"
            ])
        
        welcome_message += (
            "\n🔧 <b>Админ-команды:</b>\n" + 
            "\n".join(admin_commands) + "\n"
        )
    welcome_message += (
        "\n📩 Для проверки, просто отправь мне свой домен для оценки, например: <code>google.com</code> \n"
        "\n📩 Можно отправить несколько доменов (через запятую или перенос строки), например:\n"
        "<code>example.com, google.com</code>\n"
        "Дневной лимит 100 проверок на пользователя\n"
        "Разработка при участии ИИ и проекта OpeNode.xyz\n\n"
         "🚀 Или выбери действие ниже!\n"
    )
    try:
        await message.answer(welcome_message, reply_markup=get_main_keyboard(is_admin))
    except Exception as e:
        logging.error(f"Failed to send welcome message to user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка при отправке сообщения. Попробуйте позже.")

async def handle_bulk_domains_in_group(message: types.Message, domains: list, user_id: int, short_mode: bool):
    """Обрабатывает массовые запросы доменов в группах - один ответ с кнопками"""
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    
    # Логируем массовый запрос
    await log_analytics("bulk_domain_request", user_id, 
                       details=f"group_chat, domains_count={len(domains)}, short_mode={short_mode}")
    
    # Проверяем, есть ли результаты в кэше
    r = await get_redis()
    try:
        cached_domains = []
        pending_domains = []
        
        for domain in domains:
            cached = await r.get(f"result:{domain}")
            if cached:
                cached_domains.append(domain)
            else:
                pending_domains.append(domain)
        
        # Ставим в очередь те домены, которых нет в кэше
        for domain in pending_domains:
            chat_id = message.chat.id
            message_id = message.message_id
            thread_id = get_topic_thread_id(message)
            
            enqueued = await enqueue(domain, user_id, short_mode=short_mode,
                                   chat_id=chat_id, message_id=message_id, thread_id=thread_id)
            await log_analytics("domain_check", user_id,
                              domain=domain, check_type="short" if short_mode else "full",
                              result_status="queued" if enqueued else "already_queued")
        
        # Создаем кнопки для получения результатов в ЛС
        buttons = []
        bot_info = await bot.get_me()
        bot_username = bot_info.username
        
        # Разбиваем домены на группы по 3 для кнопок
        for i in range(0, len(domains), 3):
            batch = domains[i:i+3]
            row = []
            for domain in batch:
                # Простой диплинк - /start domain (перезапуск проверки в ЛС)
                deep_link = f"https://t.me/{bot_username}?start={domain}"
                row.append(InlineKeyboardButton(
                    text=f"📄 {domain}", 
                    url=deep_link
                ))
            buttons.append(row)
        
        # Убираем кнопку "Все результаты" - она персональная
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        # Если больше одного домена - отправляем только уведомление с кнопками для перехода в ЛС
        if len(domains) > 1:
            # Формируем минимальное сообщение с кнопками
            response_text = (
                f"🔍 <b>Массовая проверка {len(domains)} доменов</b>\n\n"
                f"💡 Результаты будут доступны в ЛС с ботом:"
            )
        else:
            # Для одного домена показываем подробную информацию
            response_text = (
                f"🔍 <b>Обработка {len(domains)} доменов</b>\n\n"
                f"📊 <b>Статус:</b>\n"
                f"• Из кэша: {len(cached_domains)}\n"
                f"• В очереди: {len(pending_domains)}\n\n"
                f"💡 <b>Получить результаты:</b>\n"
                f"Нажмите на кнопки ниже для перехода в ЛС с ботом"
            )
            
            if cached_domains:
                response_text += f"\n\n✅ <b>Готовые:</b> {', '.join(cached_domains[:5])}"
                if len(cached_domains) > 5:
                    response_text += f" и ещё {len(cached_domains) - 5}..."
            
            if pending_domains:
                response_text += f"\n\n⏳ <b>В обработке:</b> {', '.join(pending_domains[:5])}"
                if len(pending_domains) > 5:
                    response_text += f" и ещё {len(pending_domains) - 5}..."
        
        sent_message = await send_topic_aware_message(message, response_text, reply_markup=keyboard)
        
    except Exception as e:
        logging.error(f"Failed to handle bulk domains in group: {e}")
        await send_topic_aware_message(message, f"❌ Ошибка при обработке массового запроса: {str(e)}")
    finally:
        await r.aclose()

async def handle_deep_link_single_result(message: types.Message, domain: str):
    """Обрабатывает запрос результата конкретного домена через deep link"""
    user_id = message.from_user.id
    
    r = await get_redis()
    try:
        cached = await r.get(f"result:{domain}")
        if cached:
            await message.answer(f"📄 <b>Результат для {domain}:</b>\n\n{cached}")
            await log_analytics("domain_check", user_id,
                              domain=domain, check_type="single_result",
                              result_status="cached", execution_time=0)
        else:
            # Проверяем, есть ли домен в очереди (проверяем любого пользователя)
            pending_keys = await r.keys(f"pending:{domain}:*")
            
            if pending_keys:
                await message.answer(
                    f"⏳ <b>Домен {domain} обрабатывается</b>\n\n"
                    f"🔄 Результат будет готов через несколько секунд.\n"
                    f"💡 Попробуйте нажать кнопку снова через 10-30 секунд."
                )
            else:
                await message.answer(
                    f"❌ <b>Результат для {domain} недоступен</b>\n\n"
                    f"💡 <b>Возможные причины:</b>\n"
                    f"• Домен ещё не проверялся\n"
                    f"• Результат устарел и был удален из кэша (24 часа)\n"
                    f"• Произошла ошибка при обработке\n\n"
                    f"🔄 <b>Решение:</b> Запросите проверку заново:\n"
                    f"<code>/check {domain}</code>"
                )
    except Exception as e:
        logging.error(f"Failed to get single result for {domain} by user {user_id}: {str(e)}")
        await message.answer(f"❌ Ошибка при получении результата для {domain}: {str(e)}")
    finally:
        await r.aclose()

async def handle_deep_link_all_results(message: types.Message, user_id: int):
    """Обрабатывает запрос всех результатов пользователя через deep link"""
    r = await get_redis()
    try:
        # Получаем историю пользователя
        history = await r.lrange(f"history:{user_id}", 0, 19)  # Последние 20 записей
        
        if not history:
            await message.answer("📜 У вас пока нет результатов проверок.")
            return
        
        results_text = "📄 <b>Ваши последние результаты:</b>\n\n"
        found_results = 0
        
        for entry in history:
            # Пробуем разные форматы записи в истории
            domain = None
            if " - " in entry:
                domain = entry.split(" - ")[1].strip()
            elif ": " in entry:
                domain = entry.split(": ")[0].strip()
            
            if domain:
                cached = await r.get(f"result:{domain}")
                if cached:
                    found_results += 1
                    # Ограничиваем вывод короткой версией
                    lines = cached.split("\n")[:10]  # Увеличиваем до 10 строк
                    short_result = "\n".join(lines)
                    if len(cached.split("\n")) > 10:
                        short_result += "\n<i>... (показаны первые 10 строк)</i>"
                    
                    results_text += f"🔍 <b>{domain}:</b>\n{short_result}\n\n"
                    
                    # Ограничиваем количество результатов в одном сообщении
                    if found_results >= 3:  # Уменьшаем до 3 для читаемости
                        break
        
        if found_results == 0:
            # Проверяем доступные результаты в кэше
            all_cached_keys = await r.keys("result:*")
            available_domains = []
            for key in all_cached_keys:
                domain_name = key.decode('utf-8').replace('result:', '') if hasattr(key, 'decode') else str(key).replace('result:', '')
                available_domains.append(domain_name)
            
            if available_domains:
                domains_text = ", ".join(available_domains[:10])
                if len(available_domains) > 10:
                    domains_text += f" и ещё {len(available_domains) - 10}..."
                
                await message.answer(
                    f"📜 Ваши недавние результаты больше не в истории, но есть кэшированные результаты:\n\n"
                    f"🔍 <b>Доступные домены:</b> {domains_text}\n\n"
                    f"💡 Используйте команду <code>/check домен</code> для получения результата"
                )
            else:
                await message.answer("📜 Результаты ваших недавних проверок больше не доступны в кэше.")
        else:
            if len(results_text) > 4000:  # Ограничение Telegram
                results_text = results_text[:3900] + "\n\n<i>... (сообщение обрезано)</i>"
            
            await message.answer(results_text)
            
        await log_analytics("all_results_requested", user_id, details=f"found={found_results}")
        
    except Exception as e:
        logging.error(f"Failed to get all results for user {user_id}: {str(e)}")
        await message.answer(f"❌ Ошибка при получении результатов: {str(e)}")
    finally:
        await r.aclose()

async def handle_deep_link_full_report(message: types.Message, domain: str):
    """Обрабатывает запрос полного отчёта через deep link"""
    user_id = message.from_user.id
    
    # Проверяем лимиты
    if not await check_rate_limit(user_id):
        await message.answer("🚫 Слишком много запросов. Не более 10 в минуту.")
        return
        
    if not await check_daily_limit(user_id):
        await message.answer("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
    
    # Проверяем кэш
    r = await get_redis()
    try:
        cached = await r.get(f"result:{domain}")
        if cached and all(k in cached for k in ["🌍 География", "📄 WHOIS", "⏱️ TTFB"]):
            # Отправляем полный отчёт из кэша
            await message.answer(f"📄 Полный отчёт для {domain}:\n\n{cached}")
            await log_analytics("domain_check", user_id,
                              domain=domain, check_type="full",
                              result_status="cached", execution_time=0)
        else:
            # Ставим в очередь на полный отчёт
            enqueued = await enqueue(domain, user_id, short_mode=False, chat_id=user_id)
            if enqueued:
                await message.answer(f"✅ <b>{domain}</b> поставлен в очередь на полный отчёт. Результат придёт сюда.")
                await log_analytics("domain_check", user_id,
                                  domain=domain, check_type="full",
                                  result_status="queued", execution_time=0)
            else:
                await message.answer(f"⚠️ <b>{domain}</b> уже в очереди на проверку.")
    except Exception as e:
        logging.error(f"Failed to process deep link full report for {domain} by user {user_id}: {str(e)}")
        await message.answer(f"❌ Ошибка при обработке запроса для {domain}: {str(e)}")
    finally:
        await r.aclose()

@router.message(Command("mode"))
async def cmd_mode(message: types.Message):
    user_id = message.from_user.id
    r = await get_redis()
    try:
        current_mode = await r.get(f"mode:{user_id}")
        current_mode = current_mode or "short"
        new_mode = "full" if current_mode == "short" else "short"
        await r.set(f"mode:{user_id}", new_mode)
        await message.reply(f"✅ Режим вывода изменён на: {new_mode}")
    except Exception as e:
        logging.error(f"Failed to change mode for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при смене режима.")
    finally:
        await r.aclose()


@router.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = message.from_user.id
    r = await get_redis()
    try:
        history = await r.lrange(f"history:{user_id}", 0, 9)
        if not history:
            await message.reply("📜 Ваша история проверок пуста.")
            return
        response = "📜 <b>Ваши последние проверки (максимум 10):</b>\n"
        for i, entry in enumerate(history, 1):
            response += f"{i}. {entry}\n"
        await message.reply(response)
    except Exception as e:
        logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при получении истории.")
    finally:
        await r.aclose()

@router.message(Command("approved"))
async def cmd_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /approved")
        return
    if not SAVE_APPROVED_DOMAINS:
        await message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        return
    r = await get_redis()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пригодных доменов пуст.")
            return
        response = "📜 <b>Пригодные домены:</b>\n"
        for i, domain in enumerate(sorted(domains), 1):
            response += f"{i}. {domain}\n"
        await message.reply(response)
    except Exception as e:
        logging.error(f"Failed to fetch approved domains for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при получении списка доменов.")
    finally:
        await r.aclose()

@router.message(Command("clear_approved"))
async def cmd_clear_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /clear_approved")
        return
    if not SAVE_APPROVED_DOMAINS:
        await message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        return
    r = await get_redis()
    try:
        deleted = await r.delete("approved_domains")
        await message.reply("✅ Список пригодных доменов очищен." if deleted else "📜 Список пригодных доменов уже пуст.")
    except Exception as e:
        logging.error(f"Failed to clear approved domains for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при очистке списка доменов.")
    finally:
        await r.aclose()

@router.message(Command("export_approved"))
async def cmd_export_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /export_approved")
        return
    if not SAVE_APPROVED_DOMAINS:
        await message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        return
    r = await get_redis()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пригодных доменов пуст. Экспорт не выполнен.")
            return
        file_path = "/app/approved_domains.txt"
        with open(file_path, "w") as f:
            for domain in sorted(domains):
                f.write(f"{domain}\n")
        await message.reply(f"✅ Список доменов экспортирован в {file_path} ({len(domains)} доменов).")
    except Exception as e:
        logging.error(f"Failed to export approved domains for user {user_id}: {str(e)}")
        await message.reply(f"❌ Ошибка при экспорте списка доменов: {str(e)}")
    finally:
        await r.aclose()

@router.message(Command("reset_queue"))
async def reset_queue_command(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        logging.warning(f"Non-admin user {user_id} attempted to reset queue")
        return
    r = await get_redis()
    try:
        queue_count = await r.llen("queue:domains")
        pending_keys = await r.keys("pending:*")
        await r.delete("queue:domains")
        if pending_keys:
            await r.delete(*pending_keys)
        await message.reply(f"✅ Очередь сброшена. Удалено задач: {queue_count}, ключей pending: {len(pending_keys)}.")
    except Exception as e:
        logging.error(f"Failed to reset queue by admin {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при сбросе очереди.")
    finally:
        await r.aclose()

@router.message(Command("clearcache"))
async def clear_cache_command(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        logging.warning(f"Non-admin user {user_id} attempted to access /clearcache")
        return
    r = await get_redis()
    try:
        keys = await r.keys("result:*")
        if keys:
            await r.delete(*keys)
            await message.reply(f"✅ Кэш очищен. Удалено {len(keys)} записей.")
        else:
            await message.reply("✅ Кэш уже пуст.")
    except Exception as e:
        logging.error(f"Failed to clear cache for user {user_id}: {str(e)}")
        await message.reply(f"❌ Ошибка при очистке кэша: {str(e)}")
    finally:
        await r.aclose()

@router.message(Command("adminhelp"))
async def admin_help_command(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        logging.warning(f"Non-admin user {user_id} attempted to access /adminhelp")
        return
    admin_commands = ["📋 <b>Доступные админские команды:</b>\n"]
    admin_commands.extend([
        "/reset_queue — Сбросить очередь",
        "/clearcache — Очистить кэш результатов",
        "/analytics — Показать аналитику бота (NEW!)",
        "/groups — Управление авторизованными группами (NEW!)",
        "/groups_add <ID> — Добавить группу в авторизованные",
        "/groups_remove <ID> — Удалить группу из авторизованных", 
        "/groups_current — Показать ID текущей группы",
        "/adminhelp — Показать этот список команд"
    ])
    
    if SAVE_APPROVED_DOMAINS:
        admin_commands.extend([
            "/approved — Показать список пригодных доменов",
            "/clear_approved — Очистить список пригодных доменов",
            "/export_approved — Экспортировать список доменов в файл"
        ])
    
    await message.reply("\n".join(admin_commands))
    logging.info(f"Admin {user_id} viewed admin commands list")

@router.message(Command("analytics"))
async def analytics_command(message: types.Message):
    """Команда для получения аналитики (только для админа)"""
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        logging.warning(f"Non-admin user {user_id} attempted to access /analytics")
        return
        
    # Проверяем доступность модуля аналитики
    if not ANALYTICS_AVAILABLE:
        await message.reply("❌ Модуль аналитики недоступен. Проверьте зависимости (redis).")
        return
        
    # Проверяем инициализацию коллектора
    if not analytics_collector:
        await message.reply("❌ Аналитика не инициализирована. Возможно, проблемы с подключением к Redis.\n\n💡 Проверьте:\n• Запущен ли Redis сервер\n• Правильность настроек подключения\n• Переменные окружения REDIS_HOST, REDIS_PORT")
        return
        
    try:
        # Получаем отчет по аналитике
        report = await analytics_collector.generate_analytics_report(user_id)
        await message.reply(report)
        
        # Логируем запрос аналитики
        await log_analytics("analytics_requested", user_id)
        logging.info(f"Admin {user_id} requested analytics report")
        
    except Exception as e:
        logging.error(f"Failed to generate analytics for user {user_id}: {str(e)}")
        await message.reply(f"❌ Ошибка при генерации аналитики: {str(e)}\n\n💡 Возможные причины:\n• Проблемы с подключением к Redis\n• Недостаток данных для отчета")

@router.message(Command("groups"))
async def groups_command(message: types.Message):
    """Команда для управления авторизованными группами (только для админа)"""
    user_id = message.from_user.id
    
    # Отладочная информация
    logging.info(f"Groups command called by user {user_id}, ADMIN_ID={ADMIN_ID}")
    
    if user_id != ADMIN_ID:
        await message.reply(f"⛔ Эта команда доступна только администратору.\n🐛 Отладка: ваш ID={user_id}, ADMIN_ID={ADMIN_ID}")
        logging.warning(f"Non-admin user {user_id} attempted to access /groups")
        return
    
    # Информация о текущих настройках
    if not GROUP_MODE_ENABLED:
        await message.reply("ℹ️ Режим работы в группах отключен (GROUP_MODE_ENABLED=false)")
        return
    
    if not AUTHORIZED_GROUPS:
        status = "🌐 <b>Режим авторизации групп:</b> Открытый (любые группы)\n"
    else:
        status = f"🔒 <b>Режим авторизации групп:</b> Ограниченный ({len(AUTHORIZED_GROUPS)} групп)\n"
        status += "📋 <b>Авторизованные группы:</b>\n"
        for group_id in sorted(AUTHORIZED_GROUPS):
            try:
                chat = await bot.get_chat(group_id)
                group_name = chat.title or "Без названия"
                status += f"• {group_name} (<code>{group_id}</code>)\n"
            except Exception:
                status += f"• ID: <code>{group_id}</code> (недоступна)\n"
    
    status += f"\n⚙️ <b>Автовыход:</b> {'Включен' if AUTO_LEAVE_UNAUTHORIZED else 'Отключен'}\n"
    status += f"🔧 <b>Префикс команд:</b> <code>{GROUP_COMMAND_PREFIX}</code>\n\n"
    
    status += "📋 <b>Команды управления:</b>\n"
    status += "/groups_add <ID> — Добавить группу\n"
    status += "/groups_remove <ID> — Удалить группу\n"
    status += "/groups_current — Показать ID текущей группы\n"
    
    await message.reply(status)

@router.message(Command("groups_add"))
async def groups_add_command(message: types.Message):
    """Добавить группу в список авторизованных"""
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        return
    
    # Извлекаем ID группы из команды
    command_parts = message.text.split()
    if len(command_parts) < 2:
        await message.reply("❌ Укажите ID группы: /groups_add -1001234567890")
        return
    
    try:
        group_id = int(command_parts[1])
    except ValueError:
        await message.reply("❌ Неверный формат ID группы. Должно быть число.")
        return
    
    # Добавляем в память (требует перезапуска для постоянного сохранения)
    AUTHORIZED_GROUPS.add(group_id)
    
    try:
        chat = await bot.get_chat(group_id)
        group_name = chat.title or "Без названия"
        await message.reply(f"✅ Группа '{group_name}' (ID: <code>{group_id}</code>) добавлена в список авторизованных.\n\n⚠️ Для постоянного сохранения добавьте ID в переменную AUTHORIZED_GROUPS и перезапустите бота.")
    except Exception as e:
        await message.reply(f"✅ ID <code>{group_id}</code> добавлен в список авторизованных.\n⚠️ Не удалось получить информацию о группе: {e}\n\n⚠️ Для постоянного сохранения добавьте ID в переменную AUTHORIZED_GROUPS и перезапустите бота.")

@router.message(Command("groups_remove"))
async def groups_remove_command(message: types.Message):
    """Удалить группу из списка авторизованных"""
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        return
    
    command_parts = message.text.split()
    if len(command_parts) < 2:
        await message.reply("❌ Укажите ID группы: /groups_remove -1001234567890")
        return
    
    try:
        group_id = int(command_parts[1])
    except ValueError:
        await message.reply("❌ Неверный формат ID группы. Должно быть число.")
        return
    
    if group_id in AUTHORIZED_GROUPS:
        AUTHORIZED_GROUPS.remove(group_id)
        await message.reply(f"✅ ID <code>{group_id}</code> удален из списка авторизованных.\n\n⚠️ Для постоянного сохранения обновите переменную AUTHORIZED_GROUPS и перезапустите бота.")
    else:
        await message.reply(f"❌ ID <code>{group_id}</code> не найден в списке авторизованных групп.")

@router.message(Command("groups_current"))
async def groups_current_command(message: types.Message):
    """Показать ID текущей группы"""
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Эта команда доступна только администратору.")
        return
    
    if is_group_chat(message):
        chat_id = message.chat.id
        is_authorized = is_authorized_group(chat_id)
        status_emoji = "✅" if is_authorized else "❌"
        status_text = "авторизована" if is_authorized else "НЕ авторизована"
        
        await message.reply(
            f"ℹ️ <b>Информация о группе:</b>\n"
            f"📝 Название: {message.chat.title}\n"
            f"🆔 ID: <code>{chat_id}</code>\n"
            f"{status_emoji} Статус: {status_text}\n\n"
            f"💡 Для авторизации: /groups_add {chat_id}"
        )
    else:
        await message.reply("ℹ️ Эта команда работает только в группах. ID этого чата: <code>" + str(message.chat.id) + "</code>")

@router.message(Command("check", "full"))
async def cmd_check(message: types.Message):
    user_id = message.from_user.id
    command_text = message.text.strip()
    command = command_text.split()[0]
    short_mode = command == "/check"
    args = command_text[len(command):].strip()
    
    # Проверяем, это групповой чат
    if is_group_chat(message) and not await should_respond_in_group(message):
        return
    
    if not args:
        response = f"⛔ Укажи домен, например: {command} example.com"
        if is_group_chat(message):
            response += f"\n\n💡 В группах также можно использовать: {GROUP_COMMAND_PREFIX}check example.com"
        await send_topic_aware_message(message, response)
        return
        
    if not await check_rate_limit(user_id):
        await send_topic_aware_message(message, "🚫 Слишком много запросов. Не более 10 в минуту.")
        return
        
    if not await check_daily_limit(user_id):
        await send_topic_aware_message(message, "🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
        
    await log_analytics("command_used", user_id, details=f"{command} {args}")
    await handle_domain_logic(message, args, short_mode=short_mode)
    logging.info(f"User {user_id} executed {command} with args: {args}")

@router.message()
async def handle_domain(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    # Обработка групповых команд с префиксом
    if is_group_chat(message) and GROUP_MODE_ENABLED and text.startswith(GROUP_COMMAND_PREFIX):
        await handle_group_commands(message)
        return
    
    # В группах отвечаем только если это упоминание или ответ
    if is_group_chat(message) and not await should_respond_in_group(message):
        return
    
    if not text or text.startswith("/"):
        return
        
    if not await check_rate_limit(user_id):
        await message.reply("🚫 Слишком много запросов. Не более 10 в минуту.")
        return
        
    if not await check_daily_limit(user_id):
        await message.reply("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
        
    await log_analytics("domain_message", user_id, details=text)
    await handle_domain_logic(message, text, short_mode=True)

async def handle_group_commands(message: types.Message):
    """Обрабатывает команды в группах с префиксом"""
    text = message.text or ""
    if not text.startswith(GROUP_COMMAND_PREFIX):
        return
        
    # Удаляем префикс и обрабатываем как обычную команду
    command_without_prefix = text[len(GROUP_COMMAND_PREFIX):]
    
    # Определяем тип команды
    if command_without_prefix.startswith("check ") or command_without_prefix == "check":
        short_mode = True
        args = command_without_prefix[5:].strip() if len(command_without_prefix) > 5 else ""
    elif command_without_prefix.startswith("full ") or command_without_prefix == "full":
        short_mode = False
        args = command_without_prefix[4:].strip() if len(command_without_prefix) > 4 else ""
    elif command_without_prefix.startswith("help") or command_without_prefix == "help":
        await handle_group_help(message)
        return
    else:
        # Возможно, это просто домен для проверки
        if extract_domain(command_without_prefix):
            short_mode = True
            args = command_without_prefix
        else:
            return
    
    user_id = message.from_user.id
    
    if not args:
        await send_topic_aware_message(message,
            f"⛔ Укажи домен, например: {GROUP_COMMAND_PREFIX}check example.com\n"
            f"💡 Доступные команды:\n"
            f"• {GROUP_COMMAND_PREFIX}check example.com — краткая проверка\n"
            f"• {GROUP_COMMAND_PREFIX}full example.com — полная проверка\n"
            f"• {GROUP_COMMAND_PREFIX}help — помощь"
        )
        return
        
    if not await check_rate_limit(user_id):
        await send_topic_aware_message(message, "🚫 Слишком много запросов. Не более 10 в минуту.")
        return
        
    if not await check_daily_limit(user_id):
        await send_topic_aware_message(message, "🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
    
    await log_analytics("group_command_used", user_id, details=f"{command_without_prefix}")
    await handle_domain_logic(message, args, short_mode=short_mode)
    
async def handle_group_help(message: types.Message):
    """Показывает помощь для групповых команд"""
    bot_info = await bot.get_me()
    help_text = (
        f"🤖 <b>Помощь по командам бота</b>\n\n"
        f"📋 <b>Доступные команды в группе:</b>\n"
        f"• {GROUP_COMMAND_PREFIX}check example.com — Краткая проверка домена\n"
        f"• {GROUP_COMMAND_PREFIX}full example.com — Полная проверка домена\n"
        f"• {GROUP_COMMAND_PREFIX}help — Показать эту справку\n\n"
        f"💡 <b>Также можно:</b>\n"
        f"• Упомянуть бота: @{bot_info.username} example.com\n"
        f"• Ответить на сообщение бота с доменом\n\n"
        f"📊 Лимиты: 10 проверок в минуту, 100 в день на пользователя\n\n"
        f"🧵 <b>Поддержка тем:</b> Бот отвечает в той же теме, где его упомянули"
    )
    await send_topic_aware_message(message, help_text)

@router.callback_query()
async def process_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin = user_id == ADMIN_ID
    if callback_query.data == "check":
        await callback_query.message.answer("⛔ Укажи домен, например: /check example.com")
    elif callback_query.data == "full":
        await callback_query.message.answer("⛔ Укажи домен, например: /full example.com")
    elif callback_query.data == "ping":
        await callback_query.message.answer("🏓 Я жив!")
    elif callback_query.data == "mode":
        r = await get_redis()
        try:
            current_mode = await r.get(f"mode:{user_id}")
            current_mode = current_mode or "short"
            new_mode = "full" if current_mode == "short" else "short"
            await r.set(f"mode:{user_id}", new_mode)
            await callback_query.message.reply(f"✅ Режим вывода изменён на: {new_mode}")
        except Exception as e:
            logging.error(f"Failed to change mode for user {user_id} via callback: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при смене режима.")
        finally:
            await r.aclose()
    elif callback_query.data == "history":
        r = await get_redis()
        try:
            history = await r.lrange(f"history:{user_id}", 0, 9)
            if not history:
                await callback_query.message.reply("📜 Ваша история проверок пуста.")
            else:
                response = "📜 <b>Ваши последние проверки (максимум 10):</b>\n"
                for i, entry in enumerate(history, 1):
                    response += f"{i}. {entry}\n"
                await callback_query.message.reply(response)
        except Exception as e:
            logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при получении истории.")
        finally:
            await r.aclose()
    elif callback_query.data == "approved" and is_admin:
        if not SAVE_APPROVED_DOMAINS:
            await callback_query.message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        else:
            r = await get_redis()
            try:
                domains = await r.smembers("approved_domains")
                if not domains:
                    await callback_query.message.reply("📜 Список пригодных доменов пуст.")
                else:
                    response = "📜 <b>Пригодные домены:</b>\n"
                    for i, domain in enumerate(sorted(domains), 1):
                        response += f"{i}. {domain}\n"
                    await callback_query.message.reply(response)
            except Exception as e:
                logging.error(f"Failed to fetch approved domains for user {user_id}: {str(e)}")
                await callback_query.message.reply("❌ Ошибка при получении списка доменов.")
            finally:
                await r.aclose()
            
    elif callback_query.data == "clearcache" and is_admin:
        r = await get_redis()
        try:
            keys = await r.keys("result:*")
            if keys:
                await r.delete(*keys)
                await callback_query.message.reply(f"✅ Кэш очищен. Удалено {len(keys)} записей.")
            else:
                await callback_query.message.reply("✅ Кэш уже пуст.")
        except Exception as e:
            logging.error(f"Failed to clear cache via callback for user {user_id}: {str(e)}")
            await callback_query.message.reply(f"❌ Ошибка при очистке кэша: {str(e)}")
        finally:
            await r.aclose()

    elif callback_query.data == "clear_approved" and is_admin:
        if not SAVE_APPROVED_DOMAINS:
            await callback_query.message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        else:
            r = await get_redis()
            try:
                deleted = await r.delete("approved_domains")
                await callback_query.message.reply("✅ Список пригодных доменов очищен." if deleted else "📜 Список пригодных доменов уже пуст.")
            except Exception as e:
                logging.error(f"Failed to clear approved domains for user {user_id}: {str(e)}")
                await callback_query.message.reply("❌ Ошибка при очистке списка доменов.")
            finally:
                await r.aclose()
    elif callback_query.data == "export_approved" and is_admin:
        if not SAVE_APPROVED_DOMAINS:
            await callback_query.message.reply("⛔ Функция сохранения доменов отключена в настройках.")
        else:
            r = await get_redis()
            try:
                domains = await r.smembers("approved_domains")
                if not domains:
                    await callback_query.message.reply("📜 Список пригодных доменов пуст. Экспорт не выполнен.")
                else:
                    file_path = "/app/approved_domains.txt"
                    with open(file_path, "w") as f:
                        for domain in sorted(domains):
                            f.write(f"{domain}\n")
                    await callback_query.message.reply(f"✅ Список доменов экспортирован в {file_path} ({len(domains)} доменов).")
            except Exception as e:
                logging.error(f"Failed to export approved domains for user {user_id}: {str(e)}")
                await callback_query.message.reply(f"❌ Ошибка при экспорте списка доменов: {str(e)}")
            finally:
                await r.aclose()
    elif callback_query.data == "reset_queue" and is_admin:
        r = await get_redis()
        try:
            queue_count = await r.llen("queue:domains")
            pending_keys = await r.keys("pending:*")
            await r.delete("queue:domains")
            if pending_keys:
                await r.delete(*pending_keys)
            await callback_query.message.reply(f"✅ Очередь сброшена. Удалено задач: {queue_count}, ключей pending: {len(pending_keys)}.")
        except Exception as e:
            logging.error(f"Failed to reset queue by admin {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при сбросе очереди.")
        finally:
            await r.aclose()
    elif callback_query.data.startswith("full_report:"):
        domain = callback_query.data.split(":", 1)[1]
        r = await get_redis()
        try:
            cached = await r.get(f"result:{domain}")
            if cached and all(k in cached for k in ["🌍 География", "📄 WHOIS", "⏱️ TTFB"]):
                await callback_query.message.answer(f"⚡ Полный отчёт для {domain}:\n\n{cached}")
            else:
                if not await check_rate_limit(user_id):
                    await callback_query.message.answer("🚫 Слишком много запросов. Не более 10 в минуту.")
                elif not await check_daily_limit(user_id):
                    await callback_query.message.answer("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
                else:
                    # Полный отчёт всегда отправляем в ЛС, даже если запрос из группы
                    enqueued = await enqueue(domain, user_id, short_mode=False, 
                                           chat_id=user_id)  # Принудительно в ЛС
                    if enqueued:
                        await callback_query.message.answer(f"✅ <b>{domain}</b> поставлен в очередь на полный отчёт в ЛС.")
                    else:
                        await callback_query.message.answer(f"⚠️ <b>{domain}</b> уже в очереди на проверку.")
        except Exception as e:
            logging.error(f"Failed to process full report for {domain} by user {user_id}: {str(e)}")
            await callback_query.message.answer(f"❌ Ошибка: {str(e)}")
        finally:
            await r.aclose()
    elif callback_query.data.startswith("full_pm:"):
        # Новый callback для получения полного отчёта в ЛС из группы
        parts = callback_query.data.split(":", 2)
        if len(parts) >= 3:
            domain = parts[1]
            target_user_id = int(parts[2])
            
            # Проверяем, что пользователь имеет право на этот отчёт
            if user_id != target_user_id:
                await callback_query.answer("❌ Этот отчёт предназначен не для вас", show_alert=True)
                return
            
            # Проверяем лимиты
            if not await check_rate_limit(user_id):
                await callback_query.answer("🚫 Слишком много запросов. Не более 10 в минуту.", show_alert=True)
                return
            if not await check_daily_limit(user_id):
                await callback_query.answer("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.", show_alert=True)
                return
            
            r = await get_redis()
            try:
                cached = await r.get(f"result:{domain}")
                if cached and all(k in cached for k in ["🌍 География", "📄 WHOIS", "⏱️ TTFB"]):
                    # Пытаемся отправить полный отчёт в ЛС
                    try:
                        await bot.send_message(user_id, f"📄 Полный отчёт для {domain}:\n\n{cached}")
                        await callback_query.answer("✅ Полный отчёт отправлен в ЛС")
                    except Exception as pm_error:
                        # Не удалось отправить в ЛС
                        await callback_query.answer(
                            "❌ Не удалось отправить в ЛС. Начните диалог с ботом командой /start", 
                            show_alert=True
                        )
                        logging.warning(f"Failed to send PM to user {user_id} via callback: {pm_error}")
                else:
                    # Ставим в очередь на полный отчёт в ЛС
                    enqueued = await enqueue(domain, user_id, short_mode=False, chat_id=user_id)
                    if enqueued:
                        await callback_query.answer("✅ Запрос на полный отчёт принят. Результат придёт в ЛС")
                    else:
                        await callback_query.answer("⚠️ Домен уже в очереди на проверку")
            except Exception as e:
                logging.error(f"Failed to process full_pm for {domain} by user {user_id}: {str(e)}")
                await callback_query.answer(f"❌ Ошибка: {str(e)}", show_alert=True)
            finally:
                await r.aclose()
        else:
            await callback_query.answer("❌ Неверный формат запроса", show_alert=True)
    else:
        await callback_query.message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted unauthorized callback: {callback_query.data}")
    await callback_query.answer()

async def handle_domain_logic(message: types.Message, input_text: str, inconclusive_domain_limit=5, short_mode: bool = True):
    user_id = message.from_user.id
    penalty, active = get_penalty(user_id)
    if active:
        await message.reply(f"🚫 Вы ограничены на {penalty//60} минут.")
        return

    r = await get_redis()
    try:
        user_mode = await r.get(f"mode:{user_id}")
        short_mode = user_mode != "full" if user_mode else short_mode
    finally:
        await r.aclose()

    domains = [d.strip() for d in re.split(r'[,\n]', input_text) if d.strip()]
    if not domains:
        await message.reply("❌ Не удалось извлечь домены. Укажите валидные домены, например: example.com")
        return

    r = await get_redis()
    try:
        valid_domains = []
        invalid_domains = []
        for domain in domains:
            extracted = extract_domain(domain)
            if extracted:
                valid_domains.append(extracted)
            else:
                invalid_domains.append(domain)
                logging.warning(f"Invalid domain input: {domain} by user {user_id}")

        if invalid_domains:
            await send_topic_aware_message(message,
                f"⚠️ Следующие домены невалидны и будут пропущены:\n" +
                "\n".join(f"- {d}" for d in invalid_domains)
            )

        if not valid_domains:
            if len(invalid_domains) >= inconclusive_domain_limit:
                timeout = register_violation(user_id)
                await send_topic_aware_message(message, f"❌ Все домены невалидны. Пользователь ограничен на {timeout//60} минут.")
            else:
                await send_topic_aware_message(message, "❌ Не найдено валидных доменов. Укажите корректные домены, например: example.com")
            return

        # Для массовых запросов (более 1 домена) в группах - просто ставим в очередь, НЕ отправляем уведомления
        if len(valid_domains) > 1 and is_group_chat(message):
            # Просто ставим в очередь без всяких сообщений
            for domain in valid_domains:
                chat_id = message.chat.id
                message_id = message.message_id
                thread_id = get_topic_thread_id(message)
                
                await enqueue(domain, user_id, short_mode=short_mode,
                             chat_id=chat_id, message_id=message_id, thread_id=thread_id)
            
            # Отправляем ТОЛЬКО короткое сообщение в группу с кнопками для получения результатов в ЛС
            try:
                bot_info = await bot.get_me()
                bot_username = bot_info.username
                
                # Создаем простые кнопки - каждая просто запускает /start domain.com в ЛС
                buttons = []
                for i in range(0, len(valid_domains), 3):
                    batch = valid_domains[i:i+3]
                    row = []
                    for domain in batch:
                        # Простой диплинк - /start domain.com (перезапуск проверки в ЛС)
                        deep_link = f"https://t.me/{bot_username}?start={domain}"
                        row.append(InlineKeyboardButton(text=f"📄 {domain}", url=deep_link))
                    buttons.append(row)
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                
                # Короткое сообщение в группе
                group_message = (
                    f"🔍 <b>Массовая проверка {len(valid_domains)} доменов</b>\n\n"
                    f"� <b>Получить результаты:</b>\n"
                    f"Нажмите на кнопки ниже для перехода в ЛС с ботом"
                )
                
                await send_topic_aware_message(message, group_message, reply_markup=keyboard)
                
            except Exception as e:
                logging.error(f"Failed to send group notification for bulk request: {e}")
            
            return
        
        # Если доменов много и доступен BatchProcessor, используем его (только в ЛС)
        if len(valid_domains) > 2 and PROGRESS_AVAILABLE and not is_group_chat(message):
            try:
                batch_processor = BatchProcessor(bot, batch_size=3, progress_update_delay=0.8)
                
                async def check_domain_wrapper(domain, user_id, short_mode):
                    """Обертка для проверки домена с логированием аналитики"""
                    start_time = time()
                    try:
                        # Проверяем кэш
                        r = await get_redis()
                        cached = await r.get(f"result:{domain}")
                        await r.aclose()
                        
                        if cached:
                            await log_analytics("domain_check", user_id, 
                                              domain=domain, check_type="short" if short_mode else "full", 
                                              result_status="cached", execution_time=time() - start_time)
                            return f"✅ {domain} - результат из кэша"
                        
                        # Ставим в очередь с контекстом
                        chat_id = message.chat.id
                        message_id = message.message_id
                        thread_id = get_topic_thread_id(message)
                        
                        enqueued = await enqueue(domain, user_id, short_mode=short_mode,
                                               chat_id=chat_id, message_id=message_id, thread_id=thread_id)
                        if enqueued:
                            await log_analytics("domain_check", user_id,
                                              domain=domain, check_type="short" if short_mode else "full",
                                              result_status="queued", execution_time=time() - start_time)
                            return f"✅ {domain} - поставлен в очередь"
                        else:
                            return f"⚠️ {domain} - уже в очереди"
                            
                    except Exception as e:
                        await log_analytics("domain_check", user_id,
                                          domain=domain, check_type="short" if short_mode else "full",
                                          result_status="failed", execution_time=time() - start_time)
                        raise e
                
                # Используем батч-обработку с прогрессом
                results = await batch_processor.process_domains(
                    valid_domains, user_id, message, check_domain_wrapper, short_mode
                )
                
                # Отправляем итоговую статистику
                summary = (
                    f"📊 <b>Обработка завершена:</b>\n"
                    f"• Обработано: {len(results['successful']) + len(results['cached'])}\n"
                    f"• Из кэша: {len(results['cached'])}\n"
                    f"• Неудач: {len(results['failed'])}\n"
                )
                
                if results['errors']:
                    summary += f"\n❌ <b>Ошибки:</b>\n" + "\n".join(f"• {error}" for error in results['errors'][:3])
                    if len(results['errors']) > 3:
                        summary += f"\n... и еще {len(results['errors']) - 3} ошибок"
                
                await send_topic_aware_message(message, summary)
                return
                
            except Exception as e:
                logging.error(f"Batch processing failed: {e}, falling back to individual processing")
        
        # Обычная обработка доменов (по одному)
        for domain in valid_domains:
            start_time = time()
            cached = await r.get(f"result:{domain}")
            is_full_report = cached and all(k in cached for k in ["🌍 География", "📄 WHOIS", "⏱️ TTFB"])
            if cached and (short_mode or is_full_report):
                if short_mode:
                    lines = cached.split("\n")
                    filtered_lines = []
                    include_next = False
                    for line in lines:
                        if any(k in line for k in ["🟢 Ping", "🔒 TLS", "🌐 HTTP", "🛡", "🟢 CDN", "🛰 Оценка пригодности"]):
                            filtered_lines.append(line)
                            include_next = True  # Включаем следующую строку (например, после "🔒 TLS")
                        elif include_next and line.strip().startswith(("✅", "❌", "⏳")):
                            filtered_lines.append(line)
                            include_next = False
                        else:
                            include_next = False
                    filtered = "\n".join(filtered_lines)
                    
                    # Выбираем правильную кнопку в зависимости от типа чата
                    if is_group_chat(message):
                        keyboard = get_group_full_report_button(domain, user_id)
                    else:
                        keyboard = get_full_report_button(domain)
                    
                    await send_topic_aware_message(message,
                        f"⚡ Результат из кэша для {domain}:\n\n{filtered}",
                        reply_markup=keyboard
                    )
                    await log_analytics("domain_check", user_id,
                                      domain=domain, check_type="short" if short_mode else "full",
                                      result_status="cached", execution_time=time() - start_time)
                    logging.info(f"Returned cached short report for {domain} to user {user_id}")
                else:
                    await send_topic_aware_message(message, f"⚡ Полный результат из кэша для {domain}:\n\n{cached}")
                    await log_analytics("domain_check", user_id,
                                      domain=domain, check_type="full",
                                      result_status="cached", execution_time=time() - start_time)
                    logging.info(f"Returned cached full report for {domain} to user {user_id}")
                await r.lpush(f"history:{user_id}", f"{datetime.now().strftime('%Y-%m-%d %H:%M')} - {domain}")
                await r.ltrim(f"history:{user_id}", 0, 9)
            else:
                # Передаём контекст чата при постановке в очередь
                chat_id = message.chat.id
                message_id = message.message_id
                thread_id = get_topic_thread_id(message)
                
                enqueued = await enqueue(domain, user_id, short_mode=short_mode,
                                       chat_id=chat_id, message_id=message_id, thread_id=thread_id)
                if enqueued:
                    await send_topic_aware_message(message, f"✅ <b>{domain}</b> поставлен в очередь на {'краткий' if short_mode else 'полный'} отчёт.")
                    await log_analytics("domain_check", user_id,
                                      domain=domain, check_type="short" if short_mode else "full",
                                      result_status="queued", execution_time=time() - start_time)
                else:
                    await send_topic_aware_message(message, f"⚠️ <b>{domain}</b> уже в очереди на проверку.")
                logging.info(f"Enqueued {domain} for user {user_id} (short_mode={short_mode})")
    except Exception as e:
        logging.error(f"Failed to process domains for user {user_id}: {str(e)}")
        await send_topic_aware_message(message, f"❌ Ошибка: {str(e)}")
    finally:
        await r.aclose()

async def main():
    """Основная функция запуска бота"""
    from aiogram import Dispatcher
    
    # Инициализируем аналитику
    await init_analytics()
    
    dp = Dispatcher()
    dp.include_router(router)
    
    try:
        logging.info("🚀 Starting Domain Reality Bot...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Error starting bot: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    logging.info("Starting bot script")
    asyncio.run(main())
