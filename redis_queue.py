import os
import redis.asyncio as redis
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO, filename="redis_queue.log", format="%(asctime)s - %(levelname)s - %(message)s")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

async def get_redis():
    try:
        return redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            retry_on_timeout=True
        )
    except Exception as e:
        logging.error(f"Failed to connect to Redis: {str(e)}")
        raise

QUEUE_NAME = "domain_check_queue"

async def enqueue(domain: str, user_id: int):
    r = await get_redis()
    try:
        await r.rpush(QUEUE_NAME, f"{user_id}|{domain}")
        logging.info(f"Enqueued {domain} for user {user_id}")
    except Exception as e:
        logging.error(f"Failed to enqueue {domain}: {str(e)}")
        raise
    finally:
        await r.close()

async def dequeue():
    r = await get_redis()
    try:
        item = await r.blpop(QUEUE_NAME, timeout=5)
        if item:
            _, value = item
            user_id, domain = value.split("|", 1)
            logging.info(f"Dequeued {domain} for user {user_id}")
            return int(user_id), domain
        return None, None
    except Exception as e:
        logging.error(f"Failed to dequeue: {str(e)}")
        return None, None
    finally:
        await r.close()
