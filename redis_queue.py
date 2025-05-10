import redis.asyncio as redis
import os
import logging
from typing import Optional, Tuple

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="redis_queue.log", format="%(asctime)s - %(levelname)s - %(message)s")

async def get_redis():
    try:
        return redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=True,
            retry_on_timeout=True
        )
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

async def enqueue(domain: str, user_id: int, short_mode: bool = False):
    r = await get_redis()
    try:
        queue_key = "queue:domains"
        length = await r.llen(queue_key)
        if length >= 1000:
            logging.error(f"Queue is full (1000 tasks). Cannot enqueue {domain} for user {user_id}")
            return False
        await r.rpush(queue_key, f"{user_id}:{domain}:{short_mode}")
        logging.info(f"Enqueued {domain} for user {user_id} (short_mode={short_mode})")
        return True
    except Exception as e:
        logging.error(f"Failed to enqueue {domain} for user {user_id}: {str(e)}")
        return False
    finally:
        await r.aclose()

async def dequeue() -> Optional[Tuple[int, str, bool]]:
    r = await get_redis()
    try:
        queue_key = "queue:domains"
        task = await r.lpop(queue_key)
        if task:
            user_id, domain, short_mode = task.split(":", 2)
            logging.info(f"Dequeued {domain} for user {user_id} (short_mode={short_mode})")
            return int(user_id), domain, short_mode.lower() == "true"
        return None, None, None
    except Exception as e:
        logging.error(f"Failed to dequeue task: {str(e)}")
        return None, None, None
    finally:
        await r.aclose()
