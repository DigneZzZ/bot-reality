import asyncio
import httpx
import subprocess
import dns.resolver
import redis.asyncio as redis
import logging
import os
from logging.handlers import RotatingFileHandler
from redis_queue import get_redis
from aiogram import Bot

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

async def check_http_version(domain: str) -> dict:
    result = {"http_version": "unknown", "alt_svc": None, "error": None}
    try:
        async with httpx.AsyncClient(http2=True, timeout=10) as client:
            resp = await client.get(f"https://{domain}", follow_redirects=True)
            http_version = resp.extensions.get("http_version", b"").decode("utf-8", errors="ignore")
            if http_version == "HTTP/2":
                result["http_version"] = "HTTP/2"
            elif http_version == "HTTP/1.1":
                result["http_version"] = "HTTP/1.1"
            else:
                result["http_version"] = "HTTP/1.1"
            result["alt_svc"] = resp.headers.get("alt-svc")
            logging.info(f"HTTP check for {domain}: {result['http_version']}, alt-svc: {result['alt_svc']}")
    except Exception as e:
        result["error"] = f"HTTP check failed: {str(e)}"
        logging.error(f"HTTP check failed for {domain}: {str(e)}")

    if result["http_version"] == "unknown" or result["error"]:
        try:
            curl_cmd = [
                "curl", "-s", "-I", "--http2", f"https://{domain}",
                "-H", "User-Agent: Mozilla/5.0", "--max-time", "10"
            ]
            process = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=15)
            headers = process.stdout
            if "HTTP/2" in headers:
                result["http_version"] = "HTTP/2"
            elif "HTTP/1.1" in headers:
                result["http_version"] = "HTTP/1.1"
            if "alt-svc" in headers.lower():
                for line in headers.splitlines():
                    if line.lower().startswith("alt-svc"):
                        result["alt_svc"] = line.split(":", 1)[1].strip()
            logging.info(f"Curl fallback for {domain}: {result['http_version']}, alt-svc: {result['alt_svc']}")
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            result["error"] = f"Curl fallback failed: {str(e)}"
            logging.error(f"Curl fallback failed for {domain}: {str(e)}")

    if result["alt_svc"] and "h3" in result["alt_svc"]:
        result["http_version"] = "HTTP/3"
        logging.info(f"Detected HTTP/3 via alt-svc for {domain}")

    return result

async def check_cname(domain: str) -> dict:
    result = {"cdn": None, "cname": None, "error": None}
    try:
        answers = dns.resolver.resolve(domain, "CNAME", raise_on_no_answer=False)
        if answers:
            cname = str(answers[0].target)
            result["cname"] = cname
            if "cloudflare" in cname.lower():
                result["cdn"] = "Cloudflare"
            elif "akamai" in cname.lower():
                result["cdn"] = "Akamai"
            elif "fastly" in cname.lower():
                result["cdn"] = "Fastly"
            logging.info(f"CNAME check for {domain}: {cname}, detected CDN: {result['cdn']}")
    except Exception as e:
        result["error"] = f"CNAME check failed: {str(e)}"
        logging.debug(f"No CNAME or error for {domain}: {str(e)}")
    return result

async def scan_ports(domain: str, ports: list = [80, 443, 8080], timeout: float = 2.0) -> dict:
    result = {"open_ports": [], "error": None}
    async def check_port(port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(domain, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            return False

    tasks = [check_port(port) for port in ports]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for port, is_open in zip(ports, results):
        if is_open and not isinstance(is_open, Exception):
            result["open_ports"].append(port)
    logging.info(f"Port scan for {domain}: open ports {result['open_ports']}")
    return result

async def check_domain(domain: str, user_id: int, short_mode: bool) -> str:
    logging.info(f"Starting check for {domain} for user {user_id}, short_mode={short_mode}")
    http_result = await check_http_version(domain)
    cname_result = await check_cname(domain)
    ports_result = await scan_ports(domain)

    r = await get_redis()
    try:
        output = f"🔍 Проверка {domain}:\n"
        output += f"🌐 HTTP: {http_result['http_version']}\n"
        if http_result["alt_svc"]:
            output += f"Alt-Svc: {http_result['alt_svc']}\n"
        if http_result["error"]:
            output += f"HTTP Error: {http_result['error']}\n"
        if cname_result["cdn"]:
            output += f"🛡️ CDN: {cname_result['cdn']}\n"
        if cname_result["cname"]:
            output += f"DNS CNAME: {cname_result['cname']}\n"
        if cname_result["error"]:
            output += f"CNAME Error: {cname_result['error']}\n"
        if ports_result["open_ports"]:
            output += f"🔌 Открытые порты: {', '.join(map(str, ports_result['open_ports']))}\n"
        # Моковые данные для полного отчёта
        output += f"🌍 География: Неизвестно\n"
        output += f"📄 WHOIS: Неизвестно\n"
        output += f"⏱️ TTFB: Неизвестно\n"
        output += f"🟢 Пригодность: Неизвестно\n"

        if short_mode:
            lines = output.split("\n")
            output = "\n".join(
                line for line in lines
                if any(k in line for k in ["🔍 Проверка", "🌐 HTTP", "🛡️ CDN", "🔌 Открытые порты", "🟢 Пригодность"])
            )

        await r.set(f"result:{domain}", output, ex=86400)
        await r.lpush(f"history:{user_id}", f"{domain}: {'Краткий' if short_mode else 'Полный'} отчёт")
        await r.ltrim(f"history:{user_id}", 0, 9)
        await r.delete(f"pending:{domain}:{user_id}")
        logging.info(f"Processed {domain} for user {user_id}, short_mode={short_mode}")
    except Exception as e:
        logging.error(f"Failed to save result for {domain}: {str(e)}")
        output = f"❌ Ошибка при проверке {domain}: {str(e)}"
    finally:
        await r.aclose()

    return output

async def worker():
    logging.info("Starting worker process")
    r = await get_redis()
    try:
        # Проверяем соединение с Redis
        await r.ping()
        logging.info("Successfully connected to Redis")
        while True:
            try:
                _, task = await r.brpop("queue:domains", timeout=5)
                if task:
                    logging.info(f"Popped task from queue: {task}")
                    domain, user_id, short_mode = task.split(":")
                    user_id = int(user_id)
                    short_mode = short_mode == "True"
                    result = await check_domain(domain, user_id, short_mode)
                    try:
                        await bot.send_message(user_id, result)
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
