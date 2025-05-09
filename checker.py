
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
        r = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json()
        loc = f"{r.get('countryCode')} / {r.get('regionName')} / {r.get('city')}"
        asn = r.get("as", "N/A")
        return loc, asn
    except Exception:
        return "N/A", "N/A"

def scan_ports(ip):
    ports = [80, 443, 8443]
    results = []
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=1):
                results.append(f"🟢 TCP {port} открыт")
        except:
            results.append(f"🔴 TCP {port} закрыт")
    return results

def check_spamhaus(ip):
    try:
        rev = ".".join(reversed(ip.split("."))) + ".zen.spamhaus.org"
        socket.gethostbyname(rev)
        return "⚠️ В списке Spamhaus"
    except socket.gaierror:
        return "✅ Не найден в Spamhaus"

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

def run_check(domain_port: str):
    if ":" in domain_port:
        domain, port = domain_port.split(":")
        port = int(port)
    else:
        domain = domain_port
        port = 443

    domain = idna.encode(domain).decode("utf-8")
    report = [f"🔍 Проверка: {domain}:{port}\n"]

    ip = resolve_dns(domain)
    report.append("🌐 DNS")
    report.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")
    if not ip:
        return "\n".join(report)

    report.append("\n📡 Скан портов")
    report += scan_ports(ip)

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
        report.append("❌ TLS: ошибка соединения")

    report.append("\n🌐 HTTP")
    http = get_http_info(domain)
    report.append("✅ HTTP/2 поддерживается" if http["http2"] else "❌ HTTP/2 не поддерживается")
    report.append("✅ HTTP/3 (h3) поддерживается" if http["http3"] else "❌ HTTP/3 не поддерживается")
    report.append(f"⏱️ TTFB: {http['ttfb']:.2f} сек" if http["ttfb"] else "⏱️ TTFB: неизвестно")
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
    elif ping_ms and ping_ms >= 8:
        report.append("❌ Не пригоден: высокий пинг")
    else:
        report.append("✅ Пригоден для Reality")

    return "\n".join(report)
# HTTP CHECK START
try:
    async with httpx.AsyncClient(http2=True, timeout=10.0, follow_redirects=False) as client:
        url = f"https://{domain}:{port}"
        response = await client.get(url)

        result["http2"] = response.http_version == "HTTP/2"
        result["http3"] = "alt-svc" in response.headers and "h3" in response.headers.get("alt-svc", "").lower()
        result["server"] = response.headers.get("server", "—")
        result["http_status"] = response.status_code
        result["redirect"] = 300 <= response.status_code < 400
        result["ttfb"] = response.elapsed.total_seconds()

except Exception as e:
    result["http_error"] = str(e)
# HTTP CHECK END
