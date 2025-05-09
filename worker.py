import asyncio
import os
from aiogram import Bot
from checker import run_check
from redis_queue import dequeue

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")

async def worker_loop():
    while True:
        user_id, domain = await dequeue()
        if user_id and domain:
            try:
                result = run_check(domain)
                await bot.send_message(user_id, result)
            except Exception as e:
                await bot.send_message(user_id, f"❌ Ошибка обработки {domain}: {e}")
        await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(worker_loop())
