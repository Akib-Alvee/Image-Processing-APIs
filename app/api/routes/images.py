import io
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from PIL import Image as PILImage, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.api.deps import CurrentUser, DBSession
from app.core.config import settings
from app.core.rate_limit import transform_rate_limiter, upload_rate_limiter
from app.core.redis import cache_delete_pattern, cache_get, cache_set
from app.models.image import Image
from app.schemas.image import (
    AsyncJobResponse,
    ImageDetailResponse,
    ImageResponse,
    PaginatedImagesResponse,
    SupportedFormat,
    TransformationResponse,
    TransformRequest,
)
from app.services.image_service import image_transformation_service
from app.services.storage import storage_service

router = APIRouter(prefix="/images", tags=["Images"])

ALLOWED_IMAGE_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": ("jpeg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


@router.post(
    "",
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[upload_rate_limiter],
    summary="Upload a new image",
)
async def upload_image(
    current_user: CurrentUser,
    db: DBSession,
    file: Annotated[UploadFile, File(description="Image file (JPEG, PNG, or WEBP)")],
) -> ImageResponse:
    """
    1. Enforces per-user upload rate limit via Redis.
    2. Reads & validates file size (up to MAX_UPLOAD_SIZE_MB).
    3. Inspects actual binary magic bytes using Pillow to verify it is a valid image and extract dimensions.
    4. Uploads original image bytes to MinIO/S3 object storage.
    5. Saves metadata in PostgreSQL and invalidates the user's pagination cache in Redis.
    """
    raw_bytes = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    if not raw_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if len(raw_bytes) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB} MB",
        )

    # Validate binary image content & extract dimensions using Pillow
    try:
        with PILImage.open(io.BytesIO(raw_bytes)) as img:
            pil_format = (img.format or "").upper()
            width, height = img.size
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or corrupted image file. Supported formats: JPEG, PNG, WEBP.",
        ) from exc

    if pil_format not in ALLOWED_IMAGE_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported image format '{pil_format}'. Supported formats: JPEG, PNG, WEBP.",
        )

    normalized_format, content_type = ALLOWED_IMAGE_FORMATS[pil_format]
    storage_key = storage_service.generate_storage_key(
        user_id=current_user.id,
        extension=normalized_format,
        prefix="originals",
    )

    # Upload to MinIO / S3
    public_url = storage_service.upload_bytes(
        data=raw_bytes,
        storage_key=storage_key,
        content_type=content_type,
    )

    # Persist metadata in PostgreSQL
    db_image = Image(
        user_id=current_user.id,
        original_filename=file.filename or f"upload.{normalized_format}",
        storage_key=storage_key,
        url=public_url,
        format=normalized_format,
        width=width,
        height=height,
        size_bytes=len(raw_bytes),
    )
    db.add(db_image)
    await db.commit()
    await db.refresh(db_image)

    # Invalidate cached paginated lists for this user so the new image appears immediately
    await cache_delete_pattern(f"images:list:{current_user.id}:*")

    return ImageResponse.model_validate(db_image)


