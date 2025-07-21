# Domain Reality Checker Bot

⚡ **Domain Reality Checker Bot** — это усовершенствованный Telegram-бот на базе Python и библиотеки Aiogram для проверки доменов на пригодность для Reality/VLESS прокси. Бот работает асинхронно, использует Redis для управления очередью задач и кэширования результатов, а также предоставляет краткие и полные отчёты о проверке доменов.

## ✨ Новые возможности

### 🚀 Система повторных попыток (Retry Logic)
- **Exponential backoff** с настраиваемым jitter
- **Автоматические повторы** для Redis, Telegram API и проверки доменов
- **Гибкая конфигурация** времени ожидания и количества попыток

### 📊 Батч-обработка с прогресс-барами
- **Автоматический прогресс-бар** для проверки 3+ доменов
- **Батч-обработка** по 3 домена за раз для оптимизации
- **Подробная статистика** выполнения (успешно, из кэша, ошибки)

### 📈 Система аналитики
- **Сбор метрик** использования бота и проверок доменов
- **Детальные отчеты** для администратора (/analytics)
- **Трекинг** популярных доменов и пользовательской активности

### 🔒 Безопасность для групповых чатов
- **Авторизация групп** через переменные окружения
- **Автоматический выход** из неавторизованных групп
- **Гибкое управление** списком разрешенных групп

### 🧵 Поддержка тем в супергруппах
- **Умные ответы** в той же теме, где упомянули бота
- **Контекстная работа** с топиками Telegram
- **Организованное общение** в больших группах

### 🎛️ Групповой режим работы
- **Команды с префиксом** (!check, !full, !help)
- **Упоминания бота** (@botname domain.com)
- **Ответы на сообщения** бота с новыми доменами
- **Настраиваемый префикс** команд

## 🔍 Что проверяет

Бот выполняет комплексную проверку доменов и возвращает отчёт, включающий:

- 🌐 **DNS**: Разрешение A-записи (IPv4)
- 📡 **Скан портов**: Проверка открытых TCP-портов (80, 443, 8443)
- 🌍 **География и ASN**: Геолокация IP, ASN и провайдер
- 🚫 **Spamhaus**: Проверка IP в чёрных списках Spamhaus
- 🟢 **Ping**: Задержка до сервера (в миллисекундах)
- 🔒 **TLS**: Версия TLS (например, TLSv1.3), шифр, срок действия сертификата
- 🌐 **HTTP**: Поддержка HTTP/2 и HTTP/3, TTFB (время до первого байта), редиректы, сервер, наличие WAF и CDN
- 📄 **WHOIS**: Срок действия домена
- 🛰 **Оценка пригодности**: Вердикт, пригоден ли домен для Reality (учитывает отсутствие CDN, поддержку HTTP/2, TLSv1.3 и пинг < 50 мс)

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

## � Команды бота

### Пользовательские команды
- `/start` — Приветствие и основное меню
- `/check <domain>` — Краткая проверка домена
- `/full <domain>` — Полная проверка домена  
- `/mode` — Переключить режим вывода (краткий/полный)
- `/history` — Показать последние 10 проверок

### Групповые команды
- `!check <domain>` — Краткая проверка в группе
- `!full <domain>` — Полная проверка в группе
- `!help` — Справка по командам в группе
- `@botname <domain>` — Упоминание бота для проверки
- Ответ на сообщение бота с новым доменом

### Административные команды
- `/adminhelp` — Список всех админ-команд
- `/reset_queue` — Сбросить очередь проверок
- `/clearcache` — Очистить кэш результатов
- `/analytics` — Показать аналитику использования
- `/groups` — Управление авторизованными группами
- `/groups_add <ID>` — Добавить группу в авторизованные
- `/groups_remove <ID>` — Удалить группу из авторизованных
- `/groups_current` — Показать ID текущей группы

## ⚙️ Переменные окружения

### Основные настройки
```env
BOT_TOKEN=your-telegram-bot-token          # Токен бота от @BotFather
ADMIN_ID=123456789                         # ID администратора
REDIS_HOST=redis                           # Хост Redis
REDIS_PORT=6379                            # Порт Redis
REDIS_PASSWORD=                            # Пароль Redis (опционально)
```

### Настройки групповой работы
```env
GROUP_MODE_ENABLED=true                    # Включить работу в группах
GROUP_COMMAND_PREFIX=!                     # Префикс команд в группах
AUTHORIZED_GROUPS=-1001234567890,-1009876543210  # ID разрешенных групп
AUTO_LEAVE_UNAUTHORIZED=false             # Автоматически покидать неавторизованные группы
```

### Дополнительные настройки
```env
SAVE_APPROVED_DOMAINS=false               # Сохранять список пригодных доменов
```

## 🔒 Настройка безопасности для групп

### 1. Получение ID группы
Добавьте бота в группу и используйте команду:
```
/groups_current
```

### 2. Авторизация группы
Добавьте ID в переменную окружения:
```env
AUTHORIZED_GROUPS=-1001234567890,-1009876543210
```

### 3. Автоматический выход
Для автоматического выхода из неавторизованных групп:
```env
AUTO_LEAVE_UNAUTHORIZED=true
```

## 🧵 Работа с темами в супергруппах

