import socket
import ssl
import time
import httpx
import requests
import ping3
import whois
from datetime import datetime
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="checker.log", format="%(asctime)s - %(levelname)s - %(message)s")

CDN_PATTERNS = [
    "cloudflare", "akamai", "fastly", "incapsula", "imperva", "sucuri", "stackpath",
    "cdn77", "edgecast", "keycdn", "azure", "tencent", "alibaba", "aliyun", "bunnycdn",
    "arvan", "g-core", "mail.ru", "mailru", "vk.com", "vk", "limelight", "lumen",
    "level3", "centurylink", "cloudfront", "verizon"
]

WAF_FINGERPRINTS = [
    "cloudflare", "imperva", "sucuri", "incapsula", "akamai", "barracuda"
]

FINGERPRINTS = {
    "nginx": "NGINX",
    "apache": "Apache",
    "caddy": "Caddy",
    "iis": "Microsoft IIS",
}

def resolve_dns(domain):
    try:
        return socket.gethostbyname(domain)
    except Exception as e:
        logging.error(f"DNS resolution failed for {domain}: {str(e)}")
        return None

def get_ping(ip, timeout=1):
    try:
        result = ping3.ping(ip, timeout=timeout, unit="ms")
        if result is not None:
            return float(result)
        return None
    except Exception as e:
        logging.error(f"Ping failed for {ip}: {str(e)}")
        return None

def get_tls_info(domain, port, timeout=5):
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

def get_http_info(domain, timeout=5.0):
    info = {"http2": False, "http3": False, "server": None, "ttfb": None, "redirect": None, "error": None}
    try:
        url = f"https://{domain}"
        with httpx.Client(http2=True, timeout=timeout) as client:
            start = time.time()
            resp = client.get(url, follow_redirects=True)
            duration = time.time() - start
            info["http2"] = resp.http_version == "HTTP/2"
            info["http3"] = any("h3" in svc.lower() for svc in resp.headers.get("alt-svc", "").split(","))
            info["server"] = resp.headers.get("server", "N/A")
            info["ttfb"] = duration
            if resp.history:
                info["redirect"] = str(resp.url)
    except Exception as e:
        info["error"] = str(e)
        logging.error(f"HTTP check failed for {domain}: {str(e)}")
    return info

def get_domain_whois(domain):
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
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=timeout).json()
        loc = f"{r.get('countryCode')} / {r.get('regionName')} / {r.get('city')}"
        asn = r.get("as", "N/A")
        return loc, asn
    except Exception as e:
        logging.error(f"IP info check failed for {ip}: {str(e)}")
        return "N/A", "N/A"

def scan_ports(ip, ports=[80, 443, 8443], timeout=1):
    results = []
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                results.append(f"🟢 TCP {port} открыт")
        except Exception:
            results.append(f"🔴 TCP {port} закрыт")
    return results

def check_spamhaus(ip):
    try:
        rev = ".".join(reversed(ip.split("."))) + ".zen.spamhaus.org"
        result = socket.gethostbyname(rev)
        return f"⚠️ В списке Spamhaus (код: {result})"
    except socket.gaierror:
        return "✅ Не найден в Spamhaus"
    except Exception as e:
        logging.error(f"Spamhaus check failed for {ip}: {str(e)}")
        return "❌ Spamhaus: ошибка"

def detect_cdn(text):
    text = text.lower() if isinstance(text, str) else ''
    for pat in CDN_PATTERNS:
        if pat in text:
            return pat
    return None

def detect_waf(text):
    text = text.lower() if isinstance(text, str) else ''
    for pat in WAF_FINGERPRINTS:
        if pat in text:
            return f"🛡 Обнаружен WAF: {pat.capitalize()}"
    return "🟢 WAF не обнаружен"

def fingerprint_server(text):
    text = text.lower() if isinstance(text, str) else ''
    for key, name in FINGERPRINTS.items():
        if key in text:
            return f"🧾 Сервер: {name}"
    return "🧾 Сервер: неизвестен"

def run_check(domain_port: str, ping_threshold=50, http_timeout=10.0, port_timeout=2):
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

    report.append("\n📄 WHOIS")
    whois_exp = get_domain_whois(domain)
    report.append(f"📆 Срок действия: {whois_exp}" if whois_exp else "❌ WHOIS: ошибка")

    report.append("\n🛰 Оценка пригодности")
    summary = " ".join(str(s) for s in report if isinstance(s, str)).lower()
    if detect_cdn(summary):
        report.append("❌ Не пригоден: CDN обнаружен")
    elif not http["http2"]:
        report.append("❌ Не пригоден: HTTP/2 отсутствует")
    elif tls["tls"] not in ["TLSv1.3", "TLS 1.3"]:
        report.append("❌ Не пригоден: TLS 1.3 отсутствует")
    elif ping_ms and ping_ms >= ping_threshold:
        report.append(f"❌ Не пригоден: высокий пинг ({ping_ms:.1f} ms)")
    else:
        report.append("✅ Пригоден для Reality")

    return "\n".join(report)
