from celery import Celery
from celery.signals import worker_process_init

from app.config import settings

celery_app = Celery(
    "kairos",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_max_tasks_per_child=20,  # release RAM (torch/vosk) periodically
)

# Indexation automatique : le worker démarré avec -B (beat embarqué) déclenche
# le balayage. Le calendrier n'est publié que si la fonction est activée, pour
# qu'une installation classique ne paie aucun réveil périodique.
if settings.autosync_enabled:
    celery_app.conf.beat_schedule = {
        "kairos-autosync": {
            "task": "kairos.autosync",
            "schedule": max(settings.autosync_interval_minutes, 1) * 60.0,
        }
    }


@worker_process_init.connect
def _preload_models(**_kwargs) -> None:
    """Charge le modèle d'embeddings dans chaque process worker au démarrage,
    pour que la première vidéo traitée ne paie pas ce coût."""
    try:
        from app.embeddings import embed_one
        embed_one("warmup")
    except Exception:  # noqa: BLE001
        pass
