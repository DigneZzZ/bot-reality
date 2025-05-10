import asyncio
from aiogram import Bot, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import redis.asyncio as redis
from redis_queue import enqueue, get_redis
from collections import defaultdict
from time import time
import re
from urllib.parse import urlparse
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

# Настройка логирования
log_dir = "/app"
log_file = os.path.join(log_dir, "bot.log")
fallback_log_file = "/tmp/bot.log"
os.makedirs(log_dir, exist_ok=True)
log_handlers = []

try:
    with open(log_file, "a") as f:
        f.write("")
    file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
    log_handlers.append(file_handler)
except Exception as e:
    logging.warning(f"Failed to initialize logging to {log_file}: {str(e)}. Falling back to {fallback_log_file}")
    os.makedirs("/tmp", exist_ok=True)
    file_handler = RotatingFileHandler(fallback_log_file, maxBytes=10*1024*1024, backupCount=5)
    log_handlers.append(file_handler)

log_handlers.append(logging.StreamHandler())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=log_handlers
)
logging.info("Logging initialized")

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
bot = Bot(token=TOKEN, parse_mode="HTML")
router = Router()

def get_main_keyboard(is_admin: bool):
    buttons = [
        [InlineKeyboardButton(text="Проверить домен", callback_data="check")],
        [InlineKeyboardButton(text="Полный отчёт", callback_data="full")],
        [InlineKeyboardButton(text="Пинг", callback_data="ping")],
        [InlineKeyboardButton(text="История", callback_data="history")]
    ]
    if is_admin:
        buttons.extend([
            [InlineKeyboardButton(text="Список пригодных доменов", callback_data="approved")],
            [InlineKeyboardButton(text="Очистить список доменов", callback_data="clear_approved")],
            [InlineKeyboardButton(text="Экспортировать домены", callback_data="export_approved")]
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_full_report_button(domain: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Полный отчёт", callback_data=f"full_report:{domain}")]
    ])
    return keyboard

async def get_redis():
    try:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
        logging.debug("Connected to Redis")
        return redis_client
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

user_requests = defaultdict(list)
user_violations = {}

def extract_domain(text: str):
    text = text.strip()
    text = re.sub(r':\d+$', '', text)
    if text.startswith("http://") or text.startswith("https://"):
        try:
            parsed = urlparse(text)
            if parsed.hostname:
                return parsed.hostname
        except:
            return None
    if re.match(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$", text):
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
    duration = [60, 300, 900, 3600]
    if record["count"] >= 5:
        stage = record["count"] - 5
        timeout = duration[min(stage, len(duration) - 1)]
        record["until"] = time() + timeout
    user_violations[user_id] = record
    return int(record["until"] - time()) if record["count"] >= 5 else 0

async def check_rate_limit(user_id: int) -> bool:
    r = await get_redis()
    try:
        key = f"rate:{user_id}:{datetime.now().strftime('%Y%m%d%H%M')}"
        count = await r.get(key)
        count = int(count) if count else 0
        if count >= 10:
            logging.warning(f"Rate limit exceeded for user {user_id}: {count} requests")
            return False
        await r.incr(key)
        await r.expire(key, 60)
        return True
    finally:
        await r.aclose()

async def check_daily_limit(user_id: int) -> bool:
    r = await get_redis()
    try:
        key = f"daily:{user_id}:{datetime.now().strftime('%Y%m%d')}"
        count = await r.get(key)
        count = int(count) if count else 0
        if count >= 100:
            logging.warning(f"Daily limit exceeded for user {user_id}: {count} requests")
            return False
        await r.incr(key)
        await r.expire(key, 86400)
        return True
    finally:
        await r.aclose()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    logging.debug(f"Processing /start for user {user_id} (is_admin={is_admin})")
    welcome_message = (
        "👋 <b>Привет!</b> Я бот для проверки доменов на пригодность для прокси и Reality.\n\n"
        "📋 <b>Доступные команды:</b>\n"
        "/check \"домен\" — Проверить домен (краткий отчёт, например, <code>/check example.com</code>)\n"
        "/full \"домен\" — Проверить домен (полный отчёт, например, <code>/full example.com</code>)\n"
        "/mode — Переключить режим вывода (краткий/полный)\n"
        "/ping — Убедиться, что бот работает\n"
        "/history — Показать последние 10 проверок\n"
        "/whoami — Показать ваш Telegram ID\n"
    )
    if is_admin:
        welcome_message += (
            "\n🔧 <b>Админ-команды:</b>\n"
            "/approved — Показать список пригодных доменов\n"
            "/clear_approved — Очистить список пригодных доменов\n"
            "/export_approved — Экспортировать список доменов в файл\n"
        )
    welcome_message += (
        "\n📩 Можно отправить несколько доменов (через запятую или перенос строки), например:\n"
        "<code>example.com, google.com</code>\n"
        "🚀 Выбери действие ниже!"
    )
    try:
        await message.answer(welcome_message, reply_markup=get_main_keyboard(is_admin))
        logging.info(f"Sent welcome message to user {user_id} (is_admin={is_admin})")
    except Exception as e:
        logging.error(f"Failed to send welcome message to user {user_id}: {str(e)}")
        await message.answer("❌ Ошибка при отправке сообщения. Попробуйте позже.")

@router.message(Command("mode"))
async def cmd_mode(message: types.Message):
    user_id = message.from_user.id
    r = await get_redis()
    try:
        current_mode = await r.get(f"mode:{user_id}")
        current_mode = current_mode or "short"
        new_mode = "full" if current_mode == "short" else "short"
        await r.set(f"mode:{user_id}", new_mode)
        await message.reply(f"✅ Режим вывода изменён на: {new_mode}")
        logging.info(f"User {user_id} changed mode to {new_mode}")
    except Exception as e:
        logging.error(f"Failed to change mode for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при смене режима.")
    finally:
        await r.aclose()

@router.message(Command("whoami"))
async def cmd_whoami(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id == ADMIN_ID
    await message.reply(f"Ваш Telegram ID: {user_id}\nАдмин: {'Да' if is_admin else 'Нет'}")
    logging.info(f"User {user_id} executed /whoami (is_admin={is_admin})")

@router.message(Command("ping"))
async def cmd_ping(message: types.Message):
    user_id = message.from_user.id
    await message.reply("🏓 Я жив!")
    logging.info(f"User {user_id} executed /ping")

@router.message(Command("history"))
async def cmd_history(message: types.Message):
    user_id = message.from_user.id
    r = await get_redis()
    try:
        history = await r.lrange(f"history:{user_id}", 0, 9)
        if not history:
            await message.reply("📜 Ваша история проверок пуста.")
            return
        response = "📜 <b>Ваши последние проверки (максимум 10):</b>\n"
        for i, entry in enumerate(history, 1):
            response += f"{i}. {entry}\n"
        await message.reply(response)
        logging.info(f"User {user_id} viewed history with {len(history)} entries")
    except Exception as e:
        logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при получении истории.")
    finally:
        await r.aclose()

@router.message(Command("approved"))
async def cmd_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /approved")
        return
    r = await get_redis()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пригодных доменов пуст.")
            return
        response = "📜 <b>Пригодные домены:</b>\n"
        for i, domain in enumerate(sorted(domains), 1):
            response += f"{i}. {domain}\n"
        await message.reply(response)
        logging.info(f"User {user_id} viewed approved domains ({len(domains)} entries)")
    except Exception as e:
        logging.error(f"Failed to fetch approved domains for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при получении списка доменов.")
    finally:
        await r.aclose()

@router.message(Command("clear_approved"))
async def cmd_clear_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /clear_approved")
        return
    r = await get_redis()
    try:
        deleted = await r.delete("approved_domains")
        await message.reply("✅ Список пригодных доменов очищен." if deleted else "📜 Список пригодных доменов уже пуст.")
        logging.info(f"User {user_id} cleared approved domains")
    except Exception as e:
        logging.error(f"Failed to clear approved domains for user {user_id}: {str(e)}")
        await message.reply("❌ Ошибка при очистке списка доменов.")
    finally:
        await r.aclose()

@router.message(Command("export_approved"))
async def cmd_export_approved(message: types.Message):
    user_id = message.from_user.id
    if user_id != ADMIN_ID:
        await message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted to access /export_approved")
        return
    r = await get_redis()
    try:
        domains = await r.smembers("approved_domains")
        if not domains:
            await message.reply("📜 Список пригодных доменов пуст. Экспорт не выполнен.")
            return
        file_path = "/app/approved_domains.txt"
        with open(file_path, "w") as f:
            for domain in sorted(domains):
                f.write(f"{domain}\n")
        await message.reply(f"✅ Список доменов экспортирован в {file_path} ({len(domains)} доменов).")
        logging.info(f"User {user_id} exported {len(domains)} approved domains to {file_path}")
    except Exception as e:
        logging.error(f"Failed to export approved domains for user {user_id}: {str(e)}")
        await message.reply(f"❌ Ошибка при экспорте списка доменов: {str(e)}")
    finally:
        await r.aclose()

@router.message(Command("check", "full"))
async def cmd_check(message: types.Message):
    user_id = message.from_user.id
    command = message.get_command()
    short_mode = command == "/check"
    args = message.get_args().strip()
    if not args:
        await message.reply(f"⛔ Укажи домен, например: {command} example.com")
        return
    if not await check_rate_limit(user_id):
        await message.reply("🚫 Слишком много запросов. Не более 10 в минуту.")
        return
    if not await check_daily_limit(user_id):
        await message.reply("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
    await handle_domain_logic(message, args, short_mode=short_mode)
    logging.info(f"User {user_id} executed {command} with args: {args}")

@router.message()
async def handle_domain(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    if not text or text.startswith("/"):
        logging.debug(f"Ignoring command or empty message from user {user_id}: {text}")
        return
    if not await check_rate_limit(user_id):
        await message.reply("🚫 Слишком много запросов. Не более 10 в минуту.")
        return
    if not await check_daily_limit(user_id):
        await message.reply("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
        return
    await handle_domain_logic(message, text, short_mode=True)
    logging.info(f"User {user_id} sent domain: {text}")

@router.callback_query()
async def process_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    is_admin = user_id == ADMIN_ID
    logging.debug(f"Processing callback {callback_query.data} for user {user_id} (is_admin={is_admin})")
    if callback_query.data == "check":
        await callback_query.message.answer("⛔ Укажи домен, например: /check example.com")
    elif callback_query.data == "full":
        await callback_query.message.answer("⛔ Укажи домен, например: /full example.com")
    elif callback_query.data == "ping":
        await callback_query.message.answer("🏓 Я жив!")
        logging.info(f"User {user_id} triggered ping callback")
    elif callback_query.data == "history":
        r = await get_redis()
        try:
            history = await r.lrange(f"history:{user_id}", 0, 9)
            if not history:
                await callback_query.message.reply("📜 Ваша история проверок пуста.")
            else:
                response = "📜 <b>Ваши последние проверки (максимум 10):</b>\n"
                for i, entry in enumerate(history, 1):
                    response += f"{i}. {entry}\n"
                await callback_query.message.reply(response)
            logging.info(f"User {user_id} viewed history via callback with {len(history)} entries")
        except Exception as e:
            logging.error(f"Failed to fetch history for user {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при получении истории.")
        finally:
            await r.aclose()
    elif callback_query.data == "approved" and is_admin:
        r = await get_redis()
        try:
            domains = await r.smembers("approved_domains")
            if not domains:
                await callback_query.message.reply("📜 Список пригодных доменов пуст.")
            else:
                response = "📜 <b>Пригодные домены:</b>\n"
                for i, domain in enumerate(sorted(domains), 1):
                    response += f"{i}. {domain}\n"
                await callback_query.message.reply(response)
            logging.info(f"User {user_id} viewed approved domains via callback ({len(domains)} entries)")
        except Exception as e:
            logging.error(f"Failed to fetch approved domains for user {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при получении списка доменов.")
        finally:
            await r.aclose()
    elif callback_query.data == "clear_approved" and is_admin:
        r = await get_redis()
        try:
            deleted = await r.delete("approved_domains")
            await callback_query.message.reply("✅ Список пригодных доменов очищен." if deleted else "📜 Список пригодных доменов уже пуст.")
            logging.info(f"User {user_id} cleared approved domains via callback")
        except Exception as e:
            logging.error(f"Failed to clear approved domains for user {user_id}: {str(e)}")
            await callback_query.message.reply("❌ Ошибка при очистке списка доменов.")
        finally:
            await r.aclose()
    elif callback_query.data == "export_approved" and is_admin:
        r = await get_redis()
        try:
            domains = await r.smembers("approved_domains")
            if not domains:
                await callback_query.message.reply("📜 Список пригодных доменов пуст. Экспорт не выполнен.")
            else:
                file_path = "/app/approved_domains.txt"
                with open(file_path, "w") as f:
                    for domain in sorted(domains):
                        f.write(f"{domain}\n")
                await callback_query.message.reply(f"✅ Список доменов экспортирован в {file_path} ({len(domains)} доменов).")
                logging.info(f"User {user_id} exported {len(domains)} approved domains to {file_path} via callback")
        except Exception as e:
            logging.error(f"Failed to export approved domains for user {user_id}: {str(e)}")
            await callback_query.message.reply(f"❌ Ошибка при экспорте списка доменов: {str(e)}")
        finally:
            await r.aclose()
    elif callback_query.data.startswith("full_report:"):
        domain = callback_query.data.split(":", 1)[1]
        r = await get_redis()
        try:
            cached = await r.get(f"result:{domain}")
            if cached and all(k in cached for k in ["🌍 География", "📄 WHOIS", "⏱️ TTFB"]):
                await callback_query.message.answer(f"⚡ Полный отчёт для {domain}:\n\n{cached}")
            else:
                if not await check_rate_limit(user_id):
                    await callback_query.message.answer("🚫 Слишком много запросов. Не более 10 в минуту.")
                elif not await check_daily_limit(user_id):
                    await callback_query.message.answer("🚫 Достигнут дневной лимит (100 проверок). Попробуйте завтра.")
                else:
                    enqueued = await enqueue(domain, user_id, short_mode=False)
                    if enqueued:
                        await callback_query.message.answer(f"✅ <b>{domain}</b> поставлен в очередь на полный отчёт.")
                    else:
                        await callback_query.message.answer(f"⚠️ <b>{domain}</b> уже в очереди на проверку.")
                logging.info(f"Enqueued {domain} for full report due to incomplete cache")
        except Exception as e:
            logging.error(f"Failed to process full report for {domain} by user {user_id}: {str(e)}")
            await callback_query.message.answer(f"❌ Ошибка: {str(e)}")
        finally:
            await r.aclose()
    else:
        await callback_query.message.reply("⛔ Доступ к этой команде ограничен.")
        logging.warning(f"User {user_id} attempted unauthorized callback: {callback_query.data}")
    await callback_query.answer()

async def handle_domain_logic(message: types.Message, input_text: str, inconclusive_domain_limit=5, short_mode: bool = True):
    user_id = message.from_user.id
    penalty, active = get_penalty(user_id)
    if active:
        await message.reply(f"🚫 Вы ограничены на {penalty//60} минут.")
        return

    r = await get_redis()
    try:
        user_mode = await r.get(f"mode:{user_id}")
        short_mode = user_mode != "full"
    finally:
        await r.aclose()

    domains = [d.strip() for d in re.split(r'[,\n]', input_text) if d.strip()]
    if not domains:
        await message.reply("❌ Не удалось извлечь домены. Укажите валидные домены, например: example.com")
        return

    r = await get_redis()
    try:
        valid_domains = []
        invalid_domains = []
        for domain in domains:
            extracted = extract_domain(domain)
            if extracted:
                valid_domains.append(extracted)
            else:
                invalid_domains.append(domain)
                logging.warning(f"Invalid domain input: {domain} by user {user_id}")

        if invalid_domains:
            await message.reply(
                f"⚠️ Следующие домены невалидны и будут пропущены:\n" +
                "\n".join(f"- {d}" for d in invalid_domains)
            )

        if not valid_domains:
            if len(invalid_domains) >= inconclusive_domain_limit:
                timeout = register_violation(user_id)
                await message.reply(f"❌ Все домены невалидны. Пользователь ограничен на {timeout//60} минут.")
            else:
                await message.reply("❌ Не найдено валидных доменов. Укажите корректные домены, например: example.com")
            return

        for domain in valid_domains:
            cached = await r.get(f"result:{domain}")
            if cached:
                if short_mode:
                    lines = cached.split("\n")
                    cached = "\n".join(
                        line for line in lines
                        if any(k in line for k in ["🔍 Проверка", "🔒 TLS", "🌐 HTTP", "🛡️ CDN", "🔌 Открытые порты", "✅", "🟢", "❌"])
                    )
                    await message.answer(
                        f"⚡ Результат из кэша для {domain}:\n\n{cached}",
                        reply_markup=get_full_report_button(domain)
                    )
                else:
                    await message.answer(f"⚡ Результат из кэша для {domain}:\n\n{cached}")
                logging.info(f"Returned cached result for {domain} to user {user_id}")
            else:
                enqueued = await enqueue(domain, user_id, short_mode=short_mode)
                if enqueued:
                    await message.answer(f"✅ <b>{domain}</b> поставлен в очередь на проверку.")
                else:
                    await message.answer(f"⚠️ <b>{domain}</b> уже в очереди на проверку.")
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
    logging.info("Starting bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        logging.info("Bot polling stopped.")

if __name__ == "__main__":
    logging.debug("Starting bot script")
    asyncio.run(main())
