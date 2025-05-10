import asyncio
import httpx
import subprocess
import dns.resolver
import redis.asyncio as redis
import logging
import os
import ssl
import socket
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from redis_queue import get_redis
from aiogram import Bot
from bot import get_full_report_button

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

async def check_dns(domain: str) -> dict:
    result = {"a_record": None, "error": None}
    try:
        answers = dns.resolver.resolve(domain, "A")
        result["a_record"] = str(answers[0].address)
        logging.info(f"DNS A record for {domain}: {result['a_record']}")
    except Exception as e:
        result["error"] = f"DNS check failed: {str(e)}"
        logging.error(f"DNS check failed for {domain}: {str(e)}")
    return result

async def check_tls(domain: str, port: int = 443) -> dict:
    result = {"tls_version": None, "cipher": None, "cert_expiry": None, "error": None}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, port), timeout=5) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                result["tls_version"] = ssock.version()
                result["cipher"] = ssock.cipher()[0]
                cert = ssock.getpeercert()
                expiry = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry - datetime.now()).days
                result["cert_expiry"] = f"истекает через {days_left} дн."
        logging.info(f"TLS check for {domain}: {result['tls_version']}, {result['cipher']}, {result['cert_expiry']}")
    except Exception as e:
        result["error"] = f"TLS check failed: {str(e)}"
        logging.error(f"TLS check failed for {domain}: {str(e)}")
    return result

async def check_http_version(domain: str) -> dict:
    result = {"http_version": "unknown", "alt_svc": None, "ttfb": None, "redirect": None, "server": None, "error": None}
    start_time = time.time()
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
            result["ttfb"] = f"{(time.time() - start_time) * 1000:.1f} ms"
            result["redirect"] = "Без редиректа" if str(resp.url) == f"https://{domain}/" else f"Редирект на {resp.url}"
            result["server"] = resp.headers.get("server", "неизвестен")
            logging.info(f"HTTP check for {domain}: {result['http_version']}, alt-svc: {result['alt_svc']}, TTFB: {result['ttfb']}")
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
            result["server"] = "неизвестен"
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

