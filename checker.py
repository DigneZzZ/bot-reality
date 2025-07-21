import socket
import ssl
import time
import httpx
import requests
import ping3
import whois
from datetime import datetime
import logging
import dns.resolver
import re
from logging.handlers import RotatingFileHandler
import os

# Настройка логирования с ротацией
log_dir = os.getenv("LOG_DIR", "/app")
log_file = os.path.join(log_dir, "checker.log")
os.makedirs(log_dir, exist_ok=True)

# Создаем логгер для checker
checker_logger = logging.getLogger("checker")
checker_logger.setLevel(logging.WARNING)  # Только WARNING и ERROR

# Проверяем, есть ли уже обработчики (чтобы избежать дубликатов)
if not checker_logger.handlers:
    handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    checker_logger.addHandler(handler)

CDN_PATTERNS = [
    "cloudflare", "akamai", "fastly", "incapsula", "imperva", "sucuri", "stackpath",
    "cdn77", "edgecast", "keycdn", "azure", "tencent", "alibaba", "aliyun", "bunnycdn",
    "arvan", "g-core", "mail.ru", "mailru", "vk.com", "vk", "limelight", "lumen",
    "level3", "centurylink", "cloudfront", "verizon", "google", "gws", "googlecloud",
    "x-google", "via: 1.1 google"
]

WAF_FINGERPRINTS = [
    "cloudflare", "imperva", "sucuri", "incapsula", "akamai", "barracuda"
]

FINGERPRINTS = {
    "nginx": "NGINX",
    "apache": "Apache",
    "caddy": "Caddy",
    "iis": "Microsoft IIS",
    "gws": "Google Web Server",
}

def resolve_dns(domain):
    """Разрешает DNS для домена, возвращает IPv4-адрес."""
    try:
        return socket.gethostbyname(domain)
    except Exception as e:
        checker_logger.error(f"DNS resolution failed for {domain}: {str(e)}")
        return None

def get_ping(ip, timeout=1):
    """Проверяет пинг до IP-адреса."""
    try:
        result = ping3.ping(ip, timeout=timeout, unit="ms")
        if result is not None:
            return float(result)
        return None
    except Exception as e:
        checker_logger.error(f"Ping failed for {ip}: {str(e)}")
        return None

def get_tls_info(domain, port, timeout=10):
    """Проверяет TLS: версию, шифр, срок действия сертификата."""
    info = {"tls": None, "cipher": None, "expires_days": None, "error": None}
    try:
        # Разрешаем IP домена
        ip = socket.gethostbyname(domain)
        
        # Создаем контекст TLS с минимальной версией TLSv1.3
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3  # Принудительно TLSv1.3
        ctx.set_ciphers("DEFAULT")  # Используем стандартные шифры

        # Создаем сокет и устанавливаем таймаут
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as raw_socket:
            raw_socket.settimeout(timeout)
            # Оборачиваем сокет в TLS с указанием SNI
            with ctx.wrap_socket(raw_socket, server_hostname=domain) as s:
                s.connect((ip, port))  # Подключаемся напрямую к IP
                info["tls"] = s.version()
                info["cipher"] = s.cipher()[0] if s.cipher() else None
                cert = s.getpeercert()
                expire = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                info["expires_days"] = (expire - datetime.utcnow()).days
    except Exception as e:
        info["error"] = str(e)
        checker_logger.error(f"TLS check failed for {domain}:{port}: {str(e)}")
    return info
    
def get_http_info(domain, timeout=20.0):
    """Проверяет HTTP: HTTP/2, HTTP/3, TTFB, редиректы, сервер."""
    info = {"http2": False, "http3": False, "server": "N/A", "ttfb": None, "redirect": None, "error": None, "headers": {}}
    try:
        url = f"https://{domain}"
        with httpx.Client(http2=True, timeout=timeout) as client:
            start = time.time()
            resp = client.get(url, follow_redirects=True)
            duration = time.time() - start
            info["http2"] = resp.http_version == "HTTP/2"
            alt_svc = resp.headers.get("alt-svc", "")
            info["http3"] = any("h3" in svc.lower() for svc in alt_svc.split(",") if svc.strip())
            info["server"] = resp.headers.get("server", "N/A")
            info["ttfb"] = duration
            if resp.history:
                info["redirect"] = str(resp.url)
            info["headers"] = dict(resp.headers)
            # Убираем детальное логирование заголовков
    except ImportError as e:
        info["error"] = "HTTP/2 support requires 'h2' package. Install httpx with `pip install httpx[http2]`."
        checker_logger.error(f"HTTP check failed for {domain}: {str(e)}")
    except Exception as e:
        info["error"] = str(e)
        checker_logger.error(f"HTTP check failed for {domain}: {str(e)}")
    return info

