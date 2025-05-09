import os
import redis.asyncio as redis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

r = redis.Redis(
    host=REDIS_HOST,
    port=REDIS_PORT,
    decode_responses=True
)

QUEUE_NAME = "domain_check_queue"

async def enqueue(domain: str, user_id: int):
    await r.rpush(QUEUE_NAME, f"{user_id}|{domain}")

async def dequeue():
    item = await r.blpop(QUEUE_NAME)
    if item:
        _, value = item
        user_id, domain = value.split("|", 1)
        return int(user_id), domain
    return None, None