async def check_waf(domain: str) -> dict:
    result = {"waf": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://{domain}")
            headers = resp.headers
            if "server" in headers and "cloudflare" in headers["server"].lower():
                result["waf"] = "Cloudflare"
            elif "x-waf" in headers or "x-firewall" in headers:
                result["waf"] = "Unknown WAF"
            logging.info(f"WAF check for {domain}: {result['waf']}")
    except Exception as e:
        result["error"] = f"WAF check failed: {str(e)}"
        logging.error(f"WAF check failed for {domain}: {str(e)}")
    return result

async def check_geo_asn(ip: str) -> dict:
    result = {"location": "Неизвестно", "asn": "Неизвестно", "spamhaus": False, "ping": None}
    try:
        # Моковые данные для географии и ASN
        result["location"] = "Неизвестно"
        result["asn"] = "Неизвестно"
        # Проверка пинга
        start_time = time.time()
        process = subprocess.run(["ping", "-c", "1", ip], capture_output=True, text=True, timeout=5)
        if process.returncode == 0:
            ping_time = float(process.stdout.split("time=")[1].split(" ms")[0])
            result["ping"] = f"~{ping_time:.1f} ms"
        logging.info(f"Geo/ASN check for {ip}: {result['location']}, {result['asn']}, ping: {result['ping']}")
    except Exception as e:
        logging.error(f"Geo/ASN check failed for {ip}: {str(e)}")
    return result

async def check_whois(domain: str) -> dict:
    result = {"expiry": "Неизвестно", "error": None}
    try:
        # Моковые данные для WHOIS
        result["expiry"] = "Неизвестно"
        logging.info(f"WHOIS check for {domain}: {result['expiry']}")
    except Exception as e:
        result["error"] = f"WHOIS check failed: {str(e)}"
        logging.error(f"WHOIS check failed for {domain}: {str(e)}")
    return result

async def scan_ports(domain: str, ports: list = [80, 443, 8080, 8443], timeout: float = 2.0) -> dict:
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

async def evaluate_suitability(http_result: dict, tls_result: dict, cname_result: dict, waf_result: dict) -> str:
    reasons = []
    if http_result["http_version"] not in ["HTTP/2", "HTTP/3"]:
        reasons.append("HTTP/2 отсутствует")
    if not tls_result["tls_version"] or tls_result["tls_version"] != "TLSv1.3":
        reasons.append("TLSv1.3 отсутствует")
    if cname_result["cdn"]:
        reasons.append(f"CDN обнаружен: {cname_result['cdn']}")
    if waf_result["waf"]:
        reasons.append(f"WAF обнаружен: {waf_result['waf']}")
    if reasons:
        return f"❌ Не пригоден: {', '.join(reasons)}"
    return "✅ Пригоден для Reality"

async def check_domain(domain: str, user_id: int, short_mode: bool) -> str:
    logging.info(f"Starting check for {domain} for user {user_id}, short_mode={short_mode}")
    try:
        async with asyncio.timeout(300):  # Таймаут 5 минут
            dns_result = await check_dns(domain)
            http_result = await check_http_version(domain)
            tls_result = await check_tls(domain)
            cname_result = await check_cname(domain)
            waf_result = await check_waf(domain)
            geo_asn_result = await check_geo_asn(dns_result["a_record"] or domain)
            whois_result = await check_whois(domain)
            ports_result = await scan_ports(domain)
            suitability = await evaluate_suitability(http_result, tls_result, cname_result, waf_result)
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
        # Формируем полный отчёт
        full_output = f"🔍 Проверка {domain}:\n"
        full_output += f"✅ A: {dns_result['a_record'] or 'неизвестно'}\n"
        if dns_result["error"]:
            full_output += f"DNS Error: {dns_result['error']}\n"
        full_output += "\n🌐 DNS\n"
        full_output += f"✅ A: {dns_result['a_record'] or 'неизвестно'}\n"
        full_output += "\n📡 Скан портов\n"
        for port in [80, 443, 8080, 8443]:
            status = "🟢 открыт" if port in ports_result["open_ports"] else "🔴 закрыт"
            full_output += f"TCP {port} {status}\n"
        full_output += "\n🌍 География и ASN\n"
        full_output += f"📍 IP: {geo_asn_result['location']}\n"
        full_output += f"🏢 ASN: {geo_asn_result['asn']}\n"
        full_output += f"✅ Не найден в Spamhaus\n"
        full_output += f"🟢 Ping: {geo_asn_result['ping'] or 'неизвестно'}\n"
        full_output += "\n🔒 TLS\n"
        if tls_result["tls_version"]:
            full_output += f"✅ {tls_result['tls_version']} поддерживается\n"
        if tls_result["cipher"]:
            full_output += f"✅ {tls_result['cipher']} используется\n"
        if tls_result["cert_expiry"]:
            full_output += f"⏳ TLS сертификат {tls_result['cert_expiry']}\n"
        if tls_result["error"]:
            full_output += f"TLS Error: {tls_result['error']}\n"
        full_output += "\n🌐 HTTP\n"
        full_output += f"{'✅' if http_result['http_version'] in ['HTTP/2', 'HTTP/3'] else '❌'} {http_result['http_version']} {'поддерживается' if http_result['http_version'] in ['HTTP/2', 'HTTP/3'] else 'не поддерживается'}\n"
        if http_result["alt_svc"] and "h3" in http_result["alt_svc"]:
            full_output += f"✅ HTTP/3 (h3) поддерживается\n"
        else:
            full_output += f"❌ HTTP/3 не поддерживается\n"
        full_output += f"⏱️ TTFB: {http_result['ttfb'] or 'неизвестно'}\n"
        full_output += f"🔁 {http_result['redirect']}\n"
        full_output += f"🧾 Сервер: {http_result['server']}\n"
        full_output += f"🟢 WAF {('не обнаружен' if not waf_result['waf'] else f'обнаружен: {waf_result["waf"]}')}\n"
        full_output += f"🟢 CDN {('не обнаружен' if not cname_result['cdn'] else f'обнаружен: {cname_result["cdn"]}')}\n"
        if cname_result["cname"]:
            full_output += f"DNS CNAME: {cname_result['cname']}\n"
        full_output += "\n📄 WHOIS\n"
        full_output += f"📆 Срок действия: {whois_result['expiry']}\n"
        full_output += "\n🛰 Оценка пригодности\n"
        full_output += f"{suitability}\n"

        # Сохраняем полный отчёт в кэш
        await r.set(f"result:{domain}", full_output, ex=86400)

        # Формируем краткий отчёт для пользователя, если short_mode=True
        output = full_output
        if short_mode:
            output = f"🔍 Проверка {domain}:\n"
            output += f"✅ A: {dns_result['a_record'] or 'неизвестно'}\n"
            output += f"🟢 Ping: {geo_asn_result['ping'] or 'неизвестно'}\n"
            output += "    🔒 TLS\n"
            output += f"✅ {tls_result['tls_version']} поддерживается\n" if tls_result["tls_version"] else "❌ TLS не поддерживается\n"
            output += "    🌐 HTTP\n"
            output += f"{'✅' if http_result['http_version'] in ['HTTP/2', 'HTTP/3'] else '❌'} {http_result['http_version']} {'поддерживается' if http_result['http_version'] in ['HTTP/2', 'HTTP/3'] else 'не поддерживается'}\n"
            output += f"{'✅ HTTP/3 (h3) поддерживается' if http_result['alt_svc'] and 'h3' in http_result['alt_svc'] else '❌ HTTP/3 не поддерживается'}\n"
            output += f"🟢 WAF {('не обнаружен' if not waf_result['waf'] else f'обнаружен: {waf_result["waf"]}')}\n"
            output += f"🟢 CDN {('не обнаружен' if not cname_result['cdn'] else f'обнаружен: {cname_result["cdn"]}')}\n"
            output += "    🛰 Оценка пригодности\n"
            output += f"{suitability}\n"

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

async def worker():
    logging.info("Starting worker process")
    r = await get_redis()
    try:
        # Проверяем соединение с Redis
        await r.ping()
        logging.info("Successfully connected to Redis")
        while True:
            try:
                result = await r.brpop("queue:domains", timeout=5)
                if result is None:
                    continue  # Очередь пуста, продолжаем ждать
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
