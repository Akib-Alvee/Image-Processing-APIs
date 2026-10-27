from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import auth, images
from app.core.config import settings
from app.core.database import async_engine
from app.core.redis import redis_client
from app.models import Base
from app.services.storage import storage_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    FastAPI lifespan hook:
    - Startup: Ensures database tables and S3/MinIO bucket exist.
    - Shutdown: Cleanly closes Redis and async SQLAlchemy connection pools.
    """
    try:
        async with async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        storage_service.ensure_bucket_exists()
    except Exception:
        # Allows unit tests or offline docs inspection to boot even if Postgres/MinIO isn't running yet
        pass

    yield

    try:
        await redis_client.aclose()
    except Exception:
        pass
    await async_engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=(
        "Cloudinary-inspired Image Processing API featuring JWT Access + Refresh Token Rotation, "
        "S3/MinIO Object Storage, Pillow Image Transformations, Redis Caching & Rate Limiting, "
        "and Hybrid Sync/Async Queue Processing with RabbitMQ & Celery."
    ),
    lifespan=lifespan,
)

# Enable CORS for frontend/client integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Global fallback handler so unexpected errors return structured JSON instead of HTML."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected internal server error occurred.",
            "error": str(exc) if settings.DEBUG else "Internal Server Error",
        },
    )


# Register API Routers
app.include_router(auth.router)
app.include_router(images.router)


@app.get("/health", tags=["System"], summary="Health check endpoint")
async def health_check() -> dict[str, str]:
    """Verify the FastAPI server is up and responding."""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "environment": settings.APP_ENV,
    }
