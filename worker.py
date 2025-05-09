import asyncio
from aiogram import Bot
import os
import checker
from redis_queue import dequeue, get_redis
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="worker.log", format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)

async def process_domain(user_id: int, domain: str, short_mode: bool = False):
    try:
        result = checker.run_check(domain)
        if not result or result.strip() == "":
            logging.error(f"Empty result from checker.run_check for {domain}")
            await bot.send_message(user_id, f"❌ Ошибка: пустой отчёт для {domain}")
            return
        if short_mode:
            lines = result.split("\n")
            short_result = "\n".join(
                line for line in lines
                if any(k in line for k in ["🔍 Проверка", "🔒 TLS", "🌐 HTTP", "🛰 Оценка пригодности", "✅", "🟢", "❌"])
            )
            result = short_result if short_result.strip() else result
        await bot.send_message(user_id, result)
        async with await get_redis() as r:
            await r.setex(f"result:{domain}", 86400, result)
            history_key = f"history:{user_id}"
            await r.lpush(history_key, f"{domain}: {result.splitlines()[0]}")
            await r.ltrim(history_key, 0, 9)  # Храним последние 10 записей
            await r.expire(history_key, 604800)  # 7 дней
        logging.info(f"Processed {domain} for user {user_id} (short_mode={short_mode})")
    except Exception as e:
        logging.error(f"Failed to process {domain} for user {user_id}: {str(e)}")
        await bot.send_message(user_id, f"❌ Ошибка проверки {domain}: {str(e)}")

async def clear_cache_monthly():
    while True:
        try:
            async with await get_redis() as r:
                keys = await r.keys("result:*")
                if keys:
                    await r.delete(*keys)
                    logging.info(f"Cleared {len(keys)} cache entries")
        except Exception as e:
            logging.error(f"Failed to clear cache: {str(e)}")
        await asyncio.sleep(30 * 24 * 3600)  # 30 дней

async def main():
    asyncio.create_task(clear_cache_monthly())
    while True:
        try:
            user_id, domain, short_mode = await dequeue()
            if user_id and domain:
                await process_domain(user_id, domain, short_mode)
            else:
                logging.debug("No tasks in queue, waiting...")
        except Exception as e:
            logging.error(f"Worker error: {str(e)}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
