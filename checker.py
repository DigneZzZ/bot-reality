
import socket
import ssl
import time
import requests
import subprocess
import whois
from datetime import datetime
import idna

def resolve_dns(domain):
    try:
        ip = socket.gethostbyname(domain)
        return ip
    except Exception:
        return None

def get_ping(ip):
    ping_methods = [
        ["ping", "-c", "1", "-W", "1", ip],
        ["ping6", "-c", "1", "-W", "1", ip],
        ["ping", "-n", "-c", "1", ip],
    ]
    for cmd in ping_methods:
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=2).decode()
            if "time=" in output:
                time_part = output.split("time=")[-1].split(" ")[0]
                return f"🟢 Ping: ~{time_part}"
        except Exception:
            continue
    return "❌ Ping: ошибка запроса"

def get_tls_info(domain, port):
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(3)
            s.connect((domain, port))
            cert = s.getpeercert()
            tls_version = s.version()
            cipher = s.cipher()
            expire_str = cert["notAfter"]
            expire_date = datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z")
            days_left = (expire_date - datetime.utcnow()).days
            return [
                f"✅ {tls_version} поддерживается",
                f"✅ {cipher[0]} используется",
                f"⏳ TLS сертификат истекает через {days_left} дн."
            ]
    except Exception:
        return ["❌ TLS: не удалось подключиться"]

def get_http_info(domain):
    try:
        url = f"https://{domain}"
        start = time.time()
        resp = requests.get(url, timeout=5)
        duration = time.time() - start
        lines = []
        if resp.raw.version == 2:
            lines.append("✅ HTTP/2 поддерживается")
        if "alt-svc" in resp.headers and "h3" in resp.headers["alt-svc"]:
            lines.append("✅ HTTP/3 (h3) поддерживается")
        if "server" in resp.headers:
            lines.append(f"🔧 Server: {resp.headers['server']}")
        lines.append(f"⏱️ Время ответа (TTFB): {duration:.2f} сек")
        if resp.is_redirect or resp.history:
            lines.append(f"🔁 Redirect: {resp.url}")
        return lines
    except Exception:
        return ["❌ HTTP: ошибка подключения"]

def get_domain_whois(domain):
    try:
        w = whois.whois(domain)
        exp = w.expiration_date
        if isinstance(exp, list):
            exp = exp[0]
        return f"📆 WHOIS срок действия: {exp.isoformat()}"
    except Exception:
        return "❌ WHOIS: не удалось получить данные"

def get_ip_info(ip):
    try:
        r = requests.get(f"https://ipinfo.io/{ip}/json", timeout=5).json()
        loc = r.get("city", "") + ", " + r.get("region", "") + ", " + r.get("country", "")
        org = r.get("org", "N/A")
        asn = org.split()[0] if " " in org else org
        name = " ".join(org.split()[1:]) if " " in org else "N/A"
        return [f"📍 IPinfo: {loc}", f"🏢 {org}", f"🛰️ WHOIS: {asn} / {name}"]
    except Exception:
        return ["❌ IPinfo: ошибка запроса"]

def run_check(domain_port: str):
    if ":" in domain_port:
        domain, port = domain_port.split(":")
        port = int(port)
    else:
        domain = domain_port
        port = 443

    domain = idna.encode(domain).decode("utf-8")
    result = []

    result.append(f"🔍 Проверка: {domain}:{port}\n")

    ip = resolve_dns(domain)
    result.append("🌐 DNS")
    result.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")

    result.append("\n🌎 IP и ASN")
    if ip:
        result += get_ip_info(ip)
        result.append(get_ping(ip))
    else:
        result.append("❌ IP: отсутствует")

    result.append("\n🔒 TLS")
    result += get_tls_info(domain, port)

    result.append("\n🌐 HTTP")
    result += get_http_info(domain)

    result.append("\n📄 WHOIS домена")
    result.append(get_domain_whois(domain))

    result.append("\n🛰 Оценка пригодности")
    if ip and "cloudflare" in "".join(result).lower():
        result.append("❌ Не пригоден: обнаружен CDN")
    else:
        result.append("✅ Пригоден для Reality")

    return "\n".join(result)
