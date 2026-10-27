from celery import Celery

from app.core.config import settings

# Initialize Celery with RabbitMQ (AMQP) as message broker and Redis as result backend
celery_app = Celery(
    "image_processing_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Acknowledge task only AFTER worker finishes processing so no job is lost on crash
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)
