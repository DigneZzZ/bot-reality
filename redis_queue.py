import os
import redis.asyncio as redis
import logging
from logging.handlers import RotatingFileHandler

# Настройка логирования
log_file = "/app/redis_queue.log"
handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[handler, logging.StreamHandler()]
)
logging.info("Redis queue logging initialized")

async def get_redis():
    try:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
        logging.debug("Connected to Redis from redis_queue")
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

async def enqueue(domain: str, user_id: int, short_mode: bool):
    r = await get_redis()
    try:
        pending_key = f"pending:{domain}:{user_id}"
        if await r.exists(pending_key):
            logging.info(f"Domain {domain} for user {user_id} already in queue, skipping")
            return False
        task = f"{domain}:{user_id}:{short_mode}"
        await r.lpush("queue:domains", task)
        await r.set(pending_key, "1", ex=300)  # Флаг на 5 минут
        logging.info(f"Enqueued task: {task}")
        return True
    except Exception as e:
        logging.error(f"Failed to enqueue task for {domain}: {str(e)}")
        raise
    finally:
        await r.aclose()
