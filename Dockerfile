FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && apt-get install -y curl && apt-get clean

COPY bot.py worker.py redis_queue.py ./

CMD ["python", "bot.py"]