Бот автоматически определяет тему (топик), в которой его упомянули, и отвечает именно в ней:

- 🎯 **Контекстные ответы** — результаты приходят в нужную тему
- 📱 **Организованность** — обсуждения не смешиваются между темами  
- 🔄 **Поддержка всех команд** — работают команды с префиксом, упоминания и ответы

## �🚀 Быстрый запуск с Docker

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

## � Лимиты и ограничения

### Лимиты для пользователей
- **10 проверок в минуту** на пользователя
- **100 проверок в день** на пользователя
- **Автоматическая блокировка** при превышении лимитов

### Система наказаний
- **5+ нарушений** → временная блокировка
- **Прогрессивные тайм-ауты**: 1 мин → 5 мин → 15 мин → 1 час
- **Автоматическое снятие** блокировок по истечении времени

### Производительность
- **Асинхронная обработка** для высокой пропускной способности
- **Redis кэширование** результатов на 1 час
- **Батч-обработка** для множественных проверок
- **Автоматические повторы** при сбоях

## 🏗️ Архитектура

### Компоненты системы
```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Telegram  │───▶│    Bot      │───▶│   Redis     │
│   Updates   │    │  (bot.py)   │    │   Queue     │
└─────────────┘    └─────────────┘    └─────────────┘
                           │                   │
                           ▼                   ▼
                   ┌─────────────┐    ┌─────────────┐
                   │ Analytics   │    │   Worker    │
                   │ Collector   │    │ (worker.py) │
                   └─────────────┘    └─────────────┘
                           │                   │
                           ▼                   ▼
                   ┌─────────────┐    ┌─────────────┐
                   │   Metrics   │    │   Domain    │
                   │  Storage    │    │  Checker    │
                   └─────────────┘    └─────────────┘
```

### Модули
- **`bot.py`** — основная логика бота и обработка команд
- **`worker.py`** — воркер для выполнения проверок доменов
- **`checker.py`** — модуль проверки доменов (DNS, TLS, HTTP, etc.)
- **`redis_queue.py`** — управление очередью задач в Redis
- **`retry_logic.py`** — система повторных попыток с экспоненциальным backoff
- **`progress_tracker.py`** — прогресс-бары и батч-обработка
- **`analytics.py`** — сбор и анализ метрик использования

## 🛠️ Локальная разработка

### Требования
- Python 3.9+
- Redis 6.0+
- Docker & Docker Compose

### Установка для разработки
1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/DigneZzZ/bot-reality.git
   cd bot-reality
   ```

2. Создайте виртуальное окружение:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # или
   venv\Scripts\activate     # Windows
   ```

3. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

4. Настройте переменные окружения в `.env`

5. Запустите Redis:
   ```bash
   docker run -d -p 6379:6379 redis:7
   ```

6. Запустите бота и воркер:
   ```bash
   python bot.py &
   python worker.py
   ```

## �🛠 Локальный запуск без Docker

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
   redis-cli --version
   ```

## 🔧 Настройка и мониторинг

### Проверка работоспособности
```bash
# Статус контейнеров
docker-compose ps

# Логи всех сервисов
docker-compose logs -f

# Логи конкретного сервиса
docker-compose logs -f bot
docker-compose logs -f worker

# Мониторинг Redis
docker exec -it domain-redis redis-cli monitor
```

### Статистика использования
- Команда `/analytics` — детальная аналитика для администратора
- Автоматический сбор метрик по пользователям и доменам
- Отслеживание производительности и ошибок

## 🤝 Вклад в проект

Мы приветствуем вклад в развитие проекта! 

### Как внести вклад:
1. Форкните репозиторий
2. Создайте ветку для новой функции (`git checkout -b feature/AmazingFeature`)
3. Зафиксируйте изменения (`git commit -m 'Add some AmazingFeature'`)
4. Отправьте ветку (`git push origin feature/AmazingFeature`)
5. Откройте Pull Request

### Сообщение об ошибках:
- Используйте GitHub Issues для сообщения об ошибках
- Предоставьте подробное описание проблемы
- Включите логи и шаги для воспроизведения

## 📄 Лицензия

Этот проект распространяется под лицензией MIT. Подробности в файле [LICENSE](LICENSE).

## 🏆 Благодарности

- **[Aiogram](https://github.com/aiogram/aiogram)** — современная асинхронная библиотека для Telegram Bot API
- **[Redis](https://redis.io/)** — быстрое хранилище данных в памяти
- **[Docker](https://docker.com/)** — контейнеризация и оркестрация
- **[OpenAI](https://openai.com/)** — ИИ-ассистент в разработке
- **[OpeNode.xyz](https://openode.xyz/)** — поддержка проекта

## 🚀 Что дальше?

### Планируемые улучшения:
- 🌐 **Веб-интерфейс** для управления ботом
- 📊 **Расширенная аналитика** с графиками
- 🔄 **API для интеграции** с внешними сервисами
- 🎯 **Машинное обучение** для улучшения оценки доменов
- 🔒 **Дополнительные проверки безопасности**

---

<div align="center">

**🌟 Если проект оказался полезным, поставьте звездочку!**

Made with ❤️ by [DigneZzZ](https://github.com/DigneZzZ) and AI

</div>

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
