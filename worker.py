import asyncio
import os
import logging
from aiogram import Bot
from checker import run_check
from redis_queue import dequeue

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="worker.log", format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")

async def worker_loop():
    while True:
        try:
            user_id, domain = await dequeue()
            if user_id and domain:
                logging.info(f"Processing domain {domain} for user {user_id}")
                try:
                    result = run_check(domain)
                    await bot.send_message(user_id, result)
                    logging.info(f"Sent result for {domain} to user {user_id}")
                except Exception as e:
                    error_msg = f"❌ Ошибка обработки {domain}: {str(e)}"
                    await bot.send_message(user_id, error_msg)
                    logging.error(f"Error processing {domain}: {str(e)}")
            await asyncio.sleep(0.5)  # Уменьшенная задержка
        except Exception as e:
            logging.error(f"Worker loop error: {str(e)}")
            await asyncio.sleep(5)  # Задержка при ошибке

if __name__ == "__main__":
    asyncio.run(worker_loop())
