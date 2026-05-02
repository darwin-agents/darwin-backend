"""Celery application — shared instance used by all workers."""

from celery import Celery

from config import get_settings

settings = get_settings()

celery_app = Celery(
    "darwin",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "workers.webhook_worker",
        "workers.wake_worker",
        "workers.telemetry_worker",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Dead-letter queue: tasks that exceed max_retries land here
    task_routes={
        "workers.webhook_worker.*": {"queue": "webhooks"},
        "workers.wake_worker.*": {"queue": "wake"},
        "workers.telemetry_worker.*": {"queue": "telemetry"},
    },
    # Celery Beat schedule for cron wake-ups and container health checks
    beat_schedule={
        "check-due-wakeups": {
            "task": "workers.wake_worker.check_due_wakeups",
            "schedule": 30.0,  # every 30 seconds
        },
        "check-container-health": {
            "task": "workers.telemetry_worker.check_container_health",
            "schedule": 60.0,  # every 60 seconds
        },
    },
)
