from typing import Optional, Any, Union, List
import asyncio
import json
from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import BotCommand, FSInputFile, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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
from localization import i18n, _

# --- Conditional Imports ---
try:
    from progress_tracker import BatchProcessor
    PROGRESS_AVAILABLE = True
except ImportError:
    PROGRESS_AVAILABLE = False

try:
    import analytics
    ANALYTICS_AVAILABLE = True
except ImportError:
    ANALYTICS_AVAILABLE = False
    analytics = None

class AnalyticsCollector:
    """Analytics collector that works with or without the analytics module."""
    def __init__(self, *args, **kwargs):
        if ANALYTICS_AVAILABLE and analytics:
            try:
                self._real_collector = analytics.AnalyticsCollector(*args, **kwargs)
                logging.info("Real analytics collector initialized")
            except Exception as e:
                logging.warning(f"Failed to init real analytics: {e}")
                self._real_collector = None
        else:
            self._real_collector = None
            logging.warning("Using dummy analytics collector")
    
    async def log_user_activity(self, *args, **kwargs):
        if self._real_collector:
            return await self._real_collector.log_user_activity(*args, **kwargs)
    
    async def generate_analytics_report(self, *args, **kwargs):
        if self._real_collector:
            return await self._real_collector.generate_analytics_report()
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
GROUP_OUTPUT_MODE = os.getenv("GROUP_OUTPUT_MODE", "short").lower()  # "short" или "full"
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
analytics_collector = None
redis_pool = None  # Глобальный пул соединений Redis

# --- Redis Connection Pool ---
async def init_redis_pool():
    """Инициализирует пул соединений Redis"""
    global redis_pool
    try:
        redis_pool = redis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            max_connections=20,  # Максимум 20 соединений в пуле
            retry_on_timeout=True
        )
        # Проверяем соединение
        test_conn = redis.Redis(connection_pool=redis_pool)
        await test_conn.ping()
        await test_conn.aclose()
        logging.info("✅ Redis connection pool initialized successfully")
    except Exception as e:
        logging.error(f"❌ Failed to initialize Redis pool: {e}")
        raise

async def get_redis_connection() -> redis.Redis:
    """Возвращает соединение из пула"""
    try:
        if redis_pool is None:
            await init_redis_pool()
        return redis.Redis(connection_pool=redis_pool)
    except Exception as e:
        logging.error(f"❌ Failed to get Redis connection from pool: {e}")
        raise

async def close_redis_pool():
    """Закрывает пул соединений Redis"""
    global redis_pool
    if redis_pool:
        try:
            await redis_pool.disconnect()
            logging.info("✅ Redis pool closed successfully")
        except Exception as e:
            logging.error(f"❌ Error closing Redis pool: {e}")

# --- Language Management ---
async def get_user_language(user_id: int) -> str:
    """Получает язык пользователя из Redis или определяет автоматически"""
    r = await get_redis_connection()
    try:
        lang = await r.get(f"user:lang:{user_id}")
        if lang and i18n.is_supported(lang):
            return lang
        return i18n.default_lang
    except Exception as e:
        logging.error(f"Error getting user language: {e}")
        return i18n.default_lang
    finally:
        try:
            await r.aclose()
        except:
            pass

async def set_user_language(user_id: int, lang: str):
    """Сохраняет выбранный язык пользователя в Redis"""
    if not i18n.is_supported(lang):
        lang = i18n.default_lang
    
    r = await get_redis_connection()
    try:
        await r.set(f"user:lang:{user_id}", lang)
        logging.info(f"User {user_id} language set to: {lang}")
    except Exception as e:
        logging.error(f"Error setting user language: {e}")
    finally:
        try:
            await r.aclose()
        except:
            pass

async def init_user_language(user: types.User) -> str:
    """Инициализирует язык пользователя при первом запуске"""
    user_id = user.id
    current_lang = await get_user_language(user_id)
    
    # Если язык уже установлен, возвращаем его
    if current_lang != i18n.default_lang:
        return current_lang
    
    # Пытаемся определить из Telegram
    if user.language_code:
        detected_lang = i18n.normalize_language_code(user.language_code)
        await set_user_language(user_id, detected_lang)
        return detected_lang
    
    return i18n.default_lang

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

