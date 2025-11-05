"""
Модуль проверки доменов для Reality бота.
Проверяет DNS, TLS, HTTP/2/3, CDN, WAF и пригодность для Reality протокола.
"""

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
from logging.handlers import RotatingFileHandler
import os
import geoip2.database
import geoip2.errors
import ipaddress
from typing import Dict, List, Optional, Tuple, Any

# ============================================================================
# КОНСТАНТЫ
# ============================================================================

# Паттерны для определения CDN
CDN_PATTERNS = [
    "cloudflare", "akamai", "fastly", "incapsula", "imperva", "sucuri", "stackpath",
    "cdn77", "edgecast", "keycdn", "azure", "tencent", "alibaba", "aliyun", "bunnycdn",
    "arvan", "g-core", "mail.ru", "mailru", "vk.com", "vk", "limelight", "lumen",
    "level3", "centurylink", "cloudfront", "verizon", "google", "gws", "googlecloud",
    "x-google", "via: 1.1 google"
]

# Fingerprints для определения WAF
WAF_FINGERPRINTS = [
    "cloudflare", "imperva", "sucuri", "incapsula", "akamai", "barracuda"
]

# Fingerprints для определения веб-сервера
SERVER_FINGERPRINTS = {
    "nginx": "NGINX",
    "apache": "Apache",
    "caddy": "Caddy",
    "iis": "Microsoft IIS",
    "litespeed": "LiteSpeed",
    "openresty": "OpenResty",
    "tengine": "Tengine",
    "cloudflare": "Cloudflare"
}

