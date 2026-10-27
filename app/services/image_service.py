import asyncio
import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import cache_delete_pattern, cache_get, cache_set
from app.models.image import Image, ImageTransformation
from app.schemas.image import (
    AsyncJobResponse,
    TransformationOptions,
    TransformationResponse,
)
from app.services.processor import compute_transformation_hash, image_processor
from app.services.storage import storage_service
from app.worker.tasks import process_image_transformation_task


class ImageTransformationService:
    """
    Orchestrates the Hybrid Sync/Async Image Transformation Pipeline:
    1. Deduplicates repeat transformations via SHA-256 `transformation_hash` (Redis + PostgreSQL).
    2. Sync Mode (`async_mode=False`): Runs Pillow in a worker thread, uploads to MinIO/S3, and returns 200 OK.
    3. Async Mode (`async_mode=True`): Saves a PENDING job record, publishes task to RabbitMQ via Celery,
       and returns 202 Accepted with a polling URL.
    """

    async def transform_image(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        image_id: uuid.UUID,
        options: TransformationOptions,
        async_mode: bool = False,
    ) -> TransformationResponse | AsyncJobResponse:
        # 1. Verify parent image exists and belongs to the authenticated user
        img_stmt = select(Image).where(Image.id == image_id, Image.user_id == user_id)
        parent_image = (await db.execute(img_stmt)).scalar_one_or_none()
        if parent_image is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image not found",
            )

        # 2. Compute deterministic SHA-256 hash of (image_id + canonical transformation JSON)
        transform_hash = compute_transformation_hash(image_id=image_id, options=options)
        redis_cache_key = f"transform:hash:{transform_hash}"

        # 3. Check Redis Cache first (Instant sub-millisecond Cache Hit)
        cached = await cache_get(redis_cache_key)
        if cached is not None and isinstance(cached, dict) and cached.get("status") == "COMPLETED":
            return TransformationResponse.model_validate(cached)

        # 4. Check PostgreSQL for an existing record with the same transformation_hash
        existing_stmt = select(ImageTransformation).where(
            ImageTransformation.transformation_hash == transform_hash
        )
        existing_record = (await db.execute(existing_stmt)).scalar_one_or_none()

        if existing_record is not None:
            if existing_record.status == "COMPLETED":
                response_obj = TransformationResponse.model_validate(existing_record)
                await cache_set(redis_cache_key, response_obj.model_dump(mode="json"))
                return response_obj

            if async_mode and existing_record.status in ("PENDING", "PROCESSING"):
                return AsyncJobResponse(
                    job_id=existing_record.id,
                    celery_task_id=existing_record.celery_task_id,
                    parent_image_id=parent_image.id,
                    status=existing_record.status,
                    status_url=f"/images/{parent_image.id}/jobs/{existing_record.id}",
                    message="Transformation job is already queued or processing in RabbitMQ/Celery.",
                )

        # 5. Branch A: Asynchronous Queue Mode (?async=true -> RabbitMQ + Celery Worker)
        if async_mode:
            if existing_record is None:
                existing_record = ImageTransformation(
                    parent_image_id=parent_image.id,
                    transformation_hash=transform_hash,
                    parameters=options.model_dump(exclude_none=True, mode="json"),
                    status="PENDING",
                )
                db.add(existing_record)
                await db.commit()
                await db.refresh(existing_record)
            else:
                existing_record.status = "PENDING"
                existing_record.error_message = None
                await db.commit()

            # Publish message to RabbitMQ broker via Celery
            celery_task = process_image_transformation_task.delay(str(existing_record.id))
            existing_record.celery_task_id = celery_task.id
            await db.commit()

            return AsyncJobResponse(
                job_id=existing_record.id,
                celery_task_id=celery_task.id,
                parent_image_id=parent_image.id,
                status="PENDING",
                status_url=f"/images/{parent_image.id}/jobs/{existing_record.id}",
                message="Transformation job queued in RabbitMQ for background processing.",
            )

        # 6. Branch B: Synchronous Mode (?async=false, default -> Immediate Pillow Execution)
        source_bytes = await asyncio.to_thread(
            storage_service.download_bytes,
            parent_image.storage_key,
        )

        processed = await asyncio.to_thread(
            image_processor.process_image,
            source_bytes,
            parent_image.format,
            options,
        )

        storage_key = storage_service.generate_storage_key(
            user_id=user_id,
            extension=processed.format,
            prefix="transformed",
        )
        public_url = await asyncio.to_thread(
            storage_service.upload_bytes,
            processed.data,
            storage_key,
            processed.content_type,
        )

        if existing_record is None:
            transform_record = ImageTransformation(
                parent_image_id=parent_image.id,
                transformation_hash=transform_hash,
                parameters=options.model_dump(exclude_none=True, mode="json"),
                storage_key=storage_key,
                url=public_url,
                format=processed.format,
                width=processed.width,
                height=processed.height,
                size_bytes=processed.size_bytes,
                status="COMPLETED",
            )
            db.add(transform_record)
        else:
            transform_record = existing_record
            transform_record.storage_key = storage_key
            transform_record.url = public_url
            transform_record.format = processed.format
            transform_record.width = processed.width
            transform_record.height = processed.height
            transform_record.size_bytes = processed.size_bytes
            transform_record.status = "COMPLETED"
            transform_record.error_message = None

        await db.commit()
        await db.refresh(transform_record)

        response_obj = TransformationResponse.model_validate(transform_record)
        await cache_set(redis_cache_key, response_obj.model_dump(mode="json"))
        # Invalidate parent image detail cache so the new variant shows up in GET /images/{id}
        await cache_delete_pattern(f"images:detail:{user_id}:{parent_image.id}")

        return response_obj

    async def get_job_status(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        image_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> TransformationResponse:
        """Look up the status and result of an asynchronous transformation job."""
        stmt = (
            select(ImageTransformation)
            .join(Image, ImageTransformation.parent_image_id == Image.id)
            .where(
                ImageTransformation.id == job_id,
                ImageTransformation.parent_image_id == image_id,
                Image.user_id == user_id,
            )
        )
        record = (await db.execute(stmt)).scalar_one_or_none()
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Transformation job not found",
            )
        return TransformationResponse.model_validate(record)


image_transformation_service = ImageTransformationService()
