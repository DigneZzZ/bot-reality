import asyncio
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import checker
from redis_queue import dequeue, get_redis
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="worker.log", format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN)

# Создание инлайн-кнопки для полного отчёта
def get_full_report_button(domain: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Полный отчёт", callback_data=f"full_report:{domain}")]
    ])
    return keyboard

async def process_domain(user_id: int, domain: str, short_mode: bool = False):
    try:
        # Запрос полного отчёта, если short_mode=False
        result = checker.run_check(domain, full_report=not short_mode)
        logging.info(f"Checker output for {domain}: {result}")
        if not result or result.strip() == "":
            logging.error(f"Empty result from checker.run_check for {domain}")
            await bot.send_message(user_id, f"❌ Ошибка: пустой отчёт для {domain}")
            return
        if short_mode:
            await bot.send_message(user_id, result, reply_markup=get_full_report_button(domain))
            async with await get_redis() as r:
                await r.setex(f"result:{domain}", 86400, result)
        else:
            await bot.send_message(user_id, result)
            async with await get_redis() as r:
                await r.setex(f"result:{domain}", 86400, result)
        async with await get_redis() as r:
            history_key = f"history:{user_id}"
            await r.lpush(history_key, f"{domain}: {result.splitlines()[0]}")
            await r.ltrim(history_key, 0, 9)
            await r.expire(history_key, 604800)
            # Сохраняем пригодные домены в множество
            if "✅ Пригоден для Reality" in result:
                await r.sadd("approved_domains", domain)
                logging.info(f"Saved approved domain {domain} to Redis")
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
        await asyncio.sleep(30 * 24 * 3600)

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
