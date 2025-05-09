# Domain Checker Bot

⚡ **Domain Checker Bot** — это Telegram-бот на базе Aiogram, предназначенный для проверки доменов на пригодность для прокси и Reality. Бот работает асинхронно, использует очередь Redis для обработки запросов и кэширует результаты для повышения производительности.

## 🔍 Что проверяет

Бот выполняет комплексную проверку доменов и возвращает подробный отчёт, включающий:

- 🌐 **DNS**: Разрешение A-записи (IPv4).
- 📡 **Скан портов**: Проверка открытых TCP-портов (80, 443, 8443).
- 🌍 **География и ASN**: Геолокация IP, ASN и провайдер.
- 🚫 **Spamhaus**: Проверка IP в чёрных списках Spamhaus.
- 🟢 **Ping**: Задержка до сервера (в миллисекундах).
- 🔒 **TLS**: Версия TLS (например, TLSv1.3), шифр, срок действия сертификата.
- 🌐 **HTTP**: Поддержка HTTP/2 и HTTP/3, TTFB (время до первого байта), редиректы, сервер, наличие WAF и CDN.
- 📄 **WHOIS**: Срок действия домена.
- 🛰 **Оценка пригодности**: Вердикт, пригоден ли домен для Reality (учитывает отсутствие CDN, поддержку HTTP/2, TLSv1.3 и низкий пинг).

## 🚀 Быстрый запуск с Docker

1. Убедитесь, что у вас установлены [Docker](https://docs.docker.com/get-docker/) и [Docker Compose](https://docs.docker.com/compose/install/).

2. Создайте файл `docker-compose.yml` со следующим содержимым:
   ```yaml
   services:
     bot:
       image: ghcr.io/dignezzz/bot-reality:latest
       environment:
         - BOT_TOKEN=${BOT_TOKEN}
         - REDIS_HOST=redis
         - REDIS_PORT=6379
       depends_on:
         - redis
       restart: unless-stopped
       logging:
         options:
           max-size: "10m"
           max-file: "3"
     worker:
       image: ghcr.io/dignezzz/bot-reality:latest
       command: python worker.py
       environment:
         - BOT_TOKEN=${BOT_TOKEN}
         - REDIS_HOST=redis
         - REDIS_PORT=6379
       depends_on:
         - redis
       restart: unless-stopped
       logging:
         options:
           max-size: "10m"
           max-file: "3"
     redis:
       image: redis:7.4.3
       volumes:
         - redis_data:/data
       restart: unless-stopped
       logging:
         options:
           max-size: "10m"
           max-file: "3"
   volumes:
     redis_data:
   ```

3. Создайте файл `.env` и добавьте токен Telegram-бота, полученный от `@BotFather`:
   ```bash
   echo "BOT_TOKEN=your-telegram-bot-token" > .env
   ```

4. Запустите контейнеры:
   ```bash
   docker compose up -d
   ```

5. Проверьте логи для подтверждения запуска:
   ```bash
   docker compose logs -f
   ```

## 🛠 Альтернативный запуск (для разработчиков)

Если вы хотите собрать образ самостоятельно:

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/dignezzz/bot-reality.git
   cd bot-reality
   ```

2. Скопируйте файл окружения:
   ```bash
   cp .env.sample .env
   nano .env
   ```
   Укажите ваш Telegram-токен.

3. Соберите и запустите контейнеры:
   ```bash
   docker compose up --build -d
   ```

## 🛠 Локальный запуск без Docker

1. Убедитесь, что у вас установлены Python 3.11+ и Redis.
2. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv venv
   source venv/bin/activate  # На Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Настройте переменные окружения:
   ```bash
   export BOT_TOKEN=your-telegram-bot-token
   export REDIS_HOST=localhost
   export REDIS_PORT=6379
   ```
4. Запустите бота:
   ```bash
   python bot.py
   ```
5. В отдельном терминале запустите воркер:
   ```bash
   python worker.py
   ```

## ⚙️ Переменные окружения

Файл `.env` должен содержать следующие переменные:

- `BOT_TOKEN`: Токен Telegram-бота, полученный от `@BotFather`.
- `REDIS_HOST`: Хост Redis (по умолчанию: `redis` для Docker, `localhost` для локального запуска).
- `REDIS_PORT`: Порт Redis (по умолчанию: `6379`).

Пример `.env`:
```
BOT_TOKEN=your-telegram-bot-token
REDIS_HOST=redis
REDIS_PORT=6379
```

## 📦 Контейнеры

Проект использует три сервиса:

- **`bot`**: Telegram-бот, обрабатывающий команды пользователей и ставящий задачи в очередь.
- **`worker`**: Обрабатывает очередь, выполняет проверки доменов и отправляет результаты.
- **`redis`**: Хранит очередь задач и кэширует результаты проверок.

## 🤖 Использование бота

Найдите бота в Telegram и отправьте домен для проверки:

- Просто отправьте домен:
  ```
  example.com
  ```
- Используйте команду `/check`:
  ```
  /check example.com
  ```
- Другие команды:
  - `/ping` — Проверяет, что бот работает.
  - `/stats` — Показывает статистику очереди и кэша.

Пример ответа:
```
🔍 Проверка: example.com:443
🌐 DNS
✅ A: 93.184.216.34
...
🌐 HTTP
✅ HTTP/2 поддерживается
🟢 CDN не обнаружен
...
🛰 Оценка пригодности
✅ Пригоден для Reality
```

## ⚠️ Настройка Redis

Чтобы устранить предупреждение Redis о `vm.overcommit_memory`, настройте параметр ядра Linux:

```bash
sudo sysctl vm.overcommit_memory=1
echo "vm.overcommit_memory=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

Это предотвращает потенциальные сбои Redis при нехватке памяти.

## 🛠 CI/CD

Docker-образы автоматически собираются и публикуются в GitHub Container Registry (`ghcr.io/dignezzz/bot-reality:latest`) через GitHub Actions. Конфигурация находится в `.github/workflows/docker.yml`.

## 🔒 Автор

Разработано [neonode.cc](https://neonode.cc). Свяжитесь с нами для обратной связи или предложений!
