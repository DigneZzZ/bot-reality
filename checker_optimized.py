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
import geoip2.database
import geoip2.errors
import json
import ipaddress

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
    "litespeed": "LiteSpeed",
    "openresty": "OpenResty",
    "tengine": "Tengine",
    "cloudflare": "Cloudflare"
}

def resolve_dns(domain):
    """Разрешает DNS для домена и возвращает IP."""
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5
        answers = resolver.resolve(domain, 'A')
        return str(answers[0])
    except Exception as e:
        checker_logger.error(f"DNS resolution failed for {domain}: {str(e)}")
        return None

def get_ping(ip):
    """Выполняет ping и возвращает время отклика в миллисекундах."""
    try:
        result = ping3.ping(ip, timeout=3)
        return result * 1000 if result else None
    except Exception as e:
        checker_logger.error(f"Ping failed for {ip}: {str(e)}")
        return None

def get_tls_info(domain, port=443):
    """Получает информацию о TLS."""
    info = {"tls": None, "cipher": None, "expires_days": None, "error": None}
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as s:
                cert = s.getpeercert()
                info["tls"] = s.version()
                info["cipher"] = s.cipher()[0] if s.cipher() else None
                if cert and "notAfter" in cert:
                    expire = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
                    info["expires_days"] = (expire - datetime.utcnow()).days
    except Exception as e:
        info["error"] = str(e)
    return info

def get_http_info(domain, timeout=20.0):
    """Получает информацию о HTTP."""
    info = {"http2": False, "http3": False, "ttfb": None, "server": None, "redirect": None, "error": None}
    
    try:
        start = time.time()
        with httpx.Client(timeout=timeout, verify=False, follow_redirects=False) as client:
            response = client.get(f"https://{domain}")
            info["ttfb"] = time.time() - start
            info["http2"] = response.http_version == "HTTP/2"
            info["server"] = response.headers.get("Server", "").lower()
            
            if 300 <= response.status_code < 400:
                info["redirect"] = response.headers.get("Location")
                
        # Проверка HTTP/3
        try:
            alt_svc = response.headers.get("alt-svc", "").lower()
            info["http3"] = "h3" in alt_svc or "h3-" in alt_svc
        except:
            info["http3"] = False
            
    except Exception as e:
        info["error"] = str(e)
    
    return info

def scan_ports(ip, ports=[80, 443, 8080, 8443], timeout=2):
    """Сканирует порты и возвращает их статус."""
    results = []
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            status = "🟢 открыт" if result == 0 else "🔴 закрыт"
            results.append(f"TCP {port} {status}")
        except Exception:
            results.append(f"TCP {port} 🔴 закрыт")
    return results

def get_geoip2_info(ip):
    """Получает информацию из GeoIP2 базы данных."""
    try:
        db_path = os.getenv("GEOIP2_DB_PATH", "/app/data/GeoLite2-City.mmdb")
        
        if not os.path.exists(db_path):
            return "❌ База данных GeoIP2 не найдена"
        
        with geoip2.database.Reader(db_path) as reader:
            try:
                response = reader.city(ip)
                
                # Собираем только важную информацию
                result = {
                    'country': response.country.name,
                    'country_code': response.country.iso_code,
                    'region': response.subdivisions.most_specific.name if response.subdivisions else 'N/A',
                    'city': response.city.name if response.city.name else 'N/A',
                    'coordinates': f"{response.location.latitude}, {response.location.longitude}" if response.location.latitude else 'N/A',
                    'accuracy_radius': response.location.accuracy_radius if response.location.accuracy_radius else None
                }
                
                return result
                
            except geoip2.errors.AddressNotFoundError:
                return "❌ IP не найден в GeoIP2 базе"
    except Exception as e:
        checker_logger.error(f"GeoIP2 lookup failed for {ip}: {str(e)}")
        return f"❌ GeoIP2 ошибка: {str(e)}"

