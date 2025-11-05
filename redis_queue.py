import os
import redis.asyncio as redis
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional
import asyncio

# Настройка логирования
log_file = "/app/redis_queue.log"
# Уменьшаем размер файла логов и количество бэкапов
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=2)
logging.basicConfig(
    level=logging.WARNING,  # Изменено с INFO на WARNING
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
)

# Глобальный пул соединений
redis_pool = None

async def init_redis_pool():
    """Инициализирует глобальный пул соединений Redis"""
    global redis_pool
    try:
        redis_pool = redis.ConnectionPool(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            max_connections=15,
            retry_on_timeout=True
        )
        # Проверяем соединение
        test_conn = redis.Redis(connection_pool=redis_pool)
        await test_conn.ping()
        await test_conn.aclose()
        logging.info("✅ Redis queue pool initialized")
    except Exception as e:
        logging.error(f"❌ Failed to initialize Redis queue pool: {e}")
        raise

async def get_redis():
    """Возвращает Redis клиент из пула с обработкой ошибок"""
    try:
        if redis_pool is None:
            await init_redis_pool()
        return redis.Redis(connection_pool=redis_pool)
    except Exception as e:
        logging.error(f"❌ Failed to get Redis connection: {e}")
        raise

async def close_redis_pool():
    """Безопасно закрывает пул соединений"""
    global redis_pool
    if redis_pool:
        try:
            await redis_pool.disconnect()
            logging.info("✅ Redis queue pool closed")
        except Exception as e:
            logging.error(f"⚠️ Error closing Redis queue pool: {e}")

async def is_domain_in_queue(domain: str, user_id: int) -> bool:
    r = await get_redis()
    try:
        pending_key = f"pending:{domain}:{user_id}"
        return await r.exists(pending_key)
    except Exception as e:
        logging.error(f"❌ Error checking domain in queue: {e}")
        return False
    finally:
        try:
            await r.aclose()
        except Exception as e:
            logging.warning(f"⚠️ Error closing Redis connection: {e}")

async def enqueue(domain: str, user_id: int, short_mode: bool, chat_id: Optional[int] = None, message_id: Optional[int] = None, thread_id: Optional[int] = None, lang: str = 'ru'):
    r = await get_redis()
    try:
        pending_key = f"pending:{domain}:{user_id}"
        if await r.exists(pending_key):
            logging.info(f"Domain {domain} for user {user_id} already in queue, skipping")
            return False
        
        # Создаём расширенный task с контекстом чата и языком
        task_data = {
            'domain': domain,
            'user_id': user_id,
            'short_mode': short_mode,
            'chat_id': chat_id or user_id,  # Если chat_id не указан, используем user_id (ЛС)
            'message_id': message_id,
            'thread_id': thread_id,
            'lang': lang  # Добавляем язык пользователя
        }
        
        # Сериализуем в JSON для хранения в Redis
        import json
        task = json.dumps(task_data)
        await r.lpush("queue:domains", task)
        await r.set(pending_key, "1", ex=300)  # Флаг на 5 минут
        logging.info(f"Enqueued task: {task}")
        return True
    except Exception as e:
        logging.error(f"❌ Failed to enqueue task for {domain}: {e}")
        raise
    finally:
        try:
            await r.aclose()
        except Exception as e:
            logging.warning(f"⚠️ Error closing Redis connection: {e}")
