FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y curl cron && apt-get clean

COPY bot.py worker.py checker.py redis_queue.py progress_tracker.py analytics.py retry_logic.py cleanup_logs.sh ./

# Делаем скрипт исполняемым
RUN chmod +x cleanup_logs.sh

# Настраиваем cron для ежедневной очистки логов в 2:00
RUN echo "0 2 * * * /app/cleanup_logs.sh >> /tmp/cleanup.log 2>&1" | crontab -

# Запускаем cron в фоне и основное приложение
CMD cron && python bot.py