# Приоритетные CDN для детектирования
PRIORITY_CDNS = [
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

# Информация о Regional Internet Registries
RIR_SOURCES = {
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

# Порты по умолчанию для сканирования
DEFAULT_SCAN_PORTS = [80, 443, 8080, 8443]

# Тайм-ауты по умолчанию
DEFAULT_DNS_TIMEOUT = 5
DEFAULT_PING_TIMEOUT = 3
DEFAULT_TLS_TIMEOUT = 10
DEFAULT_HTTP_TIMEOUT = 20.0
DEFAULT_PORT_SCAN_TIMEOUT = 2
DEFAULT_RIR_TIMEOUT = 10

# ============================================================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================================================

log_dir = os.getenv("LOG_DIR", "/app")
log_file = os.path.join(log_dir, "checker.log")
os.makedirs(log_dir, exist_ok=True)

checker_logger = logging.getLogger("checker")
checker_logger.setLevel(logging.WARNING)

if not checker_logger.handlers:
    handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    checker_logger.addHandler(handler)

# ============================================================================
# ЛОКАЛИЗАЦИЯ
# ============================================================================

# Словари для локализации
TRANSLATIONS = {
    'ru': {
        'checking': '🔍 Проверка',
        'dns_ok': '✅ A:',
        'dns_fail': '❌ DNS: не разрешается',
        'ping_ok': '🟢 Ping: ~{ms:.1f} ms',
        'ping_fail': '❌ Ping: ошибка',
        'tls_supported': '✅ {version} поддерживается',
        'tls_cipher': '✅ {cipher} используется',
        'tls_expires': '⏳ TLS сертификат истекает через {days} дн.',
        'tls_error': '❌ TLS: ошибка соединения ({error})',
        'http2_ok': '✅ HTTP/2 поддерживается',
        'http2_fail': '❌ HTTP/2 не поддерживается',
        'http3_ok': '✅ HTTP/3 (h3) поддерживается',
        'http3_fail': '❌ HTTP/3 не поддерживается',
        'ttfb': '⏱️ TTFB: {time:.2f} сек',
        'ttfb_unknown': '⏱️ TTFB: неизвестно ({error})',
        'redirect': '🔁 Redirect: {url}',
        'no_redirect': '🔁 Без редиректа',
        'server_hidden': '🧾 Сервер: скрыт',
        'server': '🧾 Сервер: {name}',
        'waf_detected': '🛡 WAF обнаружен: {name}',
        'waf_not_detected': '🛡 WAF не обнаружен',
        'cdn_detected': '⚠️ CDN обнаружен: {name}',
        'cdn_not_detected': '🟢 CDN не обнаружен',
        'suitable': '✅ Пригоден для Reality',
        'conditionally_suitable': '⚠️ Условно пригоден: CDN обнаружен ({cdn})',
        'not_suitable': '❌ Не пригоден: {reasons}',
        'port_open': 'TCP {port} 🟢 открыт',
        'port_closed': 'TCP {port} 🔴 закрыт',
        'geoip2_not_found': '❌ База данных GeoIP2 не найдена',
        'geoip2_address_not_found': '❌ IP не найден в GeoIP2 базе',
        'geoip2_error': '❌ GeoIP2 ошибка: {error}',
        'rir_disabled': '🔕 RIR запросы отключены в настройках',
        'rir_not_found': '❌ Информация не найдена во всех RIR',
        'rir_unavailable': '❌ RIR недоступен: {error}',
        'rir_error': '❌ RIR ошибка: {error}',
        'spamhaus_found': '⚠️ Найден в Spamhaus',
        'spamhaus_not_found': '✅ Не найден в Spamhaus',
        'spamhaus_unavailable': '❓ Spamhaus недоступен',
        'whois_expires': '📆 Срок действия: {date}',
        'whois_error': '❌ WHOIS: ошибка',
        'section_dns': '🌐 DNS',
        'section_ports': '📡 Скан портов',
        'section_geo': '🌍 География и ASN',
        'section_geoip2': '📊 GeoIP2 данные:',
        'section_rir': '📋 {rir} данные:',
        'section_ipinfo': '🔍 ipinfo.io (дополнительно):',
        'section_tls': '🔒 TLS',
        'section_http': '🌐 HTTP',
        'section_whois': '📄 WHOIS',
        'section_suitability': '🛰 Оценка пригодности',
        'ip_location': '📍 IP: {location}',
        'ip_asn': '🏢 ASN: {asn}',
        'coordinates': '📍 Координаты: {coords}',
        'accuracy': '🎯 Точность: ±{radius} км',
        'network': '🌐 Сеть: {name}',
        'country': '🏳️ Страна: {country}',
        'organization': '🏢 Организация: {org}',
        'status': '📊 Статус: {status}',
        'description': '📝 {desc}',
        'regions': '🌍 Регионы: {regions}',
        'timezone': '🕐 Часовой пояс: {tz}',
    },
    'en': {
        'checking': '🔍 Checking',
        'dns_ok': '✅ A:',
        'dns_fail': '❌ DNS: not resolved',
        'ping_ok': '🟢 Ping: ~{ms:.1f} ms',
        'ping_fail': '❌ Ping: error',
        'tls_supported': '✅ {version} supported',
        'tls_cipher': '✅ {cipher} used',
        'tls_expires': '⏳ TLS certificate expires in {days} days',
        'tls_error': '❌ TLS: connection error ({error})',
        'http2_ok': '✅ HTTP/2 supported',
        'http2_fail': '❌ HTTP/2 not supported',
        'http3_ok': '✅ HTTP/3 (h3) supported',
        'http3_fail': '❌ HTTP/3 not supported',
        'ttfb': '⏱️ TTFB: {time:.2f} sec',
        'ttfb_unknown': '⏱️ TTFB: unknown ({error})',
        'redirect': '🔁 Redirect: {url}',
        'no_redirect': '🔁 No redirect',
        'server_hidden': '🧾 Server: hidden',
        'server': '🧾 Server: {name}',
        'waf_detected': '🛡 WAF detected: {name}',
        'waf_not_detected': '🛡 WAF not detected',
        'cdn_detected': '⚠️ CDN detected: {name}',
        'cdn_not_detected': '🟢 CDN not detected',
        'suitable': '✅ Suitable for Reality',
        'conditionally_suitable': '⚠️ Conditionally suitable: CDN detected ({cdn})',
        'not_suitable': '❌ Not suitable: {reasons}',
        'port_open': 'TCP {port} 🟢 open',
        'port_closed': 'TCP {port} 🔴 closed',
        'geoip2_not_found': '❌ GeoIP2 database not found',
        'geoip2_address_not_found': '❌ IP not found in GeoIP2 database',
        'geoip2_error': '❌ GeoIP2 error: {error}',
        'rir_disabled': '🔕 RIR requests disabled in settings',
        'rir_not_found': '❌ Information not found in all RIRs',
        'rir_unavailable': '❌ RIR unavailable: {error}',
        'rir_error': '❌ RIR error: {error}',
        'spamhaus_found': '⚠️ Found in Spamhaus',
        'spamhaus_not_found': '✅ Not found in Spamhaus',
        'spamhaus_unavailable': '❓ Spamhaus unavailable',
        'whois_expires': '📆 Expires: {date}',
        'whois_error': '❌ WHOIS: error',
        'section_dns': '🌐 DNS',
        'section_ports': '📡 Port Scan',
        'section_geo': '🌍 Geography & ASN',
        'section_geoip2': '📊 GeoIP2 Data:',
        'section_rir': '📋 {rir} Data:',
        'section_ipinfo': '🔍 ipinfo.io (additional):',
        'section_tls': '🔒 TLS',
        'section_http': '🌐 HTTP',
        'section_whois': '📄 WHOIS',
        'section_suitability': '🛰 Suitability Assessment',
        'ip_location': '📍 IP: {location}',
        'ip_asn': '🏢 ASN: {asn}',
        'coordinates': '📍 Coordinates: {coords}',
        'accuracy': '🎯 Accuracy: ±{radius} km',
        'network': '🌐 Network: {name}',
        'country': '🏳️ Country: {country}',
        'organization': '🏢 Organization: {org}',
        'status': '📊 Status: {status}',
        'description': '📝 {desc}',
        'regions': '🌍 Regions: {regions}',
        'timezone': '🕐 Timezone: {tz}',
    }
}


def t(key: str, lang: str = 'ru', **kwargs) -> str:
    """
    Получить переведенную строку.
    
    Args:
        key: Ключ перевода
        lang: Язык ('ru' или 'en')
        **kwargs: Параметры для форматирования
        
    Returns:
        Переведенная и отформатированная строка
    """
    lang = lang if lang in TRANSLATIONS else 'ru'
    text = TRANSLATIONS[lang].get(key, TRANSLATIONS['ru'].get(key, key))
    
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


# ============================================================================
# DNS И СЕТЕВЫЕ ФУНКЦИИ
# ============================================================================

def resolve_dns(domain: str, timeout: int = DEFAULT_DNS_TIMEOUT) -> Optional[str]:
    """
    Разрешает DNS для домена и возвращает IP-адрес.
    
    Args:
        domain: Доменное имя
        timeout: Тайм-аут запроса в секундах
        
    Returns:
        IP-адрес или None в случае ошибки
    """
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, 'A')
        return str(answers[0])
    except dns.resolver.NXDOMAIN:
        # Домен не существует - это нормальная ситуация, не ошибка
        checker_logger.debug(f"Domain {domain} does not exist (NXDOMAIN)")
        return None
    except dns.resolver.NoAnswer:
        # DNS ответ не содержит A-записи - тоже нормальная ситуация
        checker_logger.debug(f"Domain {domain} has no A records")
        return None
    except dns.resolver.Timeout:
        # Тайм-аут DNS запроса
        checker_logger.warning(f"DNS timeout for {domain}")
        return None
    except Exception as e:
        # Только реальные ошибки логируем как ERROR
        checker_logger.error(f"DNS resolution error for {domain}: {str(e)}")
        return None


def get_ping(ip: str, timeout: int = DEFAULT_PING_TIMEOUT) -> Optional[float]:
    """
    Выполняет ping и возвращает время отклика в миллисекундах.
    
    Args:
        ip: IP-адрес
        timeout: Тайм-аут в секундах
        
    Returns:
        Время отклика в мс или None в случае ошибки
    """
    try:
        result = ping3.ping(ip, timeout=timeout)
        return result * 1000 if result else None
    except Exception as e:
        checker_logger.error(f"Ping failed for {ip}: {str(e)}")
        return None


def scan_ports(ip: str, ports: List[int] = None, timeout: int = DEFAULT_PORT_SCAN_TIMEOUT, lang: str = 'ru') -> List[str]:
    """
    Сканирует порты и возвращает их статус.
    
    Args:
        ip: IP-адрес
        ports: Список портов для сканирования
        timeout: Тайм-аут для каждого порта
        lang: Язык результатов
        
    Returns:
        Список строк со статусом портов
    """
    if ports is None:
        ports = DEFAULT_SCAN_PORTS
        
    results = []
    for port in ports:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip, port))
            sock.close()
            
            if result == 0:
                results.append(t('port_open', lang, port=port))
            else:
                results.append(t('port_closed', lang, port=port))
        except Exception:
            results.append(t('port_closed', lang, port=port))
    
    return results


