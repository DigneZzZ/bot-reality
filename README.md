# Domain Checker Bot

⚡ Telegram-бот на базе Aiogram для проверки доменов на пригодность для прокси и Reality. Работает через очередь Redis и кэширует ответы.

## 🔍 Что проверяет:

- 🌐 DNS (A, AAAA)
- 🌎 Геолокация IP, ASN
- ⚡ Ping (внешний API)
- 🔒 TLS (1.3, X25519, срок действия)
- 🌐 HTTP (редиректы, TTFB, HTTP/2/3)
- 📄 WHOIS домена (срок действия)
- 🛰 CDN (Cloudflare и другие)
- ✅ Финальный вердикт пригодности

## 🚀 Быстрый запуск

```bash
git clone https://github.com/your-user/domain-checker-bot.git
cd domain-checker-bot
cp .env.example .env
nano .env  # вставьте токен вашего Telegram-бота
docker compose up --build -d
```

## ⚙️ Переменные окружения

- `BOT_TOKEN` — токен Telegram-бота

## 📦 Контейнеры

- `bot` — Telegram-бот
- `worker` — обрабатывает очередь
- `redis` — очередь + кэш

## 🧪 Проверка

Напишите боту любой домен:  
```
example.com
```

И получите результат с анализом пригодности.

## 🤖 Автоматическая сборка через GitHub Actions

Проект поддерживает CI/CD через `Docker build` в `.github/workflows/docker.yml`

---

🔒 Автор: [neonode.cc](https://neonode.cc)
