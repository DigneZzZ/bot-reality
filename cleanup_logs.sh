#!/bin/bash

# Скрипт очистки логов для Docker контейнера
# Запускается ежедневно в 2:00 через cron

LOG_DIR="/app"
MAX_LOG_SIZE="50M"
MAX_LOG_AGE=7  # дней

echo "$(date): Начинаем очистку логов..."

# Удаляем старые лог файлы (старше 7 дней)
find $LOG_DIR -name "*.log*" -type f -mtime +$MAX_LOG_AGE -delete 2>/dev/null

# Очищаем большие лог файлы (больше 50MB)
find $LOG_DIR -name "*.log*" -type f -size +$MAX_LOG_SIZE -exec truncate -s 0 {} \; 2>/dev/null

# Очищаем системные логи Docker
if [ -w /var/log ]; then
    find /var/log -name "*.log" -type f -mtime +3 -delete 2>/dev/null
fi

# Очищаем temporary файлы
find /tmp -name "*.tmp" -type f -mtime +1 -delete 2>/dev/null
find /tmp -name "*.temp" -type f -mtime +1 -delete 2>/dev/null

# Очищаем кэш pip если есть
if [ -d "/root/.cache/pip" ]; then
    rm -rf /root/.cache/pip/* 2>/dev/null
fi

echo "$(date): Очистка логов завершена"

# Показываем статистику использования диска
df -h /app /tmp 2>/dev/null || true