def get_domain_whois(domain):
    """Проверяет WHOIS: срок действия домена."""
    try:
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        if isinstance(exp, datetime):
            return exp.isoformat()
        return None
    except Exception as e:
        checker_logger.error(f"WHOIS check failed for {domain}: {str(e)}")
        return None

def get_ip_info(ip, timeout=5):
    """Получает геолокацию и ASN для IP."""
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=timeout).json()
        loc = f"{r.get('countryCode')} / {r.get('regionName')} / {r.get('city')}"
        asn = r.get("as", "N/A")
        return loc, asn
    except Exception as e:
        checker_logger.error(f"IP info check failed for {ip}: {str(e)}")
        return "N/A", "N/A"

def scan_ports(ip, ports=[80, 443, 8080, 8443], timeout=1):
    """Сканирует указанные порты на IP."""
    results = []
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                results.append(f"TCP {port} 🟢 открыт")
        except Exception:
            results.append(f"TCP {port} 🔴 закрыт")
    return results

def check_spamhaus(ip):
    """Проверяет, находится ли IP в чёрном списке Spamhaus."""
    try:
        rev = ".".join(reversed(ip.split("."))) + ".zen.spamhaus.org"
        resolver = dns.resolver.Resolver()
        answers = resolver.resolve(rev, "A")
        for rdata in answers:
            result = str(rdata)
            if result.startswith("127.0.0.") and 2 <= int(result.split(".")[-1]) <= 11:
                checker_logger.info(f"Spamhaus check for {ip}: listed with code {result}")
                return f"⚠️ В списке Spamhaus (код: {result})"
        checker_logger.info(f"Spamhaus check for {ip}: not listed")
        return "✅ Не найден в Spamhaus"
    except dns.resolver.NXDOMAIN:
        checker_logger.info(f"Spamhaus check for {ip}: not listed")
        return "✅ Не найден в Spamhaus"
    except Exception as e:
        checker_logger.error(f"Spamhaus check failed for {ip}: {str(e)}")
        return f"❌ Spamhaus: ошибка ({str(e)})"

def detect_cdn(http_info, asn):
    """Проверяет наличие CDN на основе заголовков, ASN и дополнительных данных с приоритетом популярных провайдеров."""
    headers_str = " ".join(f"{k}:{v}" for k, v in http_info.get("headers", {}).items() if v).lower()
    server = http_info.get("server", "N/A") or "N/A"
    text = f"{server} {headers_str}".lower()

    # Анализируем ASN
    asn_text = (asn or "").lower()
    
    # Собираем данные для поиска паттернов
    combined_text = text + " " + asn_text

    # Дополнительный запрос к ipinfo.io
    try:
        ip = socket.gethostbyname(http_info.get("domain", ""))
        ipinfo_org = requests.get(f"https://ipinfo.io/{ip}/org", timeout=5).text.lower()
        combined_text += " " + ipinfo_org
    except Exception as e:
        checker_logger.warning(f"Failed to fetch ipinfo.org for {ip}: {str(e)}")

    # Приоритетные популярные CDN (проверка по ASN и тексту)
    priority_cdns = [
        ("google", r"\b15169\b"),    # Google
        ("cloudflare", r"\b13335\b"), # Cloudflare
        ("fastly", r"\b54113\b"),    # Fastly
        ("amazon", r"\b16509\b"),    # Amazon CloudFront
        ("akamai", r"\b16625\b"),    # Akamai
    ]
    for cdn_name, asn_pattern in priority_cdns:
        if asn and re.search(asn_pattern, asn):
            return cdn_name
        if cdn_name in combined_text:
            return cdn_name

    # Оставшиеся паттерны из CDN_PATTERNS
    remaining_patterns = [
        pat for pat in CDN_PATTERNS
        if pat not in [cdn for cdn_name, _ in priority_cdns]
    ]
    for pat in remaining_patterns:
        if pat in combined_text:
            return pat

    return None

def detect_waf(server):
    """Проверяет наличие WAF на основе заголовка Server."""
    server = (server or "N/A").lower()
    for pat in WAF_FINGERPRINTS:
        if pat in server:
            return f"🛡 Обнаружен WAF: {pat.capitalize()}"
    return "🛡 WAF не обнаружен"

def fingerprint_server(server):
    """Определяет тип сервера на основе заголовка Server."""
    server = (server or "N/A").lower()
    for key, name in FINGERPRINTS.items():
        if key in server:
            return f"🧾 Сервер: {name}"
    return "🧾 Сервер: неизвестен"

