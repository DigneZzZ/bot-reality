import os
import redis.asyncio as redis
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

# Настройка логирования
log_file = "/app/redis_queue.log"
# Уменьшаем размер файла логов и количество бэкапов
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
logging.basicConfig(
    level=logging.WARNING,  # Изменено с INFO на WARNING
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
# Убираем инициализационный лог

async def get_redis():
    try:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
        # Убираем debug лог подключения
        return redis_client
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

async def is_domain_in_queue(domain: str, user_id: int) -> bool:
    r = await get_redis()
    try:
        pending_key = f"pending:{domain}:{user_id}"
        return await r.exists(pending_key)
    finally:
        await r.aclose()

async def enqueue(domain: str, user_id: int, short_mode: bool, chat_id: Optional[int] = None, message_id: Optional[int] = None, thread_id: Optional[int] = None):
    r = await get_redis()
    try:
        pending_key = f"pending:{domain}:{user_id}"
        if await r.exists(pending_key):
            logging.info(f"Domain {domain} for user {user_id} already in queue, skipping")
            return False
        
        # Создаём расширенный task с контекстом чата
        task_data = {
            'domain': domain,
            'user_id': user_id,
            'short_mode': short_mode,
            'chat_id': chat_id or user_id,  # Если chat_id не указан, используем user_id (ЛС)
            'message_id': message_id,
            'thread_id': thread_id
        }
        
        # Сериализуем в JSON для хранения в Redis
        import json
        task = json.dumps(task_data)
        await r.lpush("queue:domains", task)
        await r.set(pending_key, "1", ex=300)  # Флаг на 5 минут
        logging.info(f"Enqueued task: {task}")
        return True
    except Exception as e:
        logging.error(f"Failed to enqueue task for {domain}: {str(e)}")
        raise
    finally:
        await r.aclose()
