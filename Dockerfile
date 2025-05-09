FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN apt-get update && \
    apt-get install -y dnsutils curl openssl whois && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* && \
    pip install --no-cache-dir -r requirements.txt

HEALTHCHECK CMD curl --fail http://localhost:8080/health || exit 1

CMD ["python", "bot.py"]