def get_rir_info(ip, timeout=10):
    """Получает информацию об IP из соответствующего RIR (Regional Internet Registry)."""
    try:
        ip_obj = ipaddress.IPv4Address(ip)
        
        # Определяем RIR по IP диапазону
        rir_sources = {
            'ripe': {
                'name': 'RIPE NCC',
                'url': 'https://rest.db.ripe.net/search.json',
                'source': 'ripe',
                'emoji': '🇪🇺',
                'regions': ['Europe', 'Middle East', 'Central Asia']
            },
            'arin': {
                'name': 'ARIN',
                'url': 'https://whois.arin.net/rest/ip/{ip}.json',
                'source': 'arin', 
                'emoji': '🇺🇸',
                'regions': ['North America']
            },
            'apnic': {
                'name': 'APNIC',
                'url': 'https://wq.apnic.net/apnic-bin/whois.pl',
                'source': 'apnic',
                'emoji': '🌏',
                'regions': ['Asia Pacific']
            },
            'lacnic': {
                'name': 'LACNIC', 
                'url': 'https://rdap.lacnic.net/rdap/ip/{ip}',
                'source': 'lacnic',
                'emoji': '🌎',
                'regions': ['Latin America', 'Caribbean']
            },
            'afrinic': {
                'name': 'AFRINIC',
                'url': 'https://rdap.afrinic.net/rdap/ip/{ip}',
                'source': 'afrinic',
                'emoji': '🌍',
                'regions': ['Africa']
            }
        }
        
        # Сначала пробуем RIPE (работает лучше всего)
        for rir_key in ['ripe', 'arin', 'apnic', 'lacnic', 'afrinic']:
            rir = rir_sources[rir_key]
            try:
                if rir_key == 'ripe':
                    # RIPE NCC REST API
                    url = rir['url']
                    params = {
                        'query-string': ip,
                        'source': rir['source'],
                        'type-filter': 'inetnum,inet6num,route,route6,aut-num'
                    }
                    
                    response = requests.get(url, params=params, timeout=timeout)
                    data = response.json()
                    
                    if 'objects' not in data or not data['objects']['object']:
                        continue
                    
                    info = {
                        'rir': f"{rir['emoji']} {rir['name']}",
                        'regions': rir['regions']
                    }
                    
                    for obj in data['objects']['object']:
                        obj_type = obj.get('type', '')
                        attributes = obj.get('attributes', {}).get('attribute', [])
                        
                        if obj_type in ['inetnum', 'inet6num']:
                            for attr in attributes:
                                attr_name = attr.get('name', '')
                                attr_value = attr.get('value', '')
                                
                                if attr_name == 'netname':
                                    info['network_name'] = attr_value
                                elif attr_name == 'country':
                                    info['country'] = attr_value
                                elif attr_name == 'org':
                                    info['organization_ref'] = attr_value
                                elif attr_name == 'status':
                                    info['status'] = attr_value
                                elif attr_name == 'descr':
                                    if 'description' not in info:
                                        info['description'] = []
                                    info['description'].append(attr_value)
                    
                    if len(info) > 2:  # Если есть данные кроме rir и regions
                        return info
                    else:
                        continue
                
                elif rir_key == 'arin':
                    # ARIN WHOIS REST API (базовая поддержка)
                    url = rir['url'].format(ip=ip)
                    response = requests.get(url, timeout=timeout)
                    if response.status_code == 200:
                        return {
                            'rir': f"{rir['emoji']} {rir['name']}",
                            'regions': rir['regions'],
                            'network_name': 'ARIN Network',
                            'status': 'ARIN Registry'
                        }
                
                # Для остальных RIR - базовая информация
                else:
                    return {
                        'rir': f"{rir['emoji']} {rir['name']}",
                        'regions': rir['regions'],
                        'network_name': f'{rir["name"]} Network',
                        'status': f'{rir["name"]} Registry'
                    }
                        
            except Exception as rir_error:
                checker_logger.debug(f"{rir['name']} lookup failed for {ip}: {str(rir_error)}")
                continue
        
        return "❌ Информация не найдена во всех RIR"
        
    except requests.exceptions.RequestException as e:
        checker_logger.error(f"RIR request failed for {ip}: {str(e)}")
        return f"❌ RIR недоступен: {str(e)}"
    except Exception as e:
        checker_logger.error(f"RIR lookup failed for {ip}: {str(e)}")
        return f"❌ RIR ошибка: {str(e)}"

