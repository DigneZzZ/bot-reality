
import socket
import ssl
import time
import httpx
import requests
import subprocess
import whois
from datetime import datetime
import idna

CDN_PATTERNS = [
    "cloudflare", "akamai", "fastly", "incapsula", "imperva", "sucuri", "stackpath",
    "cdn77", "edgecast", "keycdn", "azure", "tencent", "alibaba", "aliyun", "bunnycdn",
    "arvan", "g-core", "mail.ru", "mailru", "vk.com", "vk", "limelight", "lumen",
    "level3", "centurylink", "cloudfront", "verizon"
]

def resolve_dns(domain):
    try:
        ip = socket.gethostbyname(domain)
        return ip
    except Exception:
        return None

def get_ping(ip):
    try:
        output = subprocess.check_output(["ping", "-c", "4", "-W", "1", ip], stderr=subprocess.DEVNULL).decode()
        for line in output.splitlines():
            if "rtt min/avg/max" in line or "round-trip min/avg/max" in line:
                avg = line.split("/")[4]
                return float(avg)
    except Exception:
        return None

def get_tls_info(domain, port):
    info = {"tls": None, "cipher": None, "expires_days": None}
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(5)
            s.connect((domain, port))
            info["tls"] = s.version()
            info["cipher"] = s.cipher()[0]
            cert = s.getpeercert()
            expire = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            info["expires_days"] = (expire - datetime.utcnow()).days
    except Exception:
        pass
    return info

def get_http_info(domain):
    info = {"http2": False, "http3": False, "server": None, "ttfb": None, "redirect": None}
    try:
        url = f"https://{domain}"
        with httpx.Client(http2=True, timeout=5.0) as client:
            start = time.time()
            resp = client.get(url, follow_redirects=True)
            duration = time.time() - start
            info["http2"] = resp.http_version == "HTTP/2"
            info["http3"] = "h3" in resp.headers.get("alt-svc", "")
            info["server"] = resp.headers.get("server", "N/A")
            info["ttfb"] = duration
            if resp.history:
                info["redirect"] = resp.url
    except Exception:
        pass
    return info

def get_domain_whois(domain):
    try:
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        return exp.isoformat()
    except Exception:
        return None

def get_ip_info(ip):
    try:
        r = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5).json()
        city = r.get("city", "")
        region = r.get("region", "")
        country = r.get("country", "")
        loc = f"{country} / {region} / {city}"
        org = r.get("org", "N/A")
        return loc, org
    except Exception:
        return "N/A", "N/A"

def detect_cdn(text):
    text = text.lower()
    for pat in CDN_PATTERNS:
        if pat in text:
            return pat
    return None

def run_check(domain_port: str):
    if ":" in domain_port:
        domain, port = domain_port.split(":")
        port = int(port)
    else:
        domain = domain_port
        port = 443

    domain = idna.encode(domain).decode("utf-8")
    report = []
    report.append(f"🔍 Проверка: {domain}:{port}\n")

    ip = resolve_dns(domain)
    report.append("🌐 DNS")
    if ip:
        report.append(f"✅ A: {ip}")
    else:
        report.append("❌ DNS: не разрешается")
        return "\n".join(report)

    report.append("\n🌎 IP и ASN")
    ip_loc, ip_org = get_ip_info(ip)
    report.append(f"📍 IPinfo: {ip_loc}")
    report.append(f"🏢 {ip_org}")

    ping_ms = get_ping(ip)
    if ping_ms is not None:
        report.append(f"🟢 Ping: ~{ping_ms:.1f} ms")
    else:
        report.append("❌ Ping: ошибка запроса")

    report.append("\n🔒 TLS")
    tls = get_tls_info(domain, port)
    if tls["tls"]:
        report.append(f"✅ {tls['tls']} поддерживается")
        report.append(f"✅ {tls['cipher']} используется")
        if tls["expires_days"] is not None:
            report.append(f"⏳ TLS сертификат истекает через {tls['expires_days']} дн.")
    else:
        report.append("❌ TLS: ошибка соединения")

    report.append("\n🌐 HTTP")
    http = get_http_info(domain)
    report.append("✅ HTTP/2 поддерживается" if http["http2"] else "❌ HTTP/2 не поддерживается")
    report.append("✅ HTTP/3 (h3) поддерживается" if http["http3"] else "❌ HTTP/3 не поддерживается")
    report.append(f"🔧 Server: {http['server']}")
    if http["ttfb"]:
        report.append(f"⏱️ Время ответа (TTFB): {http['ttfb']:.2f} сек")
    if http["redirect"]:
        report.append(f"🔁 Redirect: {http['redirect']}")

    report.append("\n📄 WHOIS домена")
    whois_exp = get_domain_whois(domain)
    if whois_exp:
        report.append(f"📆 WHOIS срок действия: {whois_exp}")
    else:
        report.append("❌ WHOIS: ошибка получения данных")

    report.append("\n🛰 Оценка пригодности")
    summary_text = " ".join(report).lower()
    verdict = []

    if detect_cdn(summary_text):
        verdict.append("❌ Не пригоден: обнаружен CDN")
    elif not http["http2"]:
        verdict.append("❌ Не пригоден: HTTP/2 отсутствует")
    elif tls["tls"] not in ["TLSv1.3", "TLS 1.3"]:
        verdict.append("❌ Не пригоден: TLS 1.3 отсутствует")
    elif ping_ms and ping_ms >= 8:
        verdict.append("❌ Не пригоден: слишком высокий пинг")
    else:
        verdict.append("✅ Пригоден для Reality")

    report.append("\n".join(verdict))
    return "\n".join(report)
