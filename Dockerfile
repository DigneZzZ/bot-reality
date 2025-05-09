FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN apt-get update && \
    apt-get install -y dnsutils curl openssl whois && \
    pip install -r requirements.txt

CMD ["python", "bot.py"]
