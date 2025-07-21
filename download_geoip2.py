#!/usr/bin/env python3
"""
Скрипт для загрузки бесплатной базы данных GeoLite2 City от MaxMind.
Для использования GeoIP2 функций в боте.
"""

import os
import requests
import tarfile
import tempfile
from pathlib import Path

def download_geolite2_city(target_dir=None):
    """
    Загружает базу данных GeoLite2 City.
    
    Примечание: С декабря 2019 года MaxMind требует регистрации 
    для загрузки GeoLite2 баз данных.
    
    Альтернативы:
    1. Зарегистрироваться на https://dev.maxmind.com/geoip/geolite2-free-geolocation-data
    2. Использовать старые версии из репозиториев
    3. Использовать альтернативные источники
    """
    
    if not target_dir:
        target_dir = os.getenv("LOG_DIR", "/tmp")
    
    target_path = Path(target_dir)
    target_path.mkdir(exist_ok=True)
    
    print("📋 Информация о загрузке GeoLite2 City:")
    print("🔗 MaxMind требует регистрации для загрузки GeoLite2 баз данных")
    print("📝 Зарегистрируйтесь на: https://dev.maxmind.com/geoip/geolite2-free-geolocation-data")
    print("💾 После регистрации загрузите GeoLite2 City в формате .mmdb")
    print(f"📁 Поместите файл в: {target_path / 'GeoLite2-City.mmdb'}")
    print()
    
    # Проверяем альтернативные источники
    alternative_urls = [
        "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb",
        "https://raw.githubusercontent.com/Dreamacro/maxmind-geoip/release/Country.mmdb"
    ]
    
    print("🔄 Попытка загрузки из альтернативных источников...")
    
    for i, url in enumerate(alternative_urls, 1):
        try:
            print(f"📥 Попытка {i}: {url}")
            response = requests.get(url, timeout=30, stream=True)
            
            if response.status_code == 200:
                filename = "GeoLite2-City.mmdb" if i == 1 else f"GeoLite2-Alternative-{i}.mmdb"
                filepath = target_path / filename
                
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                
                # Проверяем размер файла
                file_size = filepath.stat().st_size
                if file_size > 1024 * 1024:  # Больше 1MB
                    print(f"✅ Загружено: {filepath} ({file_size / (1024*1024):.1f} MB)")
                    print(f"🔧 Установите переменную: GEOIP2_DB_PATH={filepath}")
                    return str(filepath)
                else:
                    print(f"⚠️ Файл слишком мал ({file_size} bytes), возможно ошибка")
                    filepath.unlink()
            else:
                print(f"❌ HTTP {response.status_code}")
                
        except Exception as e:
            print(f"❌ Ошибка: {e}")
    
    print()
    print("📋 Инструкции для ручной установки:")
    print("1. Зарегистрируйтесь на https://www.maxmind.com/en/accounts/current/geoip/downloads")
    print("2. Загрузите GeoLite2 City (Binary / gzip)")
    print("3. Распакуйте и поместите .mmdb файл в папку проекта")
    print(f"4. Установите GEOIP2_DB_PATH={target_path / 'GeoLite2-City.mmdb'}")
    
    return None

if __name__ == "__main__":
    import sys
    
    target = sys.argv[1] if len(sys.argv) > 1 else None
    result = download_geolite2_city(target)
    
    if result:
        print(f"\n🎉 База данных готова к использованию: {result}")
    else:
        print("\n❌ Автоматическая загрузка не удалась. Используйте ручную установку.")
