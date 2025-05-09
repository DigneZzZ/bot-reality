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

logging.basicConfig(level=logging.INFO, filename="checker.log", format="%(asctime)s - %(levelname)s - %(message)s")

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
        logging.error(f"DNS resolution failed for {domain}: {str(e)}")
        return None

def get_ping(ip, timeout=1):
    """Проверяет пинг до IP-адреса."""
    try:
        result = ping3.ping(ip, timeout=timeout, unit="ms")
        if result is not None:
            return float(result)
        return None
    except Exception as e:
        logging.error(f"Ping failed for {ip}: {str(e)}")
        return None

def get_tls_info(domain, port, timeout=5):
    """Проверяет TLS: версию, шифр, срок действия сертификата."""
    info = {"tls": None, "cipher": None, "expires_days": None, "error": None}
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(timeout)
            s.connect((domain, port))
            info["tls"] = s.version()
            info["cipher"] = s.cipher()[0]
            cert = s.getpeercert()
            expire = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            info["expires_days"] = (expire - datetime.utcnow()).days
    except Exception as e:
        info["error"] = str(e)
        logging.error(f"TLS check failed for {domain}:{port}: {str(e)}")
    return info

def get_http_info(domain, timeout=20.0):
    """Проверяет HTTP: HTTP/2, HTTP/3, TTFB, редиректы, сервер."""
    info = {"http2": False, "http3": False, "server": None, "ttfb": None, "redirect": None, "error": None, "headers": {}}
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
            logging.info(f"HTTP headers for {domain}: {info['headers']}")
    except ImportError as e:
        info["error"] = "HTTP/2 support requires 'h2' package. Install httpx with `pip install httpx[http2]`."
        logging.error(f"HTTP check failed for {domain}: {str(e)}")
    except Exception as e:
        info["error"] = str(e)
        logging.error(f"HTTP check failed for {domain}: {str(e)}")
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
        logging.error(f"WHOIS check failed for {domain}: {str(e)}")
        return None

def get_ip_info(ip, timeout=5):
    """Получает геолокацию и ASN для IP."""
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=timeout).json()
        loc = f"{r.get('countryCode')} / {r.get('regionName')} / {r.get('city')}"
        asn = r.get("as", "N/A")
        return loc, asn
    except Exception as e:
        logging.error(f"IP info check failed for {ip}: {str(e)}")
        return "N/A", "N/A"

def scan_ports(ip, ports=[80, 443, 8443], timeout=1):
    """Сканирует указанные порты на IP."""
    results = []
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                results.append(f"🟢 TCP {port} открыт")
        except Exception:
            results.append(f"🔴 TCP {port} закрыт")
    return results

def check_spamhaus(ip):
    """Проверяет, находится ли IP в чёрном списке Spamhaus."""
    try:
        rev = ".".join(reversed(ip.split("."))) + ".zen.spamhaus.org"
        resolver = dns.resolver.Resolver()
        answers = resolver.resolve(rev, "A")
        for rdata in answers:
            result = str(rdata)
            # Spamhaus возвращает адреса в диапазоне 127.0.0.2–127.0.0.11
            if result.startswith("127.0.0.") and 2 <= int(result.split(".")[-1]) <= 11:
                logging.info(f"Spamhaus check for {ip}: listed with code {result}")
                return f"⚠️ В списке Spamhaus (код: {result})"
        logging.info(f"Spamhaus check for {ip}: not listed")
        return "✅ Не найден в Spamhaus"
    except dns.resolver.NXDOMAIN:
        logging.info(f"Spamhaus check for {ip}: not listed")
        return "✅ Не найден в Spamhaus"
    except Exception as e:
        logging.error(f"Spamhaus check failed for {ip}: {str(e)}")
        return "❌ Spamhaus: ошибка"

def detect_cdn(http_info, asn):
    """Проверяет наличие CDN на основе заголовков, ASN и других признаков."""
    # Проверка заголовков
    headers_str = " ".join(f"{k}:{v}" for k, v in http_info.get("headers", {}).items()).lower()
    server = http_info.get("server", "").lower()
    text = f"{server} {headers_str}"
    for pat in CDN_PATTERNS:
        if pat in text:
            return pat
    # Проверка ASN (Google: AS15169)
    if asn and re.search(r"\b15169\b", asn):
        return "google"
    return None

