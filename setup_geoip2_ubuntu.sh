#!/bin/bash
# Скрипт для настройки GeoIP2 на Ubuntu сервере

echo "🌍 Настройка GeoIP2 для Ubuntu сервера..."

# Создаем директории
sudo mkdir -p /var/lib/geoip
sudo mkdir -p /opt/geoip

# Устанавливаем зависимости
echo "📦 Установка зависимостей..."
sudo apt-get update
sudo apt-get install -y python3-pip curl wget

# Устанавливаем Python пакеты
pip3 install geoip2 maxminddb schedule

# Загружаем базу данных в системную директорию
echo "📥 Загрузка базы данных GeoIP2..."
cd /var/lib/geoip

# Используем автообновляльщик
if [ -f "/path/to/bot-reality/geoip2_updater.py" ]; then
    sudo python3 /path/to/bot-reality/geoip2_updater.py --force
else
    # Альтернативная загрузка
    sudo wget -O GeoLite2-City.mmdb.tmp "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb"
    
    # Проверяем размер файла
    if [ -s GeoLite2-City.mmdb.tmp ]; then
        sudo mv GeoLite2-City.mmdb.tmp GeoLite2-City.mmdb
        echo "✅ База данных загружена в /var/lib/geoip/GeoLite2-City.mmdb"
    else
        echo "❌ Ошибка загрузки базы данных"
        sudo rm -f GeoLite2-City.mmdb.tmp
        exit 1
    fi
fi

# Устанавливаем права доступа
sudo chown -R www-data:www-data /var/lib/geoip
sudo chmod -R 644 /var/lib/geoip/*.mmdb

# Создаем cron задачу для автообновления
echo "⏰ Настройка автообновления..."
CRON_JOB="0 3 * * 0 cd /path/to/bot-reality && python3 geoip2_updater.py --force >> /var/log/geoip2_update.log 2>&1"

# Добавляем в crontab если еще не добавлено
(crontab -l 2>/dev/null | grep -v "geoip2_updater.py"; echo "$CRON_JOB") | crontab -

echo "✅ Настройка завершена!"
echo ""
echo "📋 Для использования добавьте в .env:"
echo "GEOIP2_DB_PATH=/var/lib/geoip/GeoLite2-City.mmdb"
echo "GEOIP2_AUTO_UPDATE=true"
echo "RIPE_NCC_ENABLED=true"
echo ""
echo "🔍 Проверить статус: python3 geoip2_updater.py --status"
