import json
from typing import Any

import redis
import redis.asyncio as aioredis

from app.core.config import settings

# 1. Async Redis Client (Used by FastAPI routes for non-blocking caching & rate limiting)
redis_client = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)

# 2. Sync Redis Client (Used by synchronous Celery background workers to update cache)
sync_redis_client = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)


async def cache_get(key: str) -> dict[str, Any] | list[Any] | None:
    """Read and deserialize a JSON value from Redis cache. Returns None on cache miss or Redis error."""
    try:
        raw = await redis_client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


async def cache_set(
    key: str,
    value: dict[str, Any] | list[Any],
    ttl_seconds: int | None = None,
) -> None:
    """Serialize a Python dict/list to JSON and store in Redis with an expiration TTL."""
    ttl = ttl_seconds or settings.CACHE_TTL_SECONDS
    try:
        await redis_client.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


async def cache_delete_pattern(pattern: str) -> None:
    """Delete all Redis keys matching a glob pattern (e.g., invalidating user pagination cache on new upload)."""
    try:
        keys = [key async for key in redis_client.scan_iter(match=pattern)]
        if keys:
            await redis_client.delete(*keys)
    except Exception:
        pass


def sync_cache_set(
    key: str,
    value: dict[str, Any],
    ttl_seconds: int | None = None,
) -> None:
    """Synchronous helper for Celery workers to populate the transformation cache."""
    ttl = ttl_seconds or settings.CACHE_TTL_SECONDS
    try:
        sync_redis_client.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass
