import asyncio
import redis.asyncio as redis
import logging
import os
import json
from logging.handlers import RotatingFileHandler
from redis_queue import get_redis
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot import get_full_report_button
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
if not TOKEN:
    logging.error("BOT_TOKEN environment variable is not set")
    raise ValueError("BOT_TOKEN environment variable is not set")
bot = Bot(token=TOKEN, parse_mode="HTML")

# Инициализация аналитики
analytics_collector = None

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

async def check_domain(domain: str, user_id: int, short_mode: bool) -> str:
    """Проверяет домен с retry логикой и аналитикой"""
    start_time = datetime.now()
    
    async def perform_check():
        """Внутренняя функция для выполнения проверки"""
        try:
            # Вызываем функцию из checker.py с таймаутом
            async with asyncio.timeout(300):
                # run_check не асинхронна, поэтому запускаем её в потоке
                loop = asyncio.get_event_loop()
                report = await loop.run_in_executor(None, lambda: run_check(domain, full_report=not short_mode))
                return report
        except asyncio.TimeoutError:
            logging.error(f"Timeout while checking {domain} for user {user_id}")
            raise asyncio.TimeoutError(f"Проверка {domain} прервана: превышено время ожидания (5 минут).")
    
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
            
        return f"❌ Ошибка при проверке {domain}: {str(e)}"

    # Сохраняем результат
    r = await get_redis()
    try:
        # Сохраняем полный отчет в кэш на 7 дней (вместо 24 часов)
        await r.set(f"result:{domain}", report, ex=604800)

        # Проверяем пригодность домена и добавляем в approved_domains (только если включена опция)
        if SAVE_APPROVED_DOMAINS and "✅ Пригоден для Reality" in report:
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

def get_group_full_report_button(domain: str, user_id: int):
    """Создаёт кнопку с deep link для получения полного отчёта в ЛС"""
    bot_username = os.getenv("BOT_USERNAME", "bot")  # Замените на актуальное имя бота
    deep_link = f"https://t.me/{bot_username}?start=full_{domain}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📄 Полный отчёт в ЛС", url=deep_link)]
    ])
    return keyboard

def get_deep_link_button(domain: str):
    """Создаёт кнопку с deep link на бота для получения полного отчёта"""
    # Получаем имя бота из токена или используем переменную окружения
    bot_username = os.getenv("BOT_USERNAME", "bot")  # Замените на актуальное имя бота
    deep_link = f"https://t.me/{bot_username}?start=full_{domain}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤖 Получить полный отчёт", url=deep_link)]
    ])
    return keyboard

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
                except (json.JSONDecodeError, KeyError):
                    # Fallback к старому формату
                    domain, user_id, short_mode = task.split(":")
                    user_id = int(user_id)
                    short_mode = short_mode == "True"
                    chat_id = user_id
                    message_id = None
                    thread_id = None
                
                result = await check_domain(domain, user_id, short_mode)
                
                try:
                    # Определяем, это групповой чат или ЛС
                    is_group = chat_id != user_id
                    
                    if is_group:
                        # В группе отвечаем кратким отчётом с кнопкой "Полный в ЛС"
                        if short_mode:
                            # Краткий отчёт + кнопка для полного в ЛС
                            keyboard = get_group_full_report_button(domain, user_id)
                            await send_group_reply(chat_id, message_id, thread_id, result, keyboard)
                        else:
                            # Полный отчёт пытаемся отправить в ЛС
                            try:
                                await bot.send_message(user_id, f"📄 Полный отчёт для {domain}:\n\n{result}")
                                # В группе уведомляем об успешной отправке
                                await send_group_reply(chat_id, message_id, thread_id, 
                                                     f"✅ Полный отчёт для <b>{domain}</b> отправлен вам в личные сообщения.")
                            except Exception as pm_error:
                                # Не удалось отправить в ЛС (скорее всего диалог не начат)
                                logging.warning(f"Failed to send PM to user {user_id}: {pm_error}")
                                
                                # Отправляем уведомление с кнопкой deep link
                                warning_text = f"⚠️ Не удалось отправить полный отчёт в ЛС для <b>{domain}</b>"
                                deep_link_keyboard = get_deep_link_button(domain)
                                await send_group_reply(chat_id, message_id, thread_id, warning_text, deep_link_keyboard)
                    else:
                        # В ЛС отправляем как обычно
                        await bot.send_message(user_id, result, 
                                             reply_markup=get_full_report_button(domain) if short_mode else None)
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
