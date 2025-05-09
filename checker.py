import subprocess
import json
import re

def run(cmd):
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout.strip()

def get_ipinfo(ip):
    try:
        data = run(f"curl -s https://ipinfo.io/{ip}/json")
        parsed = json.loads(data)
        org = parsed.get("org", "N/A")
        city = parsed.get("city", "N/A")
        region = parsed.get("region", "N/A")
        country = parsed.get("country", "N/A")
        return f"📍 IPinfo: {country} / {region} / {city}\n🏢 {org}"
    except:
        return "📍 IPinfo: ошибка получения данных"

def get_whois_asn(ip):
    whois_data = run(f"whois {ip}")
    match = re.search(r"origin\s*:\s*(AS\d+)", whois_data, re.IGNORECASE)
    org = re.search(r"(OrgName|org-name)\s*:\s*(.+)", whois_data, re.IGNORECASE)
    asn = match.group(1) if match else "N/A"
    org_name = org.group(2).strip() if org else "N/A"
    return f"🛰️ WHOIS: {asn} / {org_name}"

def run_check(domain):
    port = "443"
    if ":" in domain:
        domain, port = domain.split(":")

    output = [f"<b>🔍 Проверка: {domain}:{port}</b>\n"]

    ip_v4 = run(f"dig +short A {domain}")
    ip_v6 = run(f"dig +short AAAA {domain}")

    if not ip_v4 and not ip_v6:
        output.append("❌ DNS: не разрешается")
    else:
        if ip_v4:
            output.append(f"✅ A: {ip_v4.splitlines()[0]}")
        if ip_v6:
            output.append(f"✅ AAAA: {ip_v6.splitlines()[0]}")

    ip = ip_v4.splitlines()[0] if ip_v4 else ip_v6.splitlines()[0] if ip_v6 else None
    if ip:
        output.append("")
        output.append(get_ipinfo(ip))
        output.append(get_whois_asn(ip))

    tls_out = run(f"echo | timeout 5 openssl s_client -connect {domain}:{port} -servername {domain} -tls1_3 2>/dev/null")
    if "TLSv1.3" in tls_out:
        output.append("✅ TLS 1.3 поддерживается")
        if "X25519" in tls_out:
            output.append("✅ X25519 используется")
    else:
        output.append("❌ TLS 1.3 не поддерживается")

    curl_out = run(f"curl -sIk --max-time 8 https://{domain}:{port}")
    if curl_out:
        if "HTTP/2" in curl_out:
            output.append("✅ HTTP/2 поддерживается")
        else:
            output.append("❌ HTTP/2 не поддерживается")

        if "alt-svc: h3" in curl_out.lower():
            output.append("✅ HTTP/3 (h3) поддерживается")
        else:
            output.append("❌ HTTP/3 не поддерживается")

        if "location:" in curl_out.lower():
            output.append("ℹ️ Перенаправление включено")
    else:
        output.append("❌ HTTP: нет ответа")

    return "\n".join(output)
