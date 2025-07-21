#!/bin/bash

# Скрипт для очистки логов бота
# Запускать через cron ежедневно: 0 2 * * * /app/cleanup_logs.sh

LOG_DIR="/app"
MAX_AGE_DAYS=7

echo "$(date): Starting log cleanup..."

# Удаляем старые лог файлы старше 7 дней
find $LOG_DIR -name "*.log*" -type f -mtime +$MAX_AGE_DAYS -delete

# Специально очищаем логи Redis queue если они большие
if [ -f "$LOG_DIR/redis_queue.log" ] && [ $(stat -c%s "$LOG_DIR/redis_queue.log" 2>/dev/null || echo 0) -gt 5242880 ]; then
    echo "Redis queue log is large, truncating..."
    > "$LOG_DIR/redis_queue.log"
fi

# Очищаем Docker логи (требует доступа к Docker socket)
if command -v docker &> /dev/null; then
    echo "Cleaning Docker logs..."
    docker system prune -f --filter "until=168h" # 7 дней
fi

# Очищаем системные логи journal (если есть доступ)
if command -v journalctl &> /dev/null; then
    echo "Cleaning journal logs..."
    journalctl --vacuum-time=7d
fi

# Очищаем временные файлы
find /tmp -name "*.log" -type f -mtime +1 -delete 2>/dev/null || true

echo "$(date): Log cleanup completed."
