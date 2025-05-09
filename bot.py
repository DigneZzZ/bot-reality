import asyncio
from aiogram import Bot, Dispatcher, types
import os
from redis_queue import enqueue
from collections import defaultdict
from time import time
import redis.asyncio as redis
import re
from urllib.parse import urlparse
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="bot.log", format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

async def get_redis():
    try:
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            decode_responses=True,
            retry_on_timeout=True
        )
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

user_requests = defaultdict(list)
user_violations = {}

def extract_domain(text: str):
    if text.startswith("http://") or text.startswith("https://"):
        try:
            parsed = urlparse(text)
            if parsed.hostname:
                return parsed.hostname
        except:
            return None
    # Улучшенное регулярное выражение для доменов и доменов с портом
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}[a-zA-Z0-9](:[0-9]{1,5})?$", text):
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
    duration = [60, 300, 900, 3600]  # 1m, 5m, 15m, 1h
    if record["count"] >= 5:
        stage = record["count"] - 5
        timeout = duration[min(stage, len(duration) - 1)]
        record["until"] = time() + timeout
    user_violations[user_id] = record
    return int(record["until"] - time()) if record["count"] >= 5 else 0

@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    await message.answer(
        """👋 Привет! Я бот для проверки доменов на пригодность для прокси и Reality.

Отправь домен (например, `example.com`) или используй команду:
/check <домен>

/ping — проверить, что бот работает
/stats — статистика очереди и кэша""",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=["ping"])
async def cmd_ping(message: types.Message):
    await message.reply("🏓 Я жив!")

@dp.message_handler(commands=["stats"])
async def cmd_stats(message: types.Message):
    r = await get_redis()
    try:
        qlen = await r.llen("domain_check_queue")
        keys = await r.keys("result:*")
        await message.reply(
            f"📊 В очереди: {qlen} доменов\n🧠 В кэше: {len(keys)} доменов"
        )
    except Exception as e:
        logging.error(f"Stats command failed: {str(e)}")
        await message.reply("❌ Ошибка получения статистики")
    finally:
        await r.aclose()  # Используем aclose() вместо close()

@dp.message_handler(commands=["check"])
async def cmd_check(message: types.Message):
    args = message.get_args().strip()
    if not args:
        await message.reply("⛔ Укажи домен, например: /check example.com")
        return
    await handle_domain_logic(message, args)

@dp.message_handler()
async def handle_domain(message: types.Message):
    await handle_domain_logic(message, message.text.strip())

async def handle_domain_logic(message: types.Message, input_text: str):
    user_id = message.from_user.id
    penalty, active = get_penalty(user_id)
    if active:
        await message.reply(f"🚫 Вы ограничены на {penalty//60} минут.")
        return

    if rate_limited(user_id):
        await message.reply("🚫 Слишком много запросов. Не более 10 проверок за 30 секунд.")
        return

    if len(input_text) > 100 or input_text.count(".") > 5:
        timeout = register_violation(user_id)
        await message.reply(f"⚠️ Сообщение не похоже на домен. Пользователь ограничен на {timeout//60} минут.")
        return

    domain = extract_domain(input_text)
    if not domain:
        timeout = register_violation(user_id)
        await message.reply(f"❌ Не удалось извлечь домен. Пользователь ограничен на {timeout//60} минут.")
        return

    r = await get_redis()
    try:
        cached = await r.get(f"result:{domain}")
        if cached:
            await message.answer(f"⚡ Результат из кэша:\n\n{cached}")
            return

        await enqueue(domain, user_id)
        await message.answer(f"✅ <b>{domain}</b> поставлен в очередь на проверку.")
    except Exception as e:
        logging.error(f"Failed to process domain {domain}: {str(e)}")
        await message.reply(f"❌ Ошибка обработки {domain}")
    finally:
        await r.aclose()  # Используем aclose() вместо close()

if __name__ == "__main__":
    from aiogram import executor
    executor.start_polling(dp)