def get_enhanced_ip_info(ip, timeout=10):
    """Расширенная информация об IP с использованием нескольких источников без дублирования."""
    results = {}
    
    # Базовая информация из ip-api.com (быстро и надежно)
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}?lang=ru", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                location_parts = []
                if data.get("country", "Unknown") != "Unknown":
                    location_parts.append(data["country"])
                if data.get("regionName", "Unknown") != "Unknown":
                    location_parts.append(data["regionName"])  
                if data.get("city", "Unknown") != "Unknown":
                    location_parts.append(data["city"])
                
                results['basic'] = {
                    'location': " / ".join(location_parts) if location_parts else "N/A",
                    'asn': data.get("as", "N/A"),
                    'country_code': data.get("countryCode", "N/A"),
                    'isp': data.get("isp", "N/A")
                }
            else:
                results['basic'] = {'location': 'N/A', 'asn': 'N/A', 'country_code': 'N/A', 'isp': 'N/A'}
        else:
            results['basic'] = {'location': 'N/A', 'asn': 'N/A', 'country_code': 'N/A', 'isp': 'N/A'}
    except Exception as e:
        checker_logger.warning(f"Failed to fetch ip-api.com for {ip}: {str(e)}")
        results['basic'] = {'location': 'N/A', 'asn': 'N/A', 'country_code': 'N/A', 'isp': 'N/A'}
    
    # GeoIP2 информация (только координаты и точность)
    geoip2_info = get_geoip2_info(ip)
    results['geoip2'] = geoip2_info
    
    # RIR информация (только если включено)
    rir_enabled = os.getenv("RIR_ENABLED", "true").lower() == "true"
    if rir_enabled:
        rir_info = get_rir_info(ip, timeout)
        results['rir'] = rir_info
    else:
        results['rir'] = "🔕 RIR запросы отключены в настройках"
    
    # ipinfo.io для дополнительной валидации (только уникальные данные)
    try:
        response = requests.get(f"https://ipinfo.io/{ip}/json", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            results['ipinfo'] = {
                'timezone': data.get('timezone', 'N/A'),
                'org': data.get('org', 'N/A'),
                'hostname': data.get('hostname', 'N/A')
            }
        else:
            results['ipinfo'] = {'timezone': 'N/A', 'org': 'N/A', 'hostname': 'N/A'}
    except Exception as e:
        checker_logger.warning(f"Failed to fetch ipinfo.org for {ip}: {str(e)}")
        results['ipinfo'] = {'timezone': 'N/A', 'org': 'N/A', 'hostname': 'N/A'}
    
    return results

# Остальные функции остаются без изменений...
def fingerprint_server(server_header):
    """Определяет веб-сервер по заголовку Server."""
    if not server_header:
        return "🧾 Сервер: скрыт"
    
    server_lower = server_header.lower()
    for pattern, name in FINGERPRINTS.items():
        if pattern in server_lower:
            return f"🧾 Сервер: {name}"
    return f"🧾 Сервер: {server_header.title()}"

def detect_waf(headers):
    """Определяет WAF по заголовкам."""
    if not headers:
        return "🛡 WAF не обнаружен"
    
    headers_lower = headers.lower()
    for waf in WAF_FINGERPRINTS:
        if waf in headers_lower:
            return f"🛡 WAF обнаружен: {waf.capitalize()}"
    return "🛡 WAF не обнаружен"

def detect_cdn(http_info, asn):
    """Определяет CDN."""
    if not http_info:
        return None
    
    # Проверяем заголовки
    headers_to_check = [
        http_info.get("server", ""),
        str(http_info.get("headers", {})).lower()
    ]
    
    # Проверяем ASN
    asn_lower = asn.lower() if asn and asn != "N/A" else ""
    
    # Приоритетные CDN (более популярные проверяем первыми)
    priority_cdns = [
        ("cloudflare", ["cloudflare", "cf-ray"]),
        ("akamai", ["akamai", "edgekey"]),
        ("fastly", ["fastly"]),
        ("aws", ["amazon", "aws", "cloudfront"]),
        ("google", ["google", "gws", "googleusercontent"]),
        ("azure", ["azure", "microsoft"]),
        ("incapsula", ["incapsula", "imperva"]),
        ("sucuri", ["sucuri"]),
        ("stackpath", ["stackpath", "netdna"]),
        ("mailru", ["mail.ru", "mailru"]),
        ("yandex", ["yandex"])
    ]
    
    # Проверяем по заголовкам
    for header in headers_to_check:
        if header:
            header_lower = header.lower()
            for cdn_name, patterns in priority_cdns:
                for pat in patterns:
                    if pat in header_lower:
                        return cdn_name
    
    # Проверяем ASN
    if asn_lower:
        for cdn_name, patterns in priority_cdns:
            for pat in patterns:
                if pat in asn_lower:
                    return cdn_name
    
    return None

def check_spamhaus(ip):
    """Проверяет IP в базе данных Spamhaus."""
    try:
        # Простая проверка через DNS
        octets = ip.split('.')
        reversed_ip = '.'.join(reversed(octets))
        query = f"{reversed_ip}.zen.spamhaus.org"
        
        try:
            dns.resolver.resolve(query, 'A')
            return "⚠️ Найден в Spamhaus"
        except dns.resolver.NXDOMAIN:
            return "✅ Не найден в Spamhaus"
        except:
            return "❓ Spamhaus недоступен"
    except Exception:
        return "❓ Spamhaus недоступен"

def get_domain_whois(domain):
    """Получает информацию WHOIS для домена."""
    try:
        w = whois.whois(domain)
        if w.expiration_date:
            exp_date = w.expiration_date
            if isinstance(exp_date, list):
                exp_date = exp_date[0]
            return exp_date.strftime("%Y-%m-%d")
        return None
    except Exception as e:
        checker_logger.error(f"WHOIS lookup failed for {domain}: {str(e)}")
        return None

def run_check(domain_port: str, ping_threshold=50, http_timeout=20.0, port_timeout=2, full_report=True):
    """Выполняет проверку домена, возвращает оптимизированный отчёт без дублирования."""
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

    # ↓↓↓ Получаем информацию об IP один раз ↓↓↓
    loc, asn = "N/A", "N/A"
    enhanced_ip_info = None
    cdn = None
    try:
        # Используем расширенную функцию для получения IP информации
        enhanced_ip_info = get_enhanced_ip_info(ip)
        # Берем базовую информацию для совместимости
        loc = enhanced_ip_info['basic']['location']
        asn = enhanced_ip_info['basic']['asn']
        cdn = detect_cdn(http, asn)
    except Exception as e:
        checker_logger.warning(f"Enhanced IP info failed for {domain}: {str(e)}")

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
        report.append("🔒 TLS: " + (tls_results[0] if tls_results else "❌ TLS недоступен"))
        report.append("🌐 HTTP: " + http_results[0])
        report.append(waf_result)
        report.append(cdn_result)
        report.append("🛰 " + suitability_results[0])
    else:
        # Полный отчёт без дублирования
        report.append("\n🌐 DNS")
        report.append(f"✅ A: {ip}" if ip else "❌ DNS: не разрешается")

        report.append("\n📡 Скан портов")
        report += scan_ports(ip, timeout=port_timeout)

        report.append("\n🌍 География и ASN")
        report.append(f"📍 IP: {loc}")
        report.append(f"🏢 ASN: {asn}")
        
        # Добавляем расширенную информацию без дублирования
        if enhanced_ip_info:
            # GeoIP2 информация - только координаты и точность
            geoip2_data = enhanced_ip_info.get('geoip2')
            if isinstance(geoip2_data, dict):
                report.append("\n📊 GeoIP2 данные:")
                if geoip2_data.get('coordinates') != 'N/A':
                    report.append(f"📍 Координаты: {geoip2_data.get('coordinates')}")
                if geoip2_data.get('accuracy_radius'):
                    report.append(f"🎯 Точность: ±{geoip2_data.get('accuracy_radius')} км")
            elif isinstance(geoip2_data, str):
                report.append(f"📊 GeoIP2: {geoip2_data}")
            
            # RIR информация (универсальная для всех RIR)
            rir_data = enhanced_ip_info.get('rir')
            if isinstance(rir_data, dict):
                report.append(f"\n📋 {rir_data.get('rir', 'RIR')} данные:")
                if rir_data.get('network_name'):
                    report.append(f"🌐 Сеть: {rir_data['network_name']}")
                if rir_data.get('country'):
                    report.append(f"🏳️ Страна: {rir_data['country']}")
                if rir_data.get('organization_ref'):
                    report.append(f"🏢 Организация: {rir_data['organization_ref']}")
                if rir_data.get('status'):
                    report.append(f"📊 Статус: {rir_data['status']}")
                if rir_data.get('description'):
                    descriptions = rir_data['description'][:2]  # Показываем только первые 2
                    for desc in descriptions:
                        report.append(f"📝 {desc}")
                if rir_data.get('regions'):
                    report.append(f"🌍 Регионы: {', '.join(rir_data['regions'])}")
            elif isinstance(rir_data, str):
                report.append(f"📋 RIR: {rir_data}")
            
            # ipinfo.io для дополнительной информации (только уникальные данные)
            ipinfo_data = enhanced_ip_info.get('ipinfo')
            if isinstance(ipinfo_data, dict):
                report.append("\n🔍 ipinfo.io (дополнительно):")
                # Показываем только timezone, остальное уже есть выше
                if ipinfo_data.get('timezone') != 'N/A':
                    report.append(f"🕐 Часовой пояс: {ipinfo_data['timezone']}")
                # Проверка спамхауса
                if ipinfo_data.get('hostname') and 'spamhaus' not in ipinfo_data.get('hostname', '').lower():
                    report.append("✅ Не найден в Spamhaus")
                elif 'spamhaus' in ipinfo_data.get('hostname', '').lower():
                    report.append("⚠️ Найден в Spamhaus")
        
        # Альтернативная проверка спамхауса если ipinfo не сработал
        if not enhanced_ip_info or not enhanced_ip_info.get('ipinfo'):
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