def run_check(domain_port: str, ping_threshold=50, http_timeout=20.0, port_timeout=2, full_report=True):
    """Выполняет проверку домена, возвращает полный или краткий отчёт."""
    if ":" in domain_port:
        domain, port = domain_port.split(":")
        port = int(port)
    else:
        domain = domain_port
        port = 443

    report = [f"🔍 Проверка {domain}:"]

    # DNS
    ip = resolve_dns(domain)
    report.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")
    if not ip:
        return "\n".join(report)

    # Пинг
    ping_ms = get_ping(ip)
    ping_result = f"🟢 Ping: ~{ping_ms:.1f} ms" if ping_ms else "❌ Ping: ошибка"

    # TLS
    tls = get_tls_info(domain, port)
    tls_results = []
    if tls["tls"]:
        tls_results.append(f"✅ {tls['tls']} поддерживается")
        if tls["cipher"]:
            tls_results.append(f"✅ {tls['cipher']} используется")
        if tls["expires_days"] is not None:
            tls_results.append(f"⏳ TLS сертификат истекает через {tls['expires_days']} дн.")
    else:
        tls_results.append(f"❌ TLS: ошибка соединения ({tls['error'] or 'неизвестно'})")

    # HTTP
    http = get_http_info(domain, timeout=http_timeout)
    http["domain"] = domain
    http_results = [
        "✅ HTTP/2 поддерживается" if http["http2"] else "❌ HTTP/2 не поддерживается",
        "✅ HTTP/3 (h3) поддерживается" if http["http3"] else "❌ HTTP/3 не поддерживается"
    ]
    http_additional = []
    if http["ttfb"]:
        http_additional.append(f"⏱️ TTFB: {http['ttfb']:.2f} сек")
    else:
        http_additional.append(f"⏱️ TTFB: неизвестно ({http['error'] or 'неизвестно'})")
    if http["redirect"]:
        http_additional.append(f"🔁 Redirect: {http['redirect']}")
    else:
        http_additional.append("🔁 Без редиректа")
    http_additional.append(fingerprint_server(http.get("server")))

    # ↓↓↓ ASN и CDN определяются один раз ↓↓↓
    loc, asn = "N/A", "N/A"
    cdn = None
    try:
        loc, asn = get_ip_info(ip)
        cdn = detect_cdn(http, asn)
    except Exception as e:
        checker_logger.warning(f"CDN detection failed for {domain}: {str(e)}")

    waf_result = detect_waf(http.get("server"))
    cdn_result = f"{'🟢 CDN не обнаружен' if not cdn else f'⚠️ CDN обнаружен: {cdn.capitalize()}'}"

    # Оценка пригодности
    suitability_results = []
    reasons = []

    if not http["http2"]:
        reasons.append("HTTP/2 отсутствует")
    if tls["tls"] not in ["TLSv1.3", "TLS 1.3"]:
        reasons.append("TLS 1.3 отсутствует")
    if ping_ms and ping_ms >= ping_threshold:
        reasons.append(f"высокий пинг ({ping_ms:.1f} ms)")
    if cdn:
        reasons.append(f"CDN обнаружен ({cdn.capitalize()})")

    if not reasons:
        suitability_results.append("✅ Пригоден для Reality")
    elif cdn and reasons == [f"CDN обнаружен ({cdn.capitalize()})"]:
        suitability_results.append(f"⚠️ Условно пригоден: CDN обнаружен ({cdn.capitalize()})")
    else:
        suitability_results.append(f"❌ Не пригоден: {', '.join(reasons)}")

    if not full_report:
        # Краткий отчёт
        report.append(ping_result)
        report.append("    🔒 TLS")
        report += tls_results[:1]
        report.append("    🌐 HTTP")
        report += http_results
        report.append(waf_result)
        report.append(cdn_result)
        report.append("    🛰 Оценка пригодности")
        report += suitability_results
    else:
        # Полный отчёт
        report.append("\n🌐 DNS")
        report.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")

        report.append("\n📡 Скан портов")
        report += scan_ports(ip, timeout=port_timeout)

        report.append("\n🌍 География и ASN")
        report.append(f"📍 IP: {loc}")
        report.append(f"🏢 ASN: {asn}")
        report.append(check_spamhaus(ip))
        report.append(ping_result)

        report.append("\n🔒 TLS")
        report += tls_results

        report.append("\n🌐 HTTP")
        report += http_results
        report += http_additional
        report.append(waf_result)
        report.append(cdn_result)

        report.append("\n📄 WHOIS")
        whois_exp = get_domain_whois(domain)
        report.append(f"📆 Срок действия: {whois_exp}" if whois_exp else "❌ WHOIS: ошибка")

        report.append("\n🛰 Оценка пригодности")
        report += suitability_results

    return "\n".join(report)
