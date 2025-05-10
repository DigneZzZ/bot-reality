# Domain Checker Bot

⚡ **Domain Checker Bot** — это Telegram-бот на базе Python и библиотеки Aiogram, предназначенный для проверки доменов на пригодность для прокси и Reality. Бот работает асинхронно, использует Redis для управления очередью задач и кэширования результатов, а также предоставляет краткие и полные отчёты о проверке доменов.

## 🔍 Что проверяет

Бот выполняет комплексную проверку доменов и возвращает отчёт, включающий:

- 🌐 **DNS**: Разрешение A-записи (IPv4).
- 📡 **Скан портов**: Проверка открытых TCP-портов (80, 443, 8443).
- 🌍 **География и ASN**: Геолокация IP, ASN и провайдер.
- 🚫 **Spamhaus**: Проверка IP в чёрных списках Spamhaus.
- 🟢 **Ping**: Задержка до сервера (в миллисекундах).
- 🔒 **TLS**: Версия TLS (например, TLSv1.3), шифр, срок действия сертификата.
- 🌐 **HTTP**: Поддержка HTTP/2 и HTTP/3, TTFB (время до первого байта), редиректы, сервер, наличие WAF и CDN.
- 📄 **WHOIS**: Срок действия домена.
- 🛰 **Оценка пригодности**: Вердикт, пригоден ли домен для Reality (учитывает отсутствие CDN, поддержку HTTP/2, TLSv1.3 и пинг < 50 мс).

### Пример краткого отчёта
```
🔍 Проверка: 35photo.pro:443
✅ A: 185.232.233.233
🟢 Ping: ~25.0 ms
    🔒 TLS
✅ TLSv1.3 поддерживается
    🌐 HTTP
❌ HTTP/2 не поддерживается
❌ HTTP/3 не поддерживается
🟢 WAF не обнаружен
🟢 CDN не обнаружен
    🛰 Оценка пригодности
❌ Не пригоден: HTTP/2 отсутствует
[Полный отчёт]
```

### Пример полного отчёта
```
🔍 Проверка: google.com:443
🌐 DNS
✅ A: 142.250.74.14
📡 Скан портов
🟢 TCP 80 открыт
🟢 TCP 443 открыт
🔴 TCP 8443 закрыт
🌍 География и ASN
📍 IP: SE / Stockholm County / Stockholm
🏢 ASN: AS15169 Google LLC
✅ Не найден в Spamhaus
🟢 Ping: ~7.7 ms
🔒 TLS
✅ TLSv1.3 поддерживается
✅ TLS_AES_256_GCM_SHA384 используется
⏳ TLS сертификат истекает через 65 дн.
🌐 HTTP
✅ HTTP/2 поддерживается
✅ HTTP/3 (h3) поддерживается
⏱️ TTFB: 0.13 сек
🔁 Redirect: https://www.google.com/
🧾 Сервер: Google Web Server
🟢 WAF не обнаружен
⚠️ CDN обнаружен: Google
📄 WHOIS
📆 Срок действия: 2028-09-14T04:00:00
🛰 Оценка пригодности
❌ Не пригоден: CDN обнаружен (Google)
```

## 🚀 Быстрый запуск с Docker

