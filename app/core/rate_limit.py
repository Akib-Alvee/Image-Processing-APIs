import time
from fastapi import Depends, HTTPException, Response, status

from app.api.deps import CurrentUser
from app.core.config import settings
from app.core.redis import redis_client


class RateLimiter:
    """
    Redis-backed fixed-window rate limiter dependency for FastAPI routes.
    Tracks request counts per authenticated user and action scope (e.g., 'upload' or 'transform').
    """

    def __init__(
        self,
        scope: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> None:
        self.scope = scope
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def __call__(
        self,
        current_user: CurrentUser,
        response: Response,
    ) -> None:
        # Bucket key rotates every `window_seconds` (e.g., every 60s)
        current_window = int(time.time() // self.window_seconds)
        redis_key = f"ratelimit:{self.scope}:{current_user.id}:{current_window}"
        retry_after = self.window_seconds - (int(time.time()) % self.window_seconds)

        try:
            # Atomically increment counter and set expiry on first hit
            async with redis_client.pipeline(transaction=True) as pipe:
                pipe.incr(redis_key)
                pipe.expire(redis_key, self.window_seconds + 5)
                results = await pipe.execute()

            current_count = int(results[0])
        except Exception:
            # Fail-open if Redis is temporarily unreachable so API stays usable
            return

        remaining = max(0, self.max_requests - current_count)
        response.headers["X-RateLimit-Limit"] = str(self.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(retry_after)

        if current_count > self.max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded for '{self.scope}': maximum {self.max_requests} "
                    f"requests per {self.window_seconds} seconds. Try again in {retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )


# Pre-configured rate limiter dependencies using limits from .env
upload_rate_limiter = Depends(
    RateLimiter(
        scope="upload",
        max_requests=settings.RATE_LIMIT_UPLOAD_PER_MINUTE,
        window_seconds=60,
    )
)

transform_rate_limiter = Depends(
    RateLimiter(
        scope="transform",
        max_requests=settings.RATE_LIMIT_TRANSFORM_PER_MINUTE,
        window_seconds=60,
    )
)