def is_valid_ipv4(ip_str: str) -> bool:
    """Проверка, является ли строка валидным IPv4 адресом"""
    if not isinstance(ip_str, str):
        return False
    
    ip_str = ip_str.strip()
    
    # Проверка регулярным выражением
    pattern = r'^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$'
    if not re.match(pattern, ip_str):
        return False
    
    # Дополнительная проверка через модуль ipaddress
    try:
        import ipaddress
        ip = ipaddress.IPv4Address(ip_str)
        return True
    except (ValueError, ipaddress.AddressValueError):
        return False

async def get_ip_info(ip_address: str, lang: str = 'ru') -> str:
    """
    Получение информации об IP адресе из GeoIP2 базы данных.
    
    Args:
        ip_address: IPv4 адрес для проверки
        lang: Язык вывода (ru/en)
    
    Returns:
        Форматированная строка с информацией об IP адресе
    """
    # Известные публичные IP адреса
    known_ips = {
        "1.1.1.1": {"name": "Cloudflare DNS", "org": "Cloudflare, Inc.", "country": "US", "city": "San Francisco"},
        "8.8.8.8": {"name": "Google Public DNS", "org": "Google LLC", "country": "US", "city": "Mountain View"},
        "8.8.4.4": {"name": "Google Public DNS", "org": "Google LLC", "country": "US", "city": "Mountain View"},
        "1.0.0.1": {"name": "Cloudflare DNS", "org": "Cloudflare, Inc.", "country": "US", "city": "San Francisco"},
        "208.67.222.222": {"name": "OpenDNS", "org": "Cisco OpenDNS", "country": "US", "city": "San Francisco"},
        "208.67.220.220": {"name": "OpenDNS", "org": "Cisco OpenDNS", "country": "US", "city": "San Francisco"},
    }
    
    # Проверяем, является ли IP известным публичным сервисом
    if ip_address in known_ips:
        info = known_ips[ip_address]
        ip_emoji = "🌐"
        country_emoji = "🌍"
        city_emoji = "🏙️"
        org_emoji = "🏢"
        service_emoji = "⚡"
        
        lines = [
            i18n.get('ip.title', lang),
            "",
            f"{ip_emoji} {i18n.get('ip.address', lang)}: `{ip_address}`",
            f"{service_emoji} {i18n.get('ip.service', lang) if i18n.is_supported(lang) else 'Service'}: {info['name']}",
            f"{org_emoji} {i18n.get('ip.organization', lang) if i18n.is_supported(lang) else 'Organization'}: {info['org']}",
            f"{country_emoji} {i18n.get('ip.country', lang)}: {info['country']}",
            f"{city_emoji} {i18n.get('ip.city', lang)}: {info['city']}",
        ]
        return "\n".join(lines)
    
    try:
        import geoip2.database
        import geoip2.errors
        
        # Путь к базе данных GeoIP2
        db_path = os.getenv("GEOIP2_DB_PATH", "GeoLite2-City.mmdb")
        
        if not os.path.exists(db_path):
            return i18n.get('ip.database_not_found', lang)
        
        # Открываем базу данных и получаем информацию
        with geoip2.database.Reader(db_path) as reader:
            try:
                response = reader.city(ip_address)
                
                # Формируем информацию
                ip_emoji = "🌐"
                country_emoji = "🌍"
                city_emoji = "🏙️"
                isp_emoji = "🔌"
                coord_emoji = "📍"
                
                # Получаем название страны на нужном языке
                country = None
                country_iso = None
                if response.country and response.country.names:
                    country_names = response.country.names
                    if lang == 'ru' and 'ru' in country_names:
                        country = country_names['ru']
                    elif lang == 'en' and 'en' in country_names:
                        country = country_names['en']
                    else:
                        country = country_names.get('en')
                    country_iso = response.country.iso_code
                
                if not country:
                    country = i18n.get('ip.unknown', lang)
                if not country_iso:
                    country_iso = i18n.get('ip.unknown', lang)
                
                # Получаем название города
                city = None
                if response.city and response.city.names:
                    city_names = response.city.names
                    if lang == 'ru' and 'ru' in city_names:
                        city = city_names['ru']
                    elif lang == 'en' and 'en' in city_names:
                        city = city_names['en']
                    else:
                        city = city_names.get('en')
                
                # Координаты
                lat = response.location.latitude
                lon = response.location.longitude
                
                # Формируем ответ
                lines = [
                    i18n.get('ip.title', lang),
                    "",
                    f"{ip_emoji} {i18n.get('ip.address', lang)}: `{ip_address}`",
                    f"{country_emoji} {i18n.get('ip.country', lang)}: {country} ({country_iso})",
                ]
                
                if city:
                    lines.append(f"{city_emoji} {i18n.get('ip.city', lang)}: {city}")
                
                # ISP/Organization (если доступно в базе)
                # В City базе может не быть ISP, это есть в ASN базе
                # Но попробуем получить
                try:
                    if hasattr(response, 'traits') and hasattr(response.traits, 'isp'):
                        isp = response.traits.isp
                        if isp:
                            lines.append(f"{isp_emoji} {i18n.get('ip.provider', lang)}: {isp}")
                except:
                    pass
                
                # Координаты
                if lat is not None and lon is not None:
                    lines.append(f"{coord_emoji} {i18n.get('ip.coordinates', lang)}: {lat:.4f}, {lon:.4f}")
                
                return "\n".join(lines)
                
            except geoip2.errors.AddressNotFoundError:
                return i18n.get('ip.not_found', lang, ip=ip_address)
            except Exception as e:
                logging.error(f"GeoIP2 lookup error for {ip_address}: {e}")
                return i18n.get('ip.lookup_error', lang, error=str(e))
                
    except ImportError:
        logging.error("geoip2 module not installed")
        return i18n.get('ip.module_not_installed', lang)
    except Exception as e:
        logging.error(f"Unexpected error in get_ip_info: {e}")
        return i18n.get('ip.unexpected_error', lang, error=str(e))

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
def get_main_keyboard(is_admin: bool, lang: str = 'ru'):
    # Определяем текст кнопки смены языка на альтернативном языке
    if lang == 'ru':
        lang_button_text = "🌐 English"
    elif lang == 'en':
        lang_button_text = "🌐 Русский"
    else:
        lang_button_text = "🌐 Language"
    
    buttons = [
        [InlineKeyboardButton(text=_("buttons.mode", lang=lang), callback_data="mode")],
        [InlineKeyboardButton(text=_("buttons.history", lang=lang), callback_data="history")],
        [InlineKeyboardButton(text=_("buttons.help", lang=lang), callback_data="help")],
        [InlineKeyboardButton(text=lang_button_text, callback_data="change_language")]
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(text=_("buttons.admin_panel", lang=lang), callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_admin_keyboard():
    buttons = [
        [InlineKeyboardButton(text="Сбросить очередь", callback_data="reset_queue"), InlineKeyboardButton(text="Очистить кэш", callback_data="clearcache")],
        [InlineKeyboardButton(text="Экспорт пригодных", callback_data="export_approved"), InlineKeyboardButton(text="Очистить пригодные", callback_data="clear_approved")],
        [InlineKeyboardButton(text="Аналитика", callback_data="analytics"), InlineKeyboardButton(text="Управление группами", callback_data="groups")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="start_menu")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_domain_result_keyboard(domain: str, is_short: bool):
    """Генерирует клавиатуру для результата проверки домена"""
    buttons = []
    if is_short:
        buttons.append([InlineKeyboardButton(
            text="📄 Полный отчет", 
            callback_data=f"full_report:{domain}"
        )])
    else:
        buttons.append([InlineKeyboardButton(
            text="📋 Краткий отчет", 
            callback_data=f"short_report:{domain}"
        )])
    
    buttons.append([InlineKeyboardButton(
        text="🔄 Перепроверить", 
        callback_data=f"recheck:{domain}:{int(is_short)}"
    )])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
        daily_key = f"daily:{user_id}{daily_key_suffix}:{datetime.now().strftime('%Y%m%d')}"
        
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
        
        # Для групп используем GROUP_OUTPUT_MODE, для личек - настройки пользователя
        if is_group:
            final_short_mode = short_mode and (GROUP_OUTPUT_MODE == "short")
        else:
            final_short_mode = short_mode and user_mode_is_short

        for domain in valid_domains:
            try:
                # Формируем ключ кэша с учетом режима вывода
                cache_mode = "short" if final_short_mode else "full"
                cache_key = f"result:{domain}:{cache_mode}"
                cached_result = await r.get(cache_key)
                
                if cached_result:
                    # Результат найден в кэше с нужным типом отчета
                    response_text = cached_result
                    if is_group and final_short_mode:
                        response_text += "\n\n💡 <i>Для полного отчета выполните повторный запрос в ЛС боту.</i>"
                    
                    # Добавляем inline кнопки только для личных сообщений
                    keyboard = get_domain_result_keyboard(domain, final_short_mode) if not is_group else None
                    await send_topic_aware_message(message, response_text, reply_markup=keyboard)
                else:
                    # Результата нет в кэше или нужен другой тип отчета
                    await enqueue(domain, user_id, final_short_mode, message.chat.id, message.message_id, message.message_thread_id)
                    await send_topic_aware_message(message, f"✅ Домен <b>{domain}</b> добавлен в очередь на проверку.")
                await log_analytics("domain_check", user_id, domain=domain, mode="short" if final_short_mode else "full")
            except Exception as e:
                logging.error(f"Error processing domain {domain}: {e}")
                await send_topic_aware_message(message, f"❌ Ошибка при обработке домена {domain}: {e}")
    except Exception as redis_error:
        logging.error(f"Redis connection error: {redis_error}")
        await send_topic_aware_message(message, "❌ Ошибка подключения к базе данных. Попробуйте позже.")
    finally:
        try:
            await r.aclose()
        except Exception:
            pass

# --- Message Handlers ---
@router.message(CommandStart())
async def cmd_start(message: Message, command: Optional[CommandObject] = None):
    if not message.from_user: return
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    
    # Инициализируем язык пользователя
    user_lang = await init_user_language(message.from_user)
    
    if command and command.args:
        param = command.args
        try:
            decoded_param = unquote(param)
        except Exception as e:
            logging.warning(f"Failed to decode param '{param}': {e}")
            decoded_param = param

        if decoded_param.startswith("full_"):
            domain = extract_domain(decoded_param[5:])
            if domain:
                msg = f"📄 <b>{_('messages.getting_full_report', lang=user_lang, domain=domain)}</b>" if i18n.is_supported(user_lang) else f"📄 <b>Получаю полный отчет для {domain}...</b>"
                await send_topic_aware_message(message, msg)
                await handle_domain_logic(message, domain, short_mode=False)
            else:
                msg = f"❌ {_('messages.invalid_domain', lang=user_lang, domain=decoded_param[5:])}" if i18n.is_supported(user_lang) else f"❌ Некорректный домен в ссылке: {decoded_param[5:]}"
                await send_topic_aware_message(message, msg)
        else:
            domain = extract_domain(decoded_param)
            if domain:
                msg = f"🔍 <b>{_('messages.getting_result', lang=user_lang, domain=domain)}</b>" if i18n.is_supported(user_lang) else f"🔍 <b>Получаю результат для {domain}...</b>"
                await send_topic_aware_message(message, msg)
                await handle_domain_logic(message, domain, short_mode=True)
            else:
                msg = f"❌ {_('messages.unknown_deeplink', lang=user_lang, param=decoded_param)}" if i18n.is_supported(user_lang) else f"❌ Неизвестный параметр deep-link: {decoded_param}"
                await send_topic_aware_message(message, msg)
        return

    # Приветственное сообщение с локализацией
    welcome_title = _("welcome.title", lang=user_lang)
    welcome_desc = _("welcome.description", lang=user_lang)
    welcome_help = _("welcome.help_hint", lang=user_lang)
    welcome_message = f"{welcome_title}\n\n{welcome_desc}\n\n{welcome_help}"
    
    await send_topic_aware_message(message, welcome_message, reply_markup=get_main_keyboard(is_admin, user_lang))

@router.message(Command("help"))
async def cmd_help(message: Message):
    if not message.from_user: return
    is_admin = message.from_user.id == ADMIN_ID
    is_group = is_group_chat(message)
    
    if is_group:
        # Команды для групповых чатов
        help_text = (
            "<b>Команды для групп:</b>\n"
            "/start - Начало работы\n"
            "/help - Показать эту справку\n"
            "/check [домен] - Краткая проверка\n"
            "/full [домен] - Полная проверка\n\n"
            f"<i>💡 Префикс команд: {GROUP_COMMAND_PREFIX}</i>\n"
            f"<i>📊 Режим вывода: {GROUP_OUTPUT_MODE}</i>"
        )
    else:
        # Команды для личных сообщений
        help_text = (
            "<b>Основные команды:</b>\n"
            "/start - Начало работы\n"
            "/help - Показать эту справку\n"
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
    
    # Команда /mode работает только в личных сообщениях
    if is_group_chat(message):
        await send_topic_aware_message(message, "⛔ Команда /mode доступна только в личных сообщениях с ботом. В группах используется настройка GROUP_OUTPUT_MODE.")
        return
        
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
    
    # Команда /history работает только в личных сообщениях
    if is_group_chat(message):
        await send_topic_aware_message(message, "⛔ Команда /history доступна только в личных сообщениях с ботом.")
        return
        
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

@router.message(Command("language", "lang"))
async def cmd_language(message: Message):
    """Команда для выбора языка интерфейса"""
    if not message.from_user: return
    
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id)
    
    # Создаем inline клавиатуру с языками
    buttons = []
    row = []
    for lang_code in i18n.supported_languages:
        lang_name = i18n.get_language_name(lang_code, user_lang)
        row.append(InlineKeyboardButton(
            text=lang_name, 
            callback_data=f"set_lang:{lang_code}"
        ))
        # По 2 кнопки в ряд
        if len(row) == 2:
            buttons.append(row)
            row = []
    
    # Добавляем оставшиеся кнопки
    if row:
        buttons.append(row)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    select_text = _("messages.select_language", lang=user_lang)
    await send_topic_aware_message(message, select_text, reply_markup=keyboard)

@router.message(Command("ip"))
async def cmd_ip(message: Message):
    """Команда для получения информации об IP адресе"""
    if not message.from_user or not message.text: return
    
    user_id = message.from_user.id
    user_lang = await get_user_language(user_id)
    
    # Парсим команду
    parts = message.text.split(maxsplit=1)
    
    if len(parts) < 2:
        # Если не указан IP адрес
        usage_text = _("ip.usage", lang=user_lang)
        await send_topic_aware_message(message, usage_text)
        return
    
    ip_address = parts[1].strip()
    
    # Валидация IPv4 адреса
    if not is_valid_ipv4(ip_address):
        invalid_text = _("ip.invalid", lang=user_lang, ip=ip_address)
        await send_topic_aware_message(message, invalid_text)
        return
    
    # Отправляем сообщение о получении информации
    processing_text = _("ip.processing", lang=user_lang, ip=ip_address)
    status_msg = await send_topic_aware_message(message, processing_text)
    
    # Получаем информацию об IP
    ip_info = await get_ip_info(ip_address, user_lang)
    
    # Отправляем результат
    await send_topic_aware_message(message, ip_info)
    
    # Удаляем сообщение о статусе (если оно было отправлено)
    if status_msg and not is_group_chat(message):
        try:
            await bot.delete_message(chat_id=status_msg.chat.id, message_id=status_msg.message_id)
        except:
            pass
    
    # Логируем аналитику
    await log_analytics("ip_lookup", user_id, ip_address=ip_address)

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
    
    # Админ-команды работают только в ЛС
    if isinstance(query_or_message, Message) and is_group_chat(query_or_message):
        await send_topic_aware_message(query_or_message, "⛔ Админ-команды доступны только в личных сообщениях с ботом.")
        return False
        
    return True

@router.message(Command("admin"))
async def admin_panel_command(message: Message):
    if not await is_admin_check(message): return
    await send_topic_aware_message(message, "Добро пожаловать в админ-панель.", reply_markup=get_admin_keyboard())

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
        report = await analytics_collector.generate_analytics_report()
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
    user_lang = await get_user_language(call.from_user.id)
    
    welcome_title = _("welcome.title", lang=user_lang)
    welcome_desc = _("welcome.description", lang=user_lang)
    welcome_help = _("welcome.help_hint", lang=user_lang)
    welcome_message = f"{welcome_title}\n\n{welcome_desc}\n\n{welcome_help}"
    
    await call.message.edit_text(
        welcome_message,
        reply_markup=get_main_keyboard(is_admin, user_lang)
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

@router.callback_query(F.data == "help")
async def cq_help(call: CallbackQuery):
    """Обработчик кнопки справки"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    user_id = call.from_user.id
    user_lang = await get_user_language(user_id)
    is_admin = user_id == ADMIN_ID
    
    # Команды для личных сообщений
    help_text = (
        f"<b>{_('help.basic_title', lang=user_lang)}</b>\n"
        f"/start - {_('commands.start', lang=user_lang)}\n"
        f"/help - {_('commands.help', lang=user_lang)}\n"
        f"/mode - {_('commands.mode', lang=user_lang)}\n"
        f"/history - {_('commands.history', lang=user_lang)}\n"
        f"/check [домен] - {_('commands.check', lang=user_lang)}\n"
        f"/full [домен] - {_('commands.full', lang=user_lang)}\n"
        f"/ip [IP] - {_('commands.ip', lang=user_lang)}\n"
        f"/language - {_('commands.language', lang=user_lang)}\n"
    )
    if is_admin:
        help_text += f"\n<b>{_('help.admin_title', lang=user_lang)}</b> /admin"
    
    await call.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=_("buttons.back", lang=user_lang), callback_data="start_menu")]
    ]))
    await call.answer()

@router.callback_query(F.data == "admin_panel")
async def cq_admin_panel(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    await call.message.edit_text("Панель администратора:", reply_markup=get_admin_keyboard())
    await call.answer()

@router.callback_query(F.data == "reset_queue")
async def cq_reset_queue(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    r = await get_redis_connection()
    try:
        q_len = await r.llen("queue:domains")
        p_keys = await r.keys("pending:*")
        if q_len > 0: await r.delete("queue:domains")
        if p_keys: await r.delete(*p_keys)
        await call.message.edit_text(f"✅ Очередь сброшена. Удалено задач: {q_len}, ключей pending: {len(p_keys)}.", reply_markup=get_admin_keyboard())
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data == "clearcache")
async def cq_clearcache(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    r = await get_redis_connection()
    try:
        keys = await r.keys("result:*")
        if keys:
            await r.delete(*keys)
            await call.message.edit_text(f"✅ Кэш очищен. Удалено {len(keys)} записей.", reply_markup=get_admin_keyboard())
        else:
            await call.message.edit_text("✅ Кэш уже пуст.", reply_markup=get_admin_keyboard())
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data == "clear_approved")
async def cq_clear_approved(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    if not SAVE_APPROVED_DOMAINS:
        await call.answer("⛔ Функция сохранения доменов отключена.", show_alert=True)
        return
    r = await get_redis_connection()
    try:
        await r.delete("approved_domains")
        await call.message.edit_text("✅ Список пригодных доменов очищен.", reply_markup=get_admin_keyboard())
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data == "export_approved")
async def cq_export_approved(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    if not SAVE_APPROVED_DOMAINS:
        await call.answer("⛔ Функция сохранения доменов отключена.", show_alert=True)
        return
    r = await get_redis_connection()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await call.message.edit_text("📜 Список пуст.", reply_markup=get_admin_keyboard())
        else:
            file_path = os.path.join(os.getenv("LOG_DIR", "/tmp"), "approved_domains.txt")
            with open(file_path, "w") as f:
                f.write("\n".join(sorted(domains)))
            await call.message.reply_document(FSInputFile(file_path))
            await call.message.edit_text("✅ Файл с пригодными доменами отправлен.", reply_markup=get_admin_keyboard())
    except Exception as e:
        await call.message.edit_text(f"❌ Ошибка экспорта: {e}", reply_markup=get_admin_keyboard())
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data == "analytics")
async def cq_analytics(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    if not analytics_collector:
        await call.message.edit_text("❌ Аналитика не инициализирована.", reply_markup=get_admin_keyboard())
        await call.answer()
        return
    try:
        report = await analytics_collector.generate_analytics_report()
        await call.message.edit_text(report, reply_markup=get_admin_keyboard())
    except Exception as e:
        await call.message.edit_text(f"❌ Ошибка генерации отчета: {e}", reply_markup=get_admin_keyboard())
    await call.answer()

@router.callback_query(F.data == "groups")
async def cq_groups(call: CallbackQuery):
    if not call.message or not isinstance(call.message, types.Message) or not await is_admin_check(call): return
    
    if not AUTHORIZED_GROUPS:
        status = "🌐 <b>Режим авторизации:</b> Открытый (любые группы)\n"
    else:
        status = f"🔒 <b>Режим авторизации:</b> Ограниченный ({len(AUTHORIZED_GROUPS)} групп)\n"
        status += "<b>Авторизованные группы:</b>\n" + "\n".join(f"• <code>{gid}</code>" for gid in sorted(AUTHORIZED_GROUPS))
    
    await call.message.edit_text(status, reply_markup=get_admin_keyboard())
    await call.answer()

# --- Domain Action Handlers ---
@router.callback_query(F.data.startswith("full_report:"))
async def cq_full_report(call: CallbackQuery):
    """Обработчик запроса полного отчета"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    domain = call.data.split(":", 1)[1]
    r = await get_redis_connection()
    try:
        cache_key = f"result:{domain}:full"
        cached_result = await r.get(cache_key)
        
        if cached_result:
            # Полный отчет найден в кэше
            keyboard = get_domain_result_keyboard(domain, is_short=False)
            await call.message.edit_text(cached_result, reply_markup=keyboard)
        else:
            # Нет в кэше, добавляем в очередь
            await enqueue(domain, call.from_user.id, short_mode=False, chat_id=call.message.chat.id)
            await call.message.edit_text(f"✅ Домен <b>{domain}</b> добавлен в очередь для полной проверки.")
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data.startswith("short_report:"))
async def cq_short_report(call: CallbackQuery):
    """Обработчик запроса краткого отчета"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    domain = call.data.split(":", 1)[1]
    r = await get_redis_connection()
    try:
        cache_key = f"result:{domain}:short"
        cached_result = await r.get(cache_key)
        
        if cached_result:
            # Краткий отчет найден в кэше
            keyboard = get_domain_result_keyboard(domain, is_short=True)
            await call.message.edit_text(cached_result, reply_markup=keyboard)
        else:
            # Нет в кэше, добавляем в очередь
            await enqueue(domain, call.from_user.id, short_mode=True, chat_id=call.message.chat.id)
            await call.message.edit_text(f"✅ Домен <b>{domain}</b> добавлен в очередь для краткой проверки.")
    finally:
        await r.aclose()
    await call.answer()

@router.callback_query(F.data.startswith("recheck:"))
async def cq_recheck(call: CallbackQuery):
    """Обработчик перепроверки домена"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    parts = call.data.split(":")
    domain = parts[1]
    is_short = bool(int(parts[2]))
    
    r = await get_redis_connection()
    try:
        # Удаляем из кэша для принудительной перепроверки
        cache_mode = "short" if is_short else "full"
        cache_key = f"result:{domain}:{cache_mode}"
        await r.delete(cache_key)
        
        # Добавляем в очередь
        await enqueue(domain, call.from_user.id, short_mode=is_short, chat_id=call.message.chat.id)
        mode_text = "краткой" if is_short else "полной"
        await call.message.edit_text(f"🔄 Домен <b>{domain}</b> добавлен в очередь для {mode_text} перепроверки.")
    finally:
        await r.aclose()
    await call.answer("Запущена перепроверка!")

# --- Language Selection Handler ---
@router.callback_query(F.data == "change_language")
async def cq_change_language(call: CallbackQuery):
    """Обработчик кнопки смены языка из главного меню"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    user_id = call.from_user.id
    user_lang = await get_user_language(user_id)
    
    # Создаем inline клавиатуру с языками
    buttons = []
    row = []
    for lang_code in i18n.supported_languages:
        lang_name = i18n.get_language_name(lang_code, user_lang)
        # Добавляем галочку для текущего языка
        if lang_code == user_lang:
            lang_name = f"✅ {lang_name}"
        row.append(InlineKeyboardButton(
            text=lang_name, 
            callback_data=f"set_lang:{lang_code}"
        ))
        # По 2 кнопки в ряд
        if len(row) == 2:
            buttons.append(row)
            row = []
    
    # Добавляем оставшиеся кнопки
    if row:
        buttons.append(row)
    
    # Добавляем кнопку "Назад"
    buttons.append([InlineKeyboardButton(text=_("buttons.back", lang=user_lang), callback_data="start_menu")])
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    select_text = _("messages.select_language", lang=user_lang)
    await call.message.edit_text(select_text, reply_markup=keyboard)
    await call.answer()

@router.callback_query(F.data.startswith("set_lang:"))
async def cq_set_language(call: CallbackQuery):
    """Обработчик выбора языка"""
    if not call.message or not isinstance(call.message, types.Message) or not call.from_user: return
    
    lang_code = call.data.split(":", 1)[1]
    user_id = call.from_user.id
    
    # Устанавливаем новый язык
    await set_user_language(user_id, lang_code)
    
    # Получаем название языка на новом языке
    lang_name = i18n.get_language_name(lang_code, lang_code)
    
    # Сообщение об успехе на новом языке
    success_msg = _("messages.language_selected", lang=lang_code, language=lang_name)
    
    # Показываем главное меню на новом языке
    is_admin = user_id == ADMIN_ID
    welcome_title = _("welcome.title", lang=lang_code)
    welcome_desc = _("welcome.description", lang=lang_code)
    welcome_help = _("welcome.help_hint", lang=lang_code)
    welcome_message = f"{welcome_title}\n\n{welcome_desc}\n\n{welcome_help}\n\n✅ {success_msg}"
    
    await call.message.edit_text(welcome_message, reply_markup=get_main_keyboard(is_admin, lang_code))
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
        BotCommand(command="ip", description="Получить информацию об IP адресе"),
        BotCommand(command="language", description="Сменить язык интерфейса"),
        BotCommand(command="admin", description="Панель администратора"),
    ]
    await bot.set_my_commands(commands)

async def shutdown(dp: Dispatcher):
    """Корректное завершение работы бота"""
    logging.info("🛑 Shutting down gracefully...")
    
    # Останавливаем polling
    await dp.stop_polling()
    
    # Закрываем Redis pool
    await close_redis_pool()
    
    # Закрываем сессию бота
    await bot.session.close()
    
    # Отменяем все активные задачи
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    # Ожидаем завершения всех задач
    await asyncio.gather(*tasks, return_exceptions=True)
    
    logging.info("✅ Bot shutdown completed")

async def main():
    dp = Dispatcher()
    dp.include_router(router)

    try:
        # Инициализация
        await init_redis_pool()
        await init_analytics()
        await set_bot_commands()
        
        logging.info("🚀 Bot starting...")
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except (KeyboardInterrupt, SystemExit):
        logging.info("⚠️ Received stop signal")
    except Exception as e:
        logging.error(f"❌ Critical error in main loop: {e}", exc_info=True)
    finally:
        await shutdown(dp)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("👋 Bot execution stopped by user.")
    except Exception as e:
        logging.error(f"❌ Critical error: {e}", exc_info=True)
