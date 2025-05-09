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

async def enqueue(domain: str, user_id: int, short_mode: bool = False):
    r = await get_redis()
    try:
        queue_length = await r.llen(QUEUE_NAME)
        if queue_length >= 1000:
            raise Exception("Очередь переполнена, попробуйте позже")
        await r.rpush(QUEUE_NAME, f"{user_id}|{domain}|{short_mode}")
        logging.info(f"Enqueued {domain} for user {user_id} (short_mode={short_mode})")
    except Exception as e:
        logging.error(f"Failed to enqueue {domain}: {str(e)}")
        raise
    finally:
        await r.aclose()

async def dequeue():
    r = await get_redis()
    try:
        item = await r.blpop(QUEUE_NAME, timeout=5)
        if item:
            _, value = item
            parts = value.split("|")
            user_id = parts[0]
            domain = parts[1]
            short_mode = len(parts) > 2 and parts[2] == "True"
            logging.info(f"Dequeued {domain} for user {user_id} (short_mode={short_mode})")
            return int(user_id), domain, short_mode
        return None, None, False
    except Exception as e:
        logging.error(f"Failed to dequeue: {str(e)}")
        return None, None, False
    finally:
        await r.aclose()
