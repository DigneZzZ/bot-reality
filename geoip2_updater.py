#!/usr/bin/env python3
"""
Автоматическое обновление базы данных GeoIP2.
Запускается раз в неделю для обновления базы данных.
"""

import os
import sys
import json
import time
import requests
import schedule
import threading
from datetime import datetime, timedelta
from pathlib import Path

# Добавляем текущую директорию в путь для импорта
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Файл для хранения информации о последнем обновлении
UPDATE_INFO_FILE = "geoip2_update_info.json"

def load_update_info():
    """Загружает информацию о последнем обновлении"""
    try:
        if os.path.exists(UPDATE_INFO_FILE):
            with open(UPDATE_INFO_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка загрузки информации об обновлениях: {e}")
    
    return {
        "last_update": None,
        "next_update": None,
        "download_count": 0,
        "current_db_path": None,
        "file_size": 0
    }

def save_update_info(info):
    """Сохраняет информацию о последнем обновлении"""
    try:
        with open(UPDATE_INFO_FILE, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения информации об обновлениях: {e}")

def download_geoip2_database(force_update=False):
    """
    Загружает базу данных GeoIP2 с проверкой необходимости обновления
    """
    info = load_update_info()
    
    # Проверяем, нужно ли обновление
    if not force_update and info.get("last_update"):
        last_update = datetime.fromisoformat(info["last_update"])
        if datetime.now() - last_update < timedelta(days=7):
            print(f"📊 База данных актуальна. Последнее обновление: {last_update.strftime('%Y-%m-%d %H:%M:%S')}")
            return info.get("current_db_path")
    
    target_dir = os.getenv("LOG_DIR", "/app/data")  # Используем /app/data для Docker, /tmp для локального запуска
    target_path = Path(target_dir)
    target_path.mkdir(exist_ok=True)
    
    print(f"🔄 Обновление базы данных GeoIP2... (попытка #{info['download_count'] + 1})")
    print(f"📁 Целевая директория: {target_path}")
    
    # Альтернативные источники (обновленный список)
    alternative_urls = [
        {
            "url": "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb",
            "name": "P3TERX Mirror",
            "filename": "GeoLite2-City.mmdb"
        },
        {
            "url": "https://raw.githubusercontent.com/Loyalsoldier/geoip/release/Country.mmdb",
            "name": "Loyalsoldier Country",
            "filename": "GeoLite2-Country.mmdb"
        },
        {
            "url": "https://github.com/Dreamacro/maxmind-geoip/raw/release/Country.mmdb",
            "name": "Dreamacro Country",
            "filename": "GeoLite2-Country-Alt.mmdb"
        }
    ]
    
    for i, source in enumerate(alternative_urls, 1):
        try:
            print(f"📥 Попытка {i}/{len(alternative_urls)}: {source['name']}")
            print(f"   URL: {source['url']}")
            
            # Скачиваем с прогресс-баром
            response = requests.get(source['url'], timeout=60, stream=True)
            
            if response.status_code == 200:
                filepath = target_path / source['filename']
                temp_filepath = target_path / f"{source['filename']}.tmp"
                
                # Получаем размер файла
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                
                print(f"   📦 Размер файла: {total_size / (1024*1024):.1f} MB")
                
                with open(temp_filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            
                            # Простой прогресс-бар
                            if total_size > 0:
                                progress = (downloaded / total_size) * 100
                                print(f"\r   ⏳ Загружено: {progress:.1f}% ({downloaded / (1024*1024):.1f} MB)", end='')
                
                print()  # Новая строка после прогресс-бара
                
                # Проверяем размер загруженного файла
                file_size = temp_filepath.stat().st_size
                if file_size > 1024 * 1024:  # Больше 1MB
                    # Перемещаем временный файл на место основного
                    if filepath.exists():
                        filepath.unlink()
                    temp_filepath.rename(filepath)
                    
                    print(f"✅ Успешно загружено: {filepath} ({file_size / (1024*1024):.1f} MB)")
                    
                    # Обновляем информацию
                    now = datetime.now()
                    info.update({
                        "last_update": now.isoformat(),
                        "next_update": (now + timedelta(days=7)).isoformat(),
                        "download_count": info.get("download_count", 0) + 1,
                        "current_db_path": str(filepath),
                        "file_size": file_size,
                        "source_name": source['name'],
                        "source_url": source['url']
                    })
                    save_update_info(info)
                    
                    # Устанавливаем переменную окружения
                    os.environ["GEOIP2_DB_PATH"] = str(filepath)
                    
                    print(f"🔧 Переменная GEOIP2_DB_PATH установлена: {filepath}")
                    print(f"📅 Следующее обновление: {info['next_update'][:19]}")
                    
                    return str(filepath)
                else:
                    print(f"⚠️ Файл слишком мал ({file_size} bytes), возможно ошибка")
                    temp_filepath.unlink()
            else:
                print(f"❌ HTTP {response.status_code}")
                
        except Exception as e:
            print(f"❌ Ошибка при загрузке из {source['name']}: {e}")
    
    print("\n❌ Все источники недоступны")
    return None

def check_and_update_database():
    """Проверяет и обновляет базу данных если необходимо"""
    print(f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - Проверка обновлений GeoIP2...")
    
    try:
        result = download_geoip2_database()
        if result:
            print("✅ База данных GeoIP2 актуальна")
            
            # Проверяем, что база данных действительно работает
            try:
                import geoip2.database
                with geoip2.database.Reader(result) as reader:
                    test_response = reader.city('8.8.8.8')
                    print(f"🧪 Тест базы данных: {test_response.country.name} / {test_response.city.name}")
            except Exception as e:
                print(f"⚠️ Ошибка тестирования базы данных: {e}")
        else:
            print("❌ Не удалось обновить базу данных")
    except Exception as e:
        print(f"❌ Ошибка при проверке обновлений: {e}")

def start_scheduler():
    """Запускает планировщик обновлений"""
    print("🚀 Запуск планировщика обновлений GeoIP2...")
    
    # Планируем обновление каждое воскресенье в 03:00
    schedule.every().sunday.at("03:00").do(check_and_update_database)
    
    # Также проверяем при запуске
    print("🔄 Выполняем первоначальную проверку...")
    check_and_update_database()
    
    print("📅 Планировщик настроен: обновление каждое воскресенье в 03:00")
    
    while True:
        schedule.run_pending()
        time.sleep(3600)  # Проверяем каждый час

def run_scheduler_in_background():
    """Запускает планировщик в фоновом потоке"""
    scheduler_thread = threading.Thread(target=start_scheduler, daemon=True)
    scheduler_thread.start()
    print("🔄 Планировщик GeoIP2 запущен в фоновом режиме")
    return scheduler_thread

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Управление базой данных GeoIP2")
    parser.add_argument("--force", action="store_true", help="Принудительное обновление")
    parser.add_argument("--daemon", action="store_true", help="Запуск в режиме демона")
    parser.add_argument("--status", action="store_true", help="Показать статус")
    
    args = parser.parse_args()
    
    if args.status:
        info = load_update_info()
        if info.get("last_update"):
            print(f"📊 Статус базы данных GeoIP2:")
            print(f"   Последнее обновление: {info['last_update'][:19]}")
            print(f"   Следующее обновление: {info.get('next_update', 'N/A')[:19]}")
            print(f"   Количество загрузок: {info.get('download_count', 0)}")
            print(f"   Текущий файл: {info.get('current_db_path', 'N/A')}")
            print(f"   Размер файла: {info.get('file_size', 0) / (1024*1024):.1f} MB")
            print(f"   Источник: {info.get('source_name', 'N/A')}")
        else:
            print("📊 База данных GeoIP2 не найдена")
    elif args.daemon:
        start_scheduler()
    else:
        download_geoip2_database(force_update=args.force)
