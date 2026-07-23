"""Ingest hooks: RTVC notifies Kairos of a new/updated media to index.

Also exposes a manual trigger for testing without wiring the webhook.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from app.config import settings
from app.schemas import ProcessResponse, WebhookPayload
from app.worker.tasks import process_media

router = APIRouter(tags=["ingest"])


def _check_secret(provided: str | None) -> None:
    if settings.webhook_secret and provided != settings.webhook_secret:
        raise HTTPException(status_code=401, detail="bad webhook secret")


@router.post("/webhook/rtvc-media-created", response_model=ProcessResponse)
def rtvc_media_created(
    payload: WebhookPayload,
    x_webhook_secret: str | None = Header(default=None),
):
    """Called by RTVC after a media is uploaded / ready. Enqueues indexing."""
    _check_secret(x_webhook_secret)
    res = process_media.delay(payload.media_id, payload.title)
    return ProcessResponse(rtvc_id=payload.media_id, task_id=res.id, status="queued")


@router.post("/process/{media_id}", response_model=ProcessResponse)
def trigger(media_id: int):
    """Manual trigger (testing): index an RTVC media_id now."""
    res = process_media.delay(media_id)
    return ProcessResponse(rtvc_id=media_id, task_id=res.id, status="queued")