def detect_waf(text):
    """Проверяет наличие WAF на основе заголовка Server."""
    text = text.lower() if isinstance(text, str) else ''
    for pat in WAF_FINGERPRINTS:
        if pat in text:
            return f"🛡 Обнаружен WAF: {pat.capitalize()}"
    return "🟢 WAF не обнаружен"

def fingerprint_server(text):
    """Определяет тип сервера на основе заголовка Server."""
    text = text.lower() if isinstance(text, str) else ''
    for key, name in FINGERPRINTS.items():
        if key in text:
            return f"🧾 Сервер: {name}"
    return "🧾 Сервер: неизвестен"

def run_check(domain_port: str, ping_threshold=50, http_timeout=20.0, port_timeout=2):
    """Выполняет полную проверку домена."""
    if ":" in domain_port:
        domain, port = domain_port.split(":")
        port = int(port)
    else:
        domain = domain_port
        port = 443

    report = [f"🔍 Проверка: {domain}:{port}\n"]

    ip = resolve_dns(domain)
    report.append("🌐 DNS")
    report.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")
    if not ip:
        return "\n".join(report)

    report.append("\n📡 Скан портов")
    report += scan_ports(ip, timeout=port_timeout)

    report.append("\n🌍 География и ASN")
    loc, asn = get_ip_info(ip)
    report.append(f"📍 IP: {loc}")
    report.append(f"🏢 ASN: {asn}")
    report.append(check_spamhaus(ip))

    ping_ms = get_ping(ip)
    report.append(f"🟢 Ping: ~{ping_ms:.1f} ms" if ping_ms else "❌ Ping: ошибка")

    report.append("\n🔒 TLS")
    tls = get_tls_info(domain, port)
    if tls["tls"]:
        report.append(f"✅ {tls['tls']} поддерживается")
        report.append(f"✅ {tls['cipher']} используется")
        if tls["expires_days"] is not None:
            report.append(f"⏳ TLS сертификат истекает через {tls['expires_days']} дн.")
    else:
        report.append(f"❌ TLS: ошибка соединения ({tls['error'] or 'неизвестно'})")

    report.append("\n🌐 HTTP")
    http = get_http_info(domain, timeout=http_timeout)
    report.append("✅ HTTP/2 поддерживается" if http["http2"] else "❌ HTTP/2 не поддерживается")
    report.append("✅ HTTP/3 (h3) поддерживается" if http["http3"] else "❌ HTTP/3 не поддерживается")
    report.append(f"⏱️ TTFB: {http['ttfb']:.2f} сек" if http["ttfb"] else f"⏱️ TTFB: неизвестно ({http['error'] or 'неизвестно'})")
    report.append(f"🔁 Redirect: {http['redirect']}" if http["redirect"] else "🔁 Без редиректа")
    report.append(fingerprint_server(http.get("server", "")))
    report.append(detect_waf(http.get("server", "")))
    cdn = detect_cdn(http, asn)
    report.append(f"⚠️ CDN обнаружен: {cdn.capitalize()}" if cdn else "🟢 CDN не обнаружен")

    report.append("\n📄 WHOIS")
    whois_exp = get_domain_whois(domain)
    report.append(f"📆 Срок действия: {whois_exp}" if whois_exp else "❌ WHOIS: ошибка")

    report.append("\n🛰 Оценка пригодности")
    if cdn:
        report.append(f"❌ Не пригоден: CDN обнаружен ({cdn.capitalize()})")
    elif not http["http2"]:
        report.append("❌ Не пригоден: HTTP/2 отсутствует")
    elif tls["tls"] not in ["TLSv1.3", "TLS 1.3"]:
        report.append("❌ Не пригоден: TLS 1.3 отсутствует")
    elif ping_ms and ping_ms >= ping_threshold:
        report.append(f"❌ Не пригоден: высокий пинг ({ping_ms:.1f} ms)")
    else:
        report.append("✅ Пригоден для Reality")

    return "\n".join(report)
