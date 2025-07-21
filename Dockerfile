FROM python:3.11-slim

WORKDIR /app

# Создаем директорию для данных GeoIP2
RUN mkdir -p /app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y curl cron && apt-get clean

# Копируем все файлы проекта
COPY bot.py worker.py checker.py redis_queue.py progress_tracker.py analytics.py retry_logic.py cleanup_logs.sh ./
COPY geoip2_updater.py geoip2_integration.py ./

# Делаем скрипты исполняемыми
RUN chmod +x cleanup_logs.sh
RUN chmod +x geoip2_updater.py

# Настраиваем cron для ежедневной очистки логов в 2:00
RUN echo "0 2 * * * /app/cleanup_logs.sh >> /tmp/cleanup.log 2>&1" | crontab -

# Настраиваем cron для еженедельного обновления GeoIP2 в воскресенье в 3:00
RUN echo "0 3 * * 0 cd /app && python geoip2_updater.py --force >> /tmp/geoip2_update.log 2>&1" | crontab -

# Загружаем GeoIP2 базу данных при сборке образа
RUN cd /app && python geoip2_updater.py --force || echo "GeoIP2 download failed, will retry at runtime"

# Запускаем cron в фоне и основное приложение
CMD cron && python bot.py