1. Убедитесь, что установлены [Docker](https://docs.docker.com/get-docker/) и [Docker Compose](https://docs.docker.com/compose/install/).

2. Создайте файл `.env` с токеном Telegram-бота, полученным от `@BotFather`:
   ```bash
   echo "BOT_TOKEN=your-telegram-bot-token" > .env
   ```

3. Создайте файл `docker-compose.yml`:
   ```yaml
   services:
     bot:
       container_name: domain-bot
       image: ghcr.io/dignezzz/bot-reality:latest
       command: python bot.py
       environment:
         - BOT_TOKEN=${BOT_TOKEN}
         - REDIS_HOST=redis
         - REDIS_PORT=6379
       depends_on:
         - redis
       restart: unless-stopped
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
         interval: 30s
         timeout: 5s
         retries: 3
       logging:
         driver: json-file
         options:
           max-size: "10m"
           max-file: "3"
           compress: "true"
     worker:
       container_name: domain-worker
       image: ghcr.io/dignezzz/bot-reality:latest
       command: python worker.py
       environment:
         - BOT_TOKEN=${BOT_TOKEN}
         - REDIS_HOST=redis
         - REDIS_PORT=6379
       depends_on:
         - redis
       restart: unless-stopped
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
         interval: 30s
         timeout: 5s
         retries: 3
       deploy:
         replicas: 3
       logging:
         driver: json-file
         options:
           max-size: "10m"
           max-file: "3"
           compress: "true"
     redis:
       container_name: domain-redis
       image: redis:7
       restart: unless-stopped
       healthcheck:
         test: ["CMD", "redis-cli", "ping"]
         interval: 10s
         timeout: 3s
         retries: 5
       logging:
         driver: json-file
         options:
           max-size: "5m"
           max-file: "2"
           compress: "true"
   ```

4. Запустите контейнеры:
   ```bash
   docker compose up -d
   ```

5. Проверьте логи для подтверждения запуска:
   ```bash
   docker compose logs -f
   ```

## 🛠 Локальный запуск без Docker

1. Установите [Python 3.11+](https://www.python.org/downloads/) и [Redis](https://redis.io/docs/install/install-redis/).
2. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/dignezzz/bot-reality.git
   cd bot-reality
   ```
3. Создайте виртуальное окружение и установите зависимости:
   ```bash
   python -m venv venv
   source venv/bin/activate  # На Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Настройте переменные окружения:
   ```bash
   export BOT_TOKEN=your-telegram-bot-token
   export REDIS_HOST=localhost
   export REDIS_PORT=6379
   ```
5. Запустите бота:
   ```bash
   python bot.py
   ```
6. В отдельном терминале запустите воркер:
   ```bash
   python worker.py
   ```

## ⚙️ Переменные окружения

- `BOT_TOKEN`: Токен Telegram-бота от `@BotFather` (обязательно).
- `REDIS_HOST`: Хост Redis (по умолчанию: `redis` для Docker, `localhost` для локального запуска).
- `REDIS_PORT`: Порт Redis (по умолчанию: `6379`).

Пример `.env`:
```
BOT_TOKEN=your-telegram-bot-token
REDIS_HOST=redis
REDIS_PORT=6379
```

## 📦 Архитектура

Проект состоит из трёх сервисов:
- **bot**: Telegram-бот, обрабатывает команды и ставит задачи в очередь Redis.
- **worker**: Три реплики воркеров, выполняют проверки доменов и отправляют результаты (файл `worker.py`).
- **redis**: Хранит очередь задач и кэширует результаты (24 часа для результатов, 7 дней для истории).

### Основные файлы
- `bot.py`: Логика Telegram-бота, обработка команд и сообщений.
- `worker.py`: Обработка задач из очереди, выполнение проверок.
- `checker.py`: Модуль проверки доменов (DNS, TLS, HTTP, WHOIS и т.д.).
- `redis_queue.py`: Управление очередью и кэшем в Redis.
- `Dockerfile`: Конфигурация Docker-образа.
- `docker-compose.yml`: Оркестрация сервисов.

### Зависимости
- `aiogram`: Асинхронный фреймворк для Telegram-ботов.
- `redis`: Клиент для взаимодействия с Redis.
- `httpx`, `h2`: Проверка HTTP/2 и HTTP/3.
- `requests`, `python-whois`: Запросы к внешним API и WHOIS.
- `ping3`, `dnspython`: Пинг и DNS-запросы.
- `aiohttp`: Асинхронные HTTP-запросы.

Полный список в `requirements.txt`.

## 🤖 Использование бота

Найдите бота в Telegram и начните взаимодействие:

### Команды
- `/start`: Приветственное сообщение с инлайн-кнопками.
- `/check <домен>`: Краткий отчёт (например, `/check example.com`).
- `/full <домен>`: Полный отчёт (например, `/full example.com`).
- `/ping`: Проверка работоспособности бота.
- `/history`: Последние 10 проверок пользователя.

### Другие способы
- Отправьте домен напрямую: `example.com` (краткий отчёт).
- Отправьте несколько доменов через запятую или перенос строки:
  ```
  example.com, google.com
  ```
- Нажмите инлайн-кнопку "Полный отчёт" для получения детализированного результата.

### Ограничения
- **Скорость**: 10 проверок за 30 секунд.
- **Дневной лимит**: 100 проверок на пользователя.
- **Штрафы**: Некорректные запросы могут привести к временной блокировке (от 1 минуты до 1 часа).

## 🔧 Настройка и оптимизация

### Redis
Для предотвращения сбоев Redis настройте параметр ядра Linux:
```bash
sudo sysctl vm.overcommit_memory=1
echo "vm.overcommit_memory=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

### Логирование
Логи хранятся в файлах:
- `bot.log`: Логи бота.
- `worker.log`: Логи воркеров.
- `checker.log`: Логи проверок.
- `redis_queue.log`: Логи очереди.

Логи в Docker ограничены 10 МБ (3 файла с сжатием).

### Healthcheck
- Бот и воркеры: Проверка `/health` на порту 8080 (каждые 30 секунд).
- Redis: Проверка `redis-cli ping` (каждые 10 секунд).

## 🛠 CI/CD

Docker-образы автоматически собираются и публикуются в [GitHub Container Registry](https://ghcr.io/dignezzz/bot-reality) через GitHub Actions. Конфигурация в `.github/workflows/docker.yml`.

## 🔒 Безопасность

- Храните `BOT_TOKEN` в `.env` и не публикуйте его.
- Используйте переменные окружения вместо жёстко закодированных значений.
- Регулярно обновляйте зависимости (`pip install -r requirements.txt --upgrade`).

## 👨‍💻 Разработка

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/dignezzz/bot-reality.git
   cd bot-reality
   ```
2. Скопируйте `.env.sample` в `.env` и настройте:
   ```bash
   cp .env.sample .env
   nano .env
   ```
3. Соберите и запустите:
   ```bash
   docker compose up --build -d
   ```

## 📜 Лицензия

Разработано [neonode.cc](https://neonode.cc). Лицензия: MIT. Свяжитесь для обратной связи или предложений!
