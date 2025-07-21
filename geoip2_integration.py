#!/usr/bin/env python3
"""
Интеграция автообновляльщика GeoIP2 с ботом.
Запускает планировщик обновлений в фоновом режиме.
"""

import os
import threading
from geoip2_updater import run_scheduler_in_background

def setup_geoip2_auto_updater():
    """
    Настраивает автоматическое обновление GeoIP2 базы данных.
    Должно вызываться при запуске бота.
    """
    
    # Проверяем, нужно ли включать автообновление
    auto_update_enabled = os.getenv("GEOIP2_AUTO_UPDATE", "true").lower() == "true"
    
    if not auto_update_enabled:
        print("🔕 Автообновление GeoIP2 отключено (GEOIP2_AUTO_UPDATE=false)")
        return None
    
    try:
        # Запускаем планировщик в фоновом потоке
        scheduler_thread = run_scheduler_in_background()
        print("✅ Автообновление GeoIP2 запущено")
        return scheduler_thread
    except Exception as e:
        print(f"❌ Ошибка запуска автообновления GeoIP2: {e}")
        return None

def get_geoip2_status():
    """Возвращает статус GeoIP2 базы данных"""
    from geoip2_updater import load_update_info
    
    info = load_update_info()
    if info.get("last_update"):
        return {
            "enabled": True,
            "last_update": info["last_update"][:19],
            "next_update": info.get("next_update", "N/A")[:19],
            "file_path": info.get("current_db_path"),
            "file_size_mb": round(info.get("file_size", 0) / (1024*1024), 1),
            "source": info.get("source_name", "N/A")
        }
    else:
        return {
            "enabled": False,
            "error": "База данных не найдена"
        }

if __name__ == "__main__":
    # Тестирование интеграции
    print("🧪 Тестирование интеграции GeoIP2...")
    
    status = get_geoip2_status()
    print(f"📊 Статус: {status}")
    
    if status["enabled"]:
        print("✅ GeoIP2 готов к использованию")
    else:
        print("⚠️ Требуется настройка GeoIP2")
        
        # Запускаем автообновляльщик для первоначальной загрузки
        print("🔄 Запускаем автообновляльщик...")
        thread = setup_geoip2_auto_updater()
        
        if thread:
            print("✅ Автообновляльщик запущен в фоне")
        else:
            print("❌ Не удалось запустить автообновляльщик")