@router.get(
    "",
    response_model=PaginatedImagesResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a paginated list of user's uploaded images",
)
async def list_images(
    current_user: CurrentUser,
    db: DBSession,
    page: Annotated[int, Query(ge=1, description="Page number (1-indexed)")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page (1-100)")] = 10,
) -> PaginatedImagesResponse:
    """
    Returns a paginated list of images uploaded by the authenticated user.
    Results are cached in Redis per (user_id, page, limit) and invalidated upon new uploads.
    """
    cache_key = f"images:list:{current_user.id}:p{page}:l{limit}"
    cached = await cache_get(cache_key)
    if cached is not None and isinstance(cached, dict):
        return PaginatedImagesResponse.model_validate(cached)

    # Count total images owned by this user
    count_stmt = (
        select(func.count())
        .select_from(Image)
        .where(Image.user_id == current_user.id)
    )
    total = (await db.execute(count_stmt)).scalar_one()

    # Fetch requested page ordered by newest first
    offset = (page - 1) * limit
    items_stmt = (
        select(Image)
        .where(Image.user_id == current_user.id)
        .order_by(Image.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    images = (await db.execute(items_stmt)).scalars().all()

    pages = max(1, math.ceil(total / limit)) if total > 0 else 0
    response_data = PaginatedImagesResponse(
        items=[ImageResponse.model_validate(img) for img in images],
        total=total,
        page=page,
        limit=limit,
        pages=pages,
    )

    await cache_set(cache_key, response_data.model_dump(mode="json"))
    return response_data


@router.get(
    "/{image_id}",
    response_model=ImageDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve image details or download image in a specific format",
)
async def get_image(
    image_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
    raw: Annotated[
        bool,
        Query(description="Set true to return the raw binary image file instead of JSON metadata"),
    ] = False,
    target_format: Annotated[
        SupportedFormat | None,
        Query(alias="format", description="Convert and stream image in a specific format (jpeg, png, webp)"),
    ] = None,
) -> ImageDetailResponse | Response:
    """
    Retrieve a saved image owned by the authenticated user:
    - By default (`?raw=false`), returns JSON metadata, public MinIO/S3 URL, and all transformed variants.
    - If `?raw=true` or `?format=png|webp|jpeg` is provided, downloads the image from S3 and streams
      the binary image in the requested format.
    """
    cache_key = f"images:detail:{current_user.id}:{image_id}"

    # Fast-path from Redis cache when requesting JSON metadata
    if not raw and target_format is None:
        cached = await cache_get(cache_key)
        if cached is not None and isinstance(cached, dict):
            return ImageDetailResponse.model_validate(cached)

    stmt = (
        select(Image)
        .options(selectinload(Image.transformations))
        .where(Image.id == image_id, Image.user_id == current_user.id)
    )
    db_image = (await db.execute(stmt)).scalar_one_or_none()

    if db_image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Image not found",
        )

    # Stream binary image (with optional on-the-fly format conversion)
    if raw or target_format is not None:
        image_bytes = storage_service.download_bytes(db_image.storage_key)
        out_fmt = (target_format or db_image.format).lower()
        if out_fmt == "jpg":
            out_fmt = "jpeg"

        if out_fmt != db_image.format.lower():
            with PILImage.open(io.BytesIO(image_bytes)) as img:
                if out_fmt == "jpeg" and img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format=out_fmt.upper())
                image_bytes = buf.getvalue()

        media_type = f"image/{out_fmt}"
        ext = "jpg" if out_fmt == "jpeg" else out_fmt
        return Response(
            content=image_bytes,
            media_type=media_type,
            headers={
                "Content-Disposition": f'inline; filename="{db_image.id}.{ext}"'
            },
        )

    detail_response = ImageDetailResponse.model_validate(db_image)
    await cache_set(cache_key, detail_response.model_dump(mode="json"))
    return detail_response


@router.post(
    "/{image_id}/transform",
    response_model=TransformationResponse | AsyncJobResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[transform_rate_limiter],
    summary="Apply transformations to an image (Sync or Async via RabbitMQ/Celery)",
)
async def transform_image(
    image_id: uuid.UUID,
    payload: TransformRequest,
    current_user: CurrentUser,
    db: DBSession,
    response: Response,
    async_mode: Annotated[
        bool,
        Query(
            alias="async",
            description="Set true to queue transformation in RabbitMQ/Celery and receive a job_id (202 Accepted)",
        ),
    ] = False,
) -> TransformationResponse | AsyncJobResponse:
    """
    Apply one or more transformations (resize, crop, rotate, flip, mirror, watermark,
    compress, format, grayscale/sepia filters) to an uploaded image:
    - Checks Redis & PostgreSQL `transformation_hash` first (instant return if already computed).
    - **Sync Mode (`?async=false`, default)**: Processes immediately with Pillow and returns `200 OK` with the transformed URL and metadata.
    - **Async Mode (`?async=true`)**: Publishes job to RabbitMQ for Celery worker processing and returns `202 Accepted` with `job_id`.
    """
    result = await image_transformation_service.transform_image(
        db=db,
        user_id=current_user.id,
        image_id=image_id,
        options=payload.transformations,
        async_mode=async_mode,
    )
    if isinstance(result, AsyncJobResponse):
        response.status_code = status.HTTP_202_ACCEPTED
    return result


@router.get(
    "/{image_id}/jobs/{job_id}",
    response_model=TransformationResponse,
    status_code=status.HTTP_200_OK,
    summary="Poll the status and result of an async transformation job",
)
async def get_transformation_job_status(
    image_id: uuid.UUID,
    job_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> TransformationResponse:
    """Check whether an asynchronous Celery transformation task is PENDING, PROCESSING, COMPLETED, or FAILED."""
    return await image_transformation_service.get_job_status(
        db=db,
        user_id=current_user.id,
        image_id=image_id,
        job_id=job_id,
    )

