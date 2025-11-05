import asyncio
import redis.asyncio as redis
import logging
import os
import json
from logging.handlers import RotatingFileHandler
from redis_queue import get_redis
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from checker import run_check  # Импорт функции из checker.py
from datetime import datetime
from typing import Optional

# Импортируем новые модули (если доступны)
try:
    from retry_logic import retry_with_backoff, DOMAIN_CHECK_RETRY, REDIS_RETRY
    RETRY_AVAILABLE = True
except ImportError:
    RETRY_AVAILABLE = False
    
try:
    from analytics import AnalyticsCollector
    ANALYTICS_AVAILABLE = True
except ImportError:
    ANALYTICS_AVAILABLE = False

# Настройка логирования
log_file = "/app/worker.log"
# Уменьшаем размер файла логов и количество бэкапов
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
logging.basicConfig(
    level=logging.WARNING,  # Изменено с INFO на WARNING
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
)

# Инициализация Telegram Bot
TOKEN = os.getenv("BOT_TOKEN")
SAVE_APPROVED_DOMAINS = os.getenv("SAVE_APPROVED_DOMAINS", "false").lower() == "true"
GROUP_OUTPUT_MODE = os.getenv("GROUP_OUTPUT_MODE", "short").lower()  # "short" или "full"
if not TOKEN:
    logging.error("BOT_TOKEN environment variable is not set")
    raise ValueError("BOT_TOKEN environment variable is not set")
bot = Bot(token=TOKEN, parse_mode="HTML")

# Инициализация аналитики
analytics_collector = None

def get_domain_result_keyboard(domain: str, is_short: bool):
    """Генерирует inline клавиатуру для результата проверки домена"""
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

async def init_analytics():
    """Инициализирует аналитику"""
    global analytics_collector
    if ANALYTICS_AVAILABLE:
        try:
            redis_client = await get_redis()
            analytics_collector = AnalyticsCollector(redis_client)
            logging.info("Worker analytics initialized successfully")
        except Exception as e:
            logging.warning(f"Failed to initialize worker analytics: {e}")

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
        except Exception as e:
            logging.warning(f"Failed to log worker analytics: {e}")

async def check_domain(domain: str, user_id: int, short_mode: bool, lang: str = 'ru') -> str:
    """Проверяет домен с retry логикой и аналитикой"""
    start_time = datetime.now()
    
    async def perform_check():
        """Внутренняя функция для выполнения проверки"""
        try:
            # Вызываем функцию из checker.py с таймаутом
            async with asyncio.timeout(300):
                # run_check не асинхронна, поэтому запускаем её в потоке
                loop = asyncio.get_event_loop()
                report = await loop.run_in_executor(None, lambda: run_check(domain, full_report=not short_mode, lang=lang))
                return report
        except asyncio.TimeoutError:
            logging.error(f"Timeout while checking {domain} for user {user_id}")
            error_msg = f"Проверка {domain} прервана: превышено время ожидания (5 минут)." if lang == 'ru' else f"Check {domain} timed out (5 minutes)."
            raise asyncio.TimeoutError(error_msg)
    
    try:
        # Используем retry логику если доступна
        if RETRY_AVAILABLE:
            report = await retry_with_backoff(perform_check, DOMAIN_CHECK_RETRY)
        else:
            report = await perform_check()
            
        execution_time = (datetime.now() - start_time).total_seconds()
        
        # Логируем успешную проверку
        await log_analytics("domain_check", user_id,
                           domain=domain, 
                           check_type="short" if short_mode else "full",
                           result_status="success",
                           execution_time=execution_time)
        
    except Exception as e:
        execution_time = (datetime.now() - start_time).total_seconds()
        
        # Логируем неудачную проверку
        await log_analytics("domain_check", user_id,
                           domain=domain,
                           check_type="short" if short_mode else "full", 
                           result_status="failed",
                           execution_time=execution_time)
        
        logging.error(f"Failed to check {domain} for user {user_id}: {str(e)}")
        
        # Удаляем pending ключ
        r = await get_redis()
        try:
            await r.delete(f"pending:{domain}:{user_id}")
        finally:
            await r.aclose()
        
        error_msg = f"❌ Ошибка при проверке {domain}: {str(e)}" if lang == 'ru' else f"❌ Error checking {domain}: {str(e)}"
        return error_msg

    # Сохраняем результат
    r = await get_redis()
    try:
        # Определяем режим для ключа кэша
        cache_mode = "short" if short_mode else "full"
        cache_key = f"result:{domain}:{cache_mode}"
        
        # Сохраняем отчет с учетом режима на 7 дней
        await r.set(cache_key, report, ex=604800)

        # Проверяем пригодность домена и добавляем в approved_domains (только если включена опция)
        # Поддержка обоих языков
        suitable_markers = ["✅ Пригоден для Reality", "✅ Suitable for Reality"]
        if SAVE_APPROVED_DOMAINS and any(marker in report for marker in suitable_markers):
            await r.sadd("approved_domains", domain)

        output = report  # Используем отчет напрямую из run_check

        await r.lpush(f"history:{user_id}", f"{datetime.now().strftime('%H:%M')} - {domain}")
        await r.ltrim(f"history:{user_id}", 0, 9)
        await r.delete(f"pending:{domain}:{user_id}")
        return output
    except Exception as e:
        logging.error(f"Failed to save result for {domain}: {str(e)}")
        output = f"❌ Ошибка при проверке {domain}: {str(e)}"
        return output
    finally:
        await r.aclose()

async def clear_cache(r: redis.Redis):
    try:
        keys = await r.keys("result:*")
        if keys:
            await r.delete(*keys)
    except Exception as e:
        logging.error(f"Failed to clear cache: {str(e)}")

async def cache_cleanup_task(r: redis.Redis):
    while True:
        await clear_cache(r)
        await asyncio.sleep(86400)

async def send_group_reply(chat_id: int, message_id: Optional[int], thread_id: Optional[int], text: str, reply_markup=None):
    """Отправляет ответ в группу с поддержкой тем и reply"""
    try:
        if thread_id:
            # Отправляем в определенную тему
            if message_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    message_thread_id=thread_id,
                    reply_to_message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    message_thread_id=thread_id,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
        else:
            # Обычная отправка в группу
            if message_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_to_message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
    except Exception as e:
        logging.error(f"Failed to send group reply to {chat_id}: {e}")
        # Fallback: отправляем без reply/thread
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def worker():
    r = await get_redis()
    try:
        await r.ping()
        asyncio.create_task(cache_cleanup_task(r))
        while True:
            try:
                result = await r.brpop("queue:domains", timeout=5)
                if result is None:
                    continue
                _, task = result
                
                # Попробуем парсить как JSON (новый формат)
                try:
                    task_data = json.loads(task)
                    domain = task_data['domain']
                    user_id = int(task_data['user_id'])
                    short_mode = task_data['short_mode']
                    chat_id = task_data.get('chat_id', user_id)
                    message_id = task_data.get('message_id')
                    thread_id = task_data.get('thread_id')
                    lang = task_data.get('lang', 'ru')  # Получаем язык пользователя
                except (json.JSONDecodeError, KeyError):
                    # Fallback к старому формату
                    domain, user_id, short_mode = task.split(":")
                    user_id = int(user_id)
                    short_mode = short_mode == "True"
                    chat_id = user_id
                    message_id = None
                    thread_id = None
                    lang = 'ru'  # По умолчанию русский для старого формата
                
                # Передаём lang в check_domain для локализации результатов проверки
                result = await check_domain(domain, user_id, short_mode, lang)
                
                try:
                    # Определяем, это групповой чат или ЛС
                    is_group = chat_id != user_id
                    
                    if is_group:
                        # В группе используем GROUP_OUTPUT_MODE
                        if GROUP_OUTPUT_MODE == "short":
                            # Краткий отчёт с инструкцией о ЛС
                            group_message = result + "\n\n💡 <i>Для полного логирования выполните повторный запрос в ЛС боту.</i>"
                            await send_group_reply(chat_id, message_id, thread_id, group_message)
                        else:
                            # Полный отчёт в группе
                            await send_group_reply(chat_id, message_id, thread_id, result)
                    else:
                        # В ЛС отправляем с inline кнопками
                        keyboard = get_domain_result_keyboard(domain, is_short=short_mode)
                        await bot.send_message(user_id, result, reply_markup=keyboard)
                except Exception as e:
                    logging.error(f"Failed to send message to chat {chat_id} for {domain}: {str(e)}")
            except Exception as e:
                logging.error(f"Worker error: {str(e)}")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Failed to initialize worker: {str(e)}")
    finally:
        await r.aclose()

if __name__ == "__main__":
    asyncio.run(worker())
