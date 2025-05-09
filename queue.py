import asyncio
import os
import aioredis

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
QUEUE_NAME = "domain_check_queue"

async def enqueue(domain, user_id):
    redis = await aioredis.create_redis_pool((REDIS_HOST, REDIS_PORT))
    await redis.rpush(QUEUE_NAME, f"{user_id}|{domain}")
    redis.close()
    await redis.wait_closed()

async def dequeue():
    redis = await aioredis.create_redis_pool((REDIS_HOST, REDIS_PORT))
    item = await redis.lpop(QUEUE_NAME)
    redis.close()
    await redis.wait_closed()
    if item:
        user_id, domain = item.decode().split("|", 1)
        return int(user_id), domain
    return None, None