# ============================================================================
# TLS И HTTP ФУНКЦИИ
# ============================================================================

def get_tls_info(domain: str, port: int = 443, timeout: int = DEFAULT_TLS_TIMEOUT) -> Dict[str, Any]:
    """
    Получает информацию о TLS соединении.
    
    Args:
        domain: Доменное имя
        port: Порт (по умолчанию 443)
        timeout: Тайм-аут соединения
        
    Returns:
        Словарь с информацией о TLS: version, cipher, expires_days, error
    """
    info: Dict[str, Any] = {"tls": None, "cipher": None, "expires_days": None, "error": None}
    
    try:
        context = ssl.create_default_context()
        with socket.create_connection((domain, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as s:
                cert = s.getpeercert()
                info["tls"] = s.version()
                
                cipher_info = s.cipher()
                info["cipher"] = cipher_info[0] if cipher_info else None
                
                if cert and "notAfter" in cert:
                    not_after = cert["notAfter"]
                    if isinstance(not_after, str):
                        expire = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                        info["expires_days"] = (expire - datetime.utcnow()).days
    except Exception as e:
        info["error"] = str(e)
    
    return info


def get_http_info(domain: str, timeout: float = DEFAULT_HTTP_TIMEOUT) -> Dict[str, Any]:
    """
    Получает информацию о HTTP/HTTPS соединении.
    
    Args:
        domain: Доменное имя
        timeout: Тайм-аут запроса
        
    Returns:
        Словарь с информацией: http2, http3, ttfb, server, redirect, error, domain
    """
    info: Dict[str, Any] = {
        "http2": False,
        "http3": False,
        "ttfb": None,
        "server": None,
        "redirect": None,
        "error": None,
        "domain": domain
    }
    
    try:
        start = time.time()
        with httpx.Client(timeout=timeout, verify=False, follow_redirects=False, http2=True) as client:
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


# ============================================================================
# GEOIP И RIR ФУНКЦИИ
# ============================================================================

def get_geoip2_info(ip: str, lang: str = 'ru') -> Dict[str, Any] | str:
    """
    Получает информацию из GeoIP2 базы данных.
    
    Args:
        ip: IP-адрес
        lang: Язык результатов
        
    Returns:
        Словарь с данными GeoIP2 или строка с ошибкой
    """
    try:
        db_path = os.getenv("GEOIP2_DB_PATH", "/app/data/GeoLite2-City.mmdb")
        
        if not os.path.exists(db_path):
            return t('geoip2_not_found', lang)
        
        with geoip2.database.Reader(db_path) as reader:
            try:
                response = reader.city(ip)
                
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
                return t('geoip2_address_not_found', lang)
    except Exception as e:
        checker_logger.error(f"GeoIP2 lookup failed for {ip}: {str(e)}")
        return t('geoip2_error', lang, error=str(e))


def get_rir_info(ip: str, timeout: int = DEFAULT_RIR_TIMEOUT, lang: str = 'ru') -> Dict[str, Any] | str:
    """
    Получает информацию об IP из соответствующего RIR (Regional Internet Registry).
    
    Args:
        ip: IP-адрес
        timeout: Тайм-аут запроса
        lang: Язык результатов
        
    Returns:
        Словарь с данными RIR или строка с ошибкой
    """
    try:
        ipaddress.IPv4Address(ip)
        
        # Пробуем каждый RIR по очереди
        for rir_key in ['ripe', 'arin', 'apnic', 'lacnic', 'afrinic']:
            rir = RIR_SOURCES[rir_key]
            
            try:
                if rir_key == 'ripe':
                    # RIPE NCC REST API
                    params = {
                        'query-string': ip,
                        'source': rir['source'],
                        'type-filter': 'inetnum,inet6num,route,route6,aut-num'
                    }
                    
                    response = requests.get(rir['url'], params=params, timeout=timeout)
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
        
        return t('rir_not_found', lang)
        
    except requests.exceptions.RequestException as e:
        checker_logger.error(f"RIR request failed for {ip}: {str(e)}")
        return t('rir_unavailable', lang, error=str(e))
    except Exception as e:
        checker_logger.error(f"RIR lookup failed for {ip}: {str(e)}")
        return t('rir_error', lang, error=str(e))


def get_enhanced_ip_info(ip: str, timeout: int = DEFAULT_RIR_TIMEOUT, lang: str = 'ru') -> Dict[str, Any]:
    """
    Расширенная информация об IP с использованием нескольких источников.
    
    Args:
        ip: IP-адрес
        timeout: Тайм-аут запросов
        lang: Язык результатов
        
    Returns:
        Словарь с данными из разных источников: basic, geoip2, rir, ipinfo
    """
    results: Dict[str, Any] = {}
    
    # Базовая информация из ip-api.com
    try:
        response = requests.get(f"http://ip-api.com/json/{ip}?lang=ru", timeout=timeout)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                location_parts = []
                for key in ["country", "regionName", "city"]:
                    val = data.get(key, "Unknown")
                    if val != "Unknown":
                        location_parts.append(val)
                
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
    
    # GeoIP2 информация
    results['geoip2'] = get_geoip2_info(ip, lang)
    
    # RIR информация
    rir_enabled = os.getenv("RIR_ENABLED", "true").lower() == "true"
    if rir_enabled:
        results['rir'] = get_rir_info(ip, timeout, lang)
    else:
        results['rir'] = t('rir_disabled', lang)
    
    # ipinfo.io для дополнительной информации
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

# ============================================================================
# ФУНКЦИИ ДЕТЕКТИРОВАНИЯ (WAF, CDN, SERVER)
# ============================================================================

def fingerprint_server(server_header: Optional[str], lang: str = 'ru') -> str:
    """
    Определяет веб-сервер по заголовку Server.
    
    Args:
        server_header: Значение заголовка Server
        lang: Язык результата
        
    Returns:
        Строка с информацией о сервере
    """
    if not server_header:
        return t('server_hidden', lang)
    
    server_lower = server_header.lower()
    for pattern, name in SERVER_FINGERPRINTS.items():
        if pattern in server_lower:
            return t('server', lang, name=name)
    
    return t('server', lang, name=server_header.title())


def detect_waf(headers: Optional[str], lang: str = 'ru') -> str:
    """
    Определяет WAF по заголовкам.
    
    Args:
        headers: Заголовки HTTP
        lang: Язык результата
        
    Returns:
        Строка с информацией о WAF
    """
    if not headers:
        return t('waf_not_detected', lang)
    
    headers_lower = headers.lower()
    for waf in WAF_FINGERPRINTS:
        if waf in headers_lower:
            return t('waf_detected', lang, name=waf.capitalize())
    
    return t('waf_not_detected', lang)


def detect_cdn(http_info: Optional[Dict[str, Any]], asn: str, lang: str = 'ru') -> Optional[str]:
    """
    Определяет CDN по HTTP информации и ASN.
    
    Args:
        http_info: Информация о HTTP
        asn: ASN информация
        lang: Язык результата
        
    Returns:
        Название CDN или None
    """
    if not http_info:
        return None
    
    # Проверяем заголовки
    headers_to_check = [
        http_info.get("server", ""),
        str(http_info.get("headers", {})).lower()
    ]
    
    asn_lower = asn.lower() if asn and asn != "N/A" else ""
    
    # Проверяем по заголовкам
    for header in headers_to_check:
        if header:
            header_lower = header.lower()
            for cdn_name, patterns in PRIORITY_CDNS:
                for pat in patterns:
                    if pat in header_lower:
                        return cdn_name
    
    # Проверяем ASN
    if asn_lower:
        for cdn_name, patterns in PRIORITY_CDNS:
            for pat in patterns:
                if pat in asn_lower:
                    return cdn_name
    
    return None


def check_spamhaus(ip: str, lang: str = 'ru') -> str:
    """
    Проверяет IP в базе данных Spamhaus.
    
    Args:
        ip: IP-адрес
        lang: Язык результата
        
    Returns:
        Строка с результатом проверки
    """
    try:
        octets = ip.split('.')
        reversed_ip = '.'.join(reversed(octets))
        query = f"{reversed_ip}.zen.spamhaus.org"
        
        try:
            dns.resolver.resolve(query, 'A')
            return t('spamhaus_found', lang)
        except dns.resolver.NXDOMAIN:
            return t('spamhaus_not_found', lang)
        except:
            return t('spamhaus_unavailable', lang)
    except Exception:
        return t('spamhaus_unavailable', lang)


def get_domain_whois(domain: str, lang: str = 'ru') -> Optional[str]:
    """
    Получает информацию WHOIS для домена.
    
    Args:
        domain: Доменное имя
        lang: Язык результата
        
    Returns:
        Дата истечения домена или None
    """
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


# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ ПРОВЕРКИ
# ============================================================================

def run_check(
    domain_port: str,
    ping_threshold: int = 50,
    http_timeout: float = DEFAULT_HTTP_TIMEOUT,
    port_timeout: int = DEFAULT_PORT_SCAN_TIMEOUT,
    full_report: bool = True,
    lang: str = 'ru'
) -> str:
    """
    Выполняет комплексную проверку домена.
    
    Args:
        domain_port: Домен или домен:порт
        ping_threshold: Порог пинга для оценки пригодности (мс)
        http_timeout: Тайм-аут HTTP запросов
        port_timeout: Тайм-аут сканирования портов
        full_report: Полный или краткий отчёт
        lang: Язык отчёта ('ru' или 'en')
        
    Returns:
        Текстовый отчёт о проверке
    """
    # Парсинг домена и порта
    if ":" in domain_port:
        domain, port_str = domain_port.split(":", 1)
        port = int(port_str)
    else:
        domain = domain_port
        port = 443

    report = [t('checking', lang) + f" {domain}:"]

    # ========================================
    # DNS РЕЗОЛЮЦИЯ
    # ========================================
    ip = resolve_dns(domain)
    if ip:
        report.append(t('dns_ok', lang) + f" {ip}")
    else:
        report.append(t('dns_fail', lang))
        return "\n".join(report)

    # ========================================
    # PING
    # ========================================
    ping_ms = get_ping(ip)
    if ping_ms:
        ping_result = t('ping_ok', lang, ms=ping_ms)
    else:
        ping_result = t('ping_fail', lang)

    # ========================================
    # TLS ИНФОРМАЦИЯ
    # ========================================
    tls = get_tls_info(domain, port)
    tls_results = []
    
    if tls["tls"]:
        tls_results.append(t('tls_supported', lang, version=tls['tls']))
        if tls["cipher"]:
            tls_results.append(t('tls_cipher', lang, cipher=tls['cipher']))
        if tls["expires_days"] is not None:
            tls_results.append(t('tls_expires', lang, days=tls['expires_days']))
    else:
        error_msg = tls["error"] or "неизвестно" if lang == 'ru' else "unknown"
        tls_results.append(t('tls_error', lang, error=error_msg))

    # ========================================
    # HTTP ИНФОРМАЦИЯ
    # ========================================
    http = get_http_info(domain, timeout=http_timeout)
    
    http_results = [
        t('http2_ok', lang) if http["http2"] else t('http2_fail', lang),
        t('http3_ok', lang) if http["http3"] else t('http3_fail', lang)
    ]
    
    http_additional = []
    if http["ttfb"]:
        http_additional.append(t('ttfb', lang, time=http['ttfb']))
    else:
        error_msg = http["error"] or ("неизвестно" if lang == 'ru' else "unknown")
        http_additional.append(t('ttfb_unknown', lang, error=error_msg))
    
    if http["redirect"]:
        http_additional.append(t('redirect', lang, url=http['redirect']))
    else:
        http_additional.append(t('no_redirect', lang))
    
    http_additional.append(fingerprint_server(http.get("server"), lang))

    # ========================================
    # IP ИНФОРМАЦИЯ
    # ========================================
    loc, asn = "N/A", "N/A"
    enhanced_ip_info = None
    cdn = None
    
    try:
        enhanced_ip_info = get_enhanced_ip_info(ip, lang=lang)
        loc = enhanced_ip_info['basic']['location']
        asn = enhanced_ip_info['basic']['asn']
        cdn = detect_cdn(http, asn, lang)
    except Exception as e:
        checker_logger.warning(f"Enhanced IP info failed for {domain}: {str(e)}")

    # WAF и CDN детектирование
    waf_result = detect_waf(http.get("server"), lang)
    
    if cdn:
        cdn_result = t('cdn_detected', lang, name=cdn.capitalize())
    else:
        cdn_result = t('cdn_not_detected', lang)

    # ========================================
    # ОЦЕНКА ПРИГОДНОСТИ ДЛЯ REALITY
    # ========================================
    suitability_results = []
    reasons = []

    # Проверяем критерии пригодности
    if not http["http2"]:
        reasons.append("HTTP/2" if lang == 'en' else "HTTP/2 отсутствует")
    
    if tls["tls"] not in ["TLSv1.3", "TLS 1.3"]:
        reasons.append("TLS 1.3" if lang == 'en' else "TLS 1.3 отсутствует")
    
    if ping_ms and ping_ms >= ping_threshold:
        reasons.append(f"high ping ({ping_ms:.1f} ms)" if lang == 'en' 
                      else f"высокий пинг ({ping_ms:.1f} ms)")
    
    if cdn:
        cdn_name = cdn.capitalize()
        reasons.append(f"CDN detected ({cdn_name})" if lang == 'en' 
                      else f"CDN обнаружен ({cdn_name})")

    # Формируем результат оценки
    if not reasons:
        suitability_results.append(t('suitable', lang))
    elif cdn and len(reasons) == 1 and "CDN" in reasons[0]:
        suitability_results.append(t('conditionally_suitable', lang, cdn=cdn.capitalize()))
    else:
        suitability_results.append(t('not_suitable', lang, reasons=', '.join(reasons)))

    # ========================================
    # ФОРМИРОВАНИЕ ОТЧЁТА
    # ========================================
    
    if not full_report:
        # Краткий отчёт
        report.append(ping_result)
        report.append("🔒 TLS: " + (tls_results[0] if tls_results else t('tls_error', lang, error="N/A")))
        report.append("🌐 HTTP: " + http_results[0])
        report.append(waf_result)
        report.append(cdn_result)
        report.append("🛰 " + suitability_results[0])
    else:
        # Полный отчёт
        report.append("\n" + t('section_dns', lang))
        report.append(t('dns_ok', lang) + f" {ip}")

        report.append("\n" + t('section_ports', lang))
        report.extend(scan_ports(ip, timeout=port_timeout, lang=lang))

        report.append("\n" + t('section_geo', lang))
        report.append(t('ip_location', lang, location=loc))
        report.append(t('ip_asn', lang, asn=asn))
        
        # GeoIP2 данные
        if enhanced_ip_info:
            geoip2_data = enhanced_ip_info.get('geoip2')
            if isinstance(geoip2_data, dict):
                report.append("\n" + t('section_geoip2', lang))
                if geoip2_data.get('coordinates') != 'N/A':
                    report.append(t('coordinates', lang, coords=geoip2_data.get('coordinates')))
                if geoip2_data.get('accuracy_radius'):
                    report.append(t('accuracy', lang, radius=geoip2_data.get('accuracy_radius')))
            elif isinstance(geoip2_data, str):
                report.append(f"📊 GeoIP2: {geoip2_data}")
            
            # RIR данные
            rir_data = enhanced_ip_info.get('rir')
            if isinstance(rir_data, dict):
                rir_name = rir_data.get('rir', 'RIR')
                report.append("\n" + t('section_rir', lang, rir=rir_name))
                
                if rir_data.get('network_name'):
                    report.append(t('network', lang, name=rir_data['network_name']))
                if rir_data.get('country'):
                    report.append(t('country', lang, country=rir_data['country']))
                if rir_data.get('organization_ref'):
                    report.append(t('organization', lang, org=rir_data['organization_ref']))
                if rir_data.get('status'):
                    report.append(t('status', lang, status=rir_data['status']))
                if rir_data.get('description'):
                    descriptions = rir_data['description'][:2]
                    for desc in descriptions:
                        report.append(t('description', lang, desc=desc))
                if rir_data.get('regions'):
                    report.append(t('regions', lang, regions=', '.join(rir_data['regions'])))
            elif isinstance(rir_data, str):
                report.append(f"📋 RIR: {rir_data}")
            
            # ipinfo.io данные
            ipinfo_data = enhanced_ip_info.get('ipinfo')
            if isinstance(ipinfo_data, dict):
                report.append("\n" + t('section_ipinfo', lang))
                if ipinfo_data.get('timezone') != 'N/A':
                    report.append(t('timezone', lang, tz=ipinfo_data['timezone']))
                
                # Проверка Spamhaus
                hostname = ipinfo_data.get('hostname', '')
                if hostname and 'spamhaus' not in hostname.lower():
                    report.append(t('spamhaus_not_found', lang))
                elif 'spamhaus' in hostname.lower():
                    report.append(t('spamhaus_found', lang))
        
        # Альтернативная проверка Spamhaus
        if not enhanced_ip_info or not enhanced_ip_info.get('ipinfo'):
            report.append(check_spamhaus(ip, lang))
        
        report.append(ping_result)

        # TLS секция
        report.append("\n" + t('section_tls', lang))
        report.extend(tls_results)

        # HTTP секция
        report.append("\n" + t('section_http', lang))
        report.extend(http_results)
        report.extend(http_additional)
        report.append(waf_result)
        report.append(cdn_result)

        # WHOIS секция
        report.append("\n" + t('section_whois', lang))
        whois_exp = get_domain_whois(domain, lang)
        if whois_exp:
            report.append(t('whois_expires', lang, date=whois_exp))
        else:
            report.append(t('whois_error', lang))

        # Оценка пригодности
        report.append("\n" + t('section_suitability', lang))
        report.extend(suitability_results)

    return "\n".join(report)
