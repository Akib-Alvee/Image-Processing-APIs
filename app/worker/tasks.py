import uuid
from typing import Any

from sqlalchemy import select

from app.core.database import SyncSessionLocal
from app.core.redis import sync_cache_set, sync_redis_client
from app.models.image import Image, ImageTransformation
from app.schemas.image import TransformationOptions, TransformationResponse
from app.services.processor import image_processor
from app.services.storage import storage_service
from app.worker.celery_app import celery_app


@celery_app.task(
    name="app.worker.tasks.process_image_transformation_task",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
)
def process_image_transformation_task(
    self: Any,
    transformation_id_str: str,
) -> dict[str, Any]:
    """
    Background Celery task consumed from RabbitMQ:
    1. Loads the PENDING ImageTransformation and parent Image from PostgreSQL.
    2. Updates status to PROCESSING.
    3. Downloads original image bytes from MinIO/S3.
    4. Runs the Pillow transformation pipeline (crop, resize, rotate, filters, watermark, etc.).
    5. Uploads transformed bytes to MinIO/S3.
    6. Marks record COMPLETED in PostgreSQL and populates the Redis transformation cache.
    """
    transformation_id = uuid.UUID(transformation_id_str)

    with SyncSessionLocal() as db:
        transform_record = db.execute(
            select(ImageTransformation).where(ImageTransformation.id == transformation_id)
        ).scalar_one_or_none()

        if transform_record is None:
            return {"status": "NOT_FOUND", "transformation_id": transformation_id_str}

        parent_image = db.execute(
            select(Image).where(Image.id == transform_record.parent_image_id)
        ).scalar_one_or_none()

        if parent_image is None:
            transform_record.status = "FAILED"
            transform_record.error_message = "Parent image no longer exists"
            db.commit()
            return {"status": "FAILED", "error": "Parent image no longer exists"}

        try:
            transform_record.status = "PROCESSING"
            transform_record.celery_task_id = self.request.id
            db.commit()

            # 1. Download original image from MinIO/S3
            source_bytes = storage_service.download_bytes(parent_image.storage_key)

            # 2. Validate parameters & run Pillow transformation pipeline
            options = TransformationOptions.model_validate(transform_record.parameters)
            processed = image_processor.process_image(
                source_bytes=source_bytes,
                source_format=parent_image.format,
                options=options,
            )

            # 3. Upload transformed image to MinIO/S3
            storage_key = storage_service.generate_storage_key(
                user_id=parent_image.user_id,
                extension=processed.format,
                prefix="transformed",
            )
            public_url = storage_service.upload_bytes(
                data=processed.data,
                storage_key=storage_key,
                content_type=processed.content_type,
            )

            # 4. Update PostgreSQL record to COMPLETED
            transform_record.storage_key = storage_key
            transform_record.url = public_url
            transform_record.format = processed.format
            transform_record.width = processed.width
            transform_record.height = processed.height
            transform_record.size_bytes = processed.size_bytes
            transform_record.status = "COMPLETED"
            transform_record.error_message = None
            db.commit()
            db.refresh(transform_record)

            # 5. Cache the completed transformation in Redis & invalidate parent detail cache
            serialized = TransformationResponse.model_validate(transform_record).model_dump(
                mode="json"
            )
            sync_cache_set(
                f"transform:hash:{transform_record.transformation_hash}",
                serialized,
            )
            try:
                sync_redis_client.delete(
                    f"images:detail:{parent_image.user_id}:{parent_image.id}"
                )
            except Exception:
                pass

            return serialized

        except Exception as exc:
            db.rollback()
            transform_record = db.execute(
                select(ImageTransformation).where(ImageTransformation.id == transformation_id)
            ).scalar_one_or_none()
            if transform_record is not None:
                transform_record.status = "FAILED"
                transform_record.error_message = str(exc)
                db.commit()
            raise
