import asyncio
from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
from redis_queue import enqueue
from collections import defaultdict
from time import time
import redis.asyncio as redis
import re
from urllib.parse import urlparse
import logging
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="bot.log", format="%(asctime)s - %(levelname)s - %(message)s")

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(token=TOKEN, parse_mode="HTML")
router = Router()

# Создание инлайн-клавиатуры
def get_main_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Проверить домен", callback_data="check")],
        [InlineKeyboardButton(text="Пинг", callback_data="ping")],
        [InlineKeyboardButton(text="История", callback_data="history")]
    ])
    return keyboard

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
    # Удаляем порт, если указан (например, oogle.com:443 → oogle.com)
    text = re.sub(r':\d+$', '', text.strip())
    if text.startswith("http://") or text.startswith("https://"):
        try:
            parsed = urlparse(text)
            if parsed.hostname:
                return parsed.hostname
        except:
            return None
    # Проверяем, является ли строка валидным доменом
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,253}[a-zA-Z0-9]$", text):
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

async def check_daily_limit(user_id):
    r = await get_redis()
    try:
        key = f"daily:{user_id}:{datetime.now().strftime('%Y%m%d')}"
        count = await r.get(key)
        count = int(count) if count else 0
        if count >= 100:
            return False
        await r.incr(key)
        await r.expire(key, 86400)  # 24 часа
        return True
    finally:
        await r.aclose()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    welcome_message = (
        "👋 <b>Привет!</b> Я бот для проверки доменов на пригодность для прокси и Reality.\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "/check <домен> — Проверить домен (краткий отчёт, например, <code>/check example.com</code>)\n"
        "/full <домен> — Проверить домен (полный отчёт, например, <code>/full example.com</code>)\n"
        "/ping — Убедиться, что бот работает\n"
        "/history — Показать последние проверки\n\n"
        "📩 Можно отправить несколько доменов (через запятую или перенос строки), например:\n"
        "<code>example.com, google.com</code>\n"
        "🚀 Выбери действие ниже!"
    )
    await message.answer(welcome_message, reply_markup=get_main_keyboard())

@router.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.reply("🏓 Я жив!")

@router.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = message.from_user.id
    r = await get_redis()
    try:
        history = await r.lrange(f"history:{user_id}", 0, -1)
        if not history:
            await message.reply("📜 История проверок пуста.")
            return
        response = "📜 <b>Последние проверки:</b>\n" + "\n".join(history)
        await message.reply(response)
    except Exception as e:
        logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при получении истории.")
    finally:
        await r.aclose()

@router.message(Command("check", "full"))
async def cmd_check(message: types.Message):
    command = message.get_command()
    short_mode = command == "/check"
    args = message.get_args().strip()
    if not args:
        await message.reply(f"⛔ Укажи домен, например: {command} example.com")
        return
    await handle_domain_logic(message, args, short_mode=short_mode)

@router.message()
async def handle_domain(message: types.Message):
    text = message.text.strip()
    if not text or extract_domain(text) is None:
        await message.reply("⛔ Укажи валидный домен, например: example.com")
        return
    await handle_domain_logic(message, text, short_mode=True)

@router.callback_query()
async def process_callback(callback_query: types.CallbackQuery):
    if callback_query.data == "check":
        await callback_query.message.answer("⛔ Укажи домен, например: /check example.com")
    elif callback_query.data == "ping":
        await callback_query.message.answer("🏓 Я жив!")
    elif callback_query.data == "history":
        user_id = callback_query.from_user.id
        r = await get_redis()
        try:
            history = await r.lrange(f"history:{user_id}", 0, -1)
            if not history:
                await callback_query.message.reply("📜 История проверок пуста.")
            else:
                response = "📜 <b>Последние проверки:</b>\n" + "\n".join(history)
                await callback_query.message.reply(response)
        except Exception as e:
            logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при получении истории.")
        finally:
            await r.aclose()
    await callback_query.answer()

async def handle_domain_logic(message: types.Message, input_text: str, short_mode: bool = True):
    user_id = message.from_user.id
    penalty, active = get_penalty(user_id)
    if active:
        await message.reply(f"🚫 Вы ограничены на {penalty//60} минут.")
        return

    if not await check_daily_limit(user_id):
        await message.reply("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return

    if rate_limited(user_id):
        await message.reply("🚫 Слишком много запросов. Не более 10 проверок за 30 секунд.")
        return

    domains = [d.strip() for d in input_text.replace(',', '\n').split('\n') if d.strip()]
    if not domains:
        timeout = register_violation(user_id)
        await message.reply(f"❌ Не удалось извлечь домены. Пользователь ограничен на {timeout//60} минут.")
        return

    r = await get_redis()
    try:
        valid_domains = []
        for domain in domains:
            extracted = extract_domain(domain)
            if extracted:
                valid_domains.append(extracted)
            else:
                await message.reply(f"⚠️ {domain} не является валидным доменом, пропущен.")
                logging.warning(f"Invalid domain input: {domain} by user {user_id}")
        if not valid_domains:
            timeout = register_violation(user_id)
            await message.reply(f"❌ Ни один домен не распознан. Пользователь ограничен на {timeout//60} минут.")
            return

        for domain in valid_domains:
            cached = await r.get(f"result:{domain}")
            if cached:
                if short_mode:
                    lines = cached.split("\n")
                    cached = "\n".join(
                        line for line in lines
                        if any(k in line for k in ["🔍 Проверка", "🔒 TLS", "🌐 HTTP", "🛰 Оценка пригодности", "✅", "🟢", "❌"])
                    )
                await message.answer(f"⚡ Результат из кэша для {domain}:\n\n{cached}")
                logging.info(f"Returned cached result for {domain} to user {user_id}")
            else:
                await enqueue(domain, user_id, short_mode=short_mode)
                await message.answer(f"✅ <b>{domain}</b> поставлен в очередь на проверку.")
                logging.info(f"Enqueued {domain} for user {user_id} (short_mode={short_mode})")
    except Exception as e:
        logging.error(f"Failed to process domains for user {user_id}: {str(e)}")
        await message.reply(f"❌ Ошибка: {str(e)}")
    finally:
        await r.aclose()

async def main():
    from aiogram import Dispatcher
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
