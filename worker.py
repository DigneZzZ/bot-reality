import asyncio
import redis.asyncio as redis
import logging
import os
from logging.handlers import RotatingFileHandler
from redis_queue import get_redis
from aiogram import Bot
from bot import get_full_report_button
from checker import run_check  # Импорт функции из checker.py

# Настройка логирования
log_file = "/app/worker.log"
handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
logging.info("Worker logging initialized")

# Инициализация Telegram Bot
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logging.error("BOT_TOKEN environment variable is not set")
    raise ValueError("BOT_TOKEN environment variable is not set")
bot = Bot(token=TOKEN, parse_mode="HTML")

async def check_domain(domain: str, user_id: int, short_mode: bool) -> str:
    logging.info(f"Starting check for {domain} for user {user_id}, short_mode={short_mode}")
    try:
        # Вызываем функцию из checker.py с таймаутом
        async with asyncio.timeout(300):
            # run_check не асинхронна, поэтому запускаем её в потоке
            loop = asyncio.get_event_loop()
            report = await loop.run_in_executor(None, lambda: run_check(domain, full_report=not short_mode))
    except asyncio.TimeoutError:
        logging.error(f"Timeout while checking {domain} for user {user_id}")
        output = f"❌ Проверка {domain} прервана: превышено время ожидания (5 минут)."
        r = await get_redis()
        try:
            await r.delete(f"pending:{domain}:{user_id}")
            logging.info(f"Removed pending flag for {domain} for user {user_id} due to timeout")
        finally:
            await r.aclose()
        return output

    r = await get_redis()
    try:
        # Сохраняем полный отчет в кэш
        await r.set(f"result:{domain}", report, ex=86400)

        output = report  # Используем отчет напрямую из run_check

        await r.lpush(f"history:{user_id}", f"{domain}: {'Краткий' if short_mode else 'Полный'} отчёт")
        await r.ltrim(f"history:{user_id}", 0, 9)
        await r.delete(f"pending:{domain}:{user_id}")
        logging.info(f"Processed {domain} for user {user_id}, short_mode={short_mode}")
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
            logging.info(f"Cleared {len(keys)} result keys from Redis cache")
    except Exception as e:
        logging.error(f"Failed to clear cache: {str(e)}")

async def cache_cleanup_task(r: redis.Redis):
    while True:
        await clear_cache(r)
        await asyncio.sleep(86400)

async def worker():
    logging.info("Starting worker process")
    r = await get_redis()
    try:
        await r.ping()
        logging.info("Successfully connected to Redis")
        asyncio.create_task(cache_cleanup_task(r))
        while True:
            try:
                result = await r.brpop("queue:domains", timeout=5)
                if result is None:
                    continue
                _, task = result
                logging.info(f"Popped task from queue: {task}")
                domain, user_id, short_mode = task.split(":")
                user_id = int(user_id)
                short_mode = short_mode == "True"
                result = await check_domain(domain, user_id, short_mode)
                try:
                    await bot.send_message(user_id, result, reply_markup=get_full_report_button(domain) if short_mode else None)
                    logging.info(f"Sent result for {domain} to user {user_id}")
                except Exception as e:
                    logging.error(f"Failed to send message to user {user_id} for {domain}: {str(e)}")
            except Exception as e:
                logging.error(f"Worker error: {str(e)}")
                await asyncio.sleep(1)
    except Exception as e:
        logging.error(f"Failed to initialize worker: {str(e)}")
    finally:
        await r.aclose()
        logging.info("Worker stopped")

if __name__ == "__main__":
    asyncio.run(worker())
