"""Job endpoints: enqueue, poll, stream events (SSE).

Routes:
    POST /v1/jobs/generate-deck      enqueue a deck generation job
    GET  /v1/jobs/{id}               poll status + result
    GET  /v1/jobs/{id}/events        SSE stream of stage events

The generate-deck endpoint accepts the BYOK Anthropic API key in the body
(or via an X-Anthropic-API-Key header). It is persisted only on the queued
job row for the worker to consume, then can be wiped by a cleanup pass —
M6 will tighten this with column-level encryption.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from ...db.models import Job, JobKind, JobStatus
from ...services.jobs import (
    JobEventEnvelope,
    JobNotFound,
    enqueue_job,
    get_job,
    list_past_events,
)
from ..dependencies import (
    Catalog,
    CurrentTenant,
    DbSession,
    RequestLocale,
    Storage,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------


class GenerateDeckBody(BaseModel):
    """Body for POST /v1/jobs/generate-deck."""

    source_asset_ids: list[uuid.UUID] = Field(..., min_length=1, description="Asset ids from /v1/assets")
    user_intent: str = Field(..., description="What the deck is for. Korean / any language welcome.")
    target_pages: tuple[int, int] = (8, 12)
    canvas_format: str = "ppt169"
    style: str = Field(default="general", description="general | consultant | consultant-top")
    lang: str = "ko-KR"
    template_name: str | None = None
    model: str = "claude-opus-4-7"
    output_basename: str | None = None
    project_id: uuid.UUID | None = None
    fail_on_quality_error: bool = False


class JobResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    kind: JobKind
    status: JobStatus
    params: dict
    cost: dict
    result: dict
    error_message: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None

    @classmethod
    def from_row(cls, job: Job) -> "JobResponse":
        # Redact the BYOK key from the params echo so polling clients never see
        # other tenants' keys on a shared cache layer.
        params = dict(job.params or {})
        if "anthropic_api_key" in params:
            params["anthropic_api_key"] = "[redacted]"
        return cls(
            id=job.id,
            tenant_id=job.tenant_id,
            kind=job.kind,
            status=job.status,
            params=params,
            cost=dict(job.cost or {}),
            result=dict(job.result or {}),
            error_message=job.error_message,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            finished_at=job.finished_at.isoformat() if job.finished_at else None,
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/generate-deck",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a deck-generation job",
)
async def enqueue_generate_deck(
    body: GenerateDeckBody,
    tenant: CurrentTenant,
    session: DbSession,
    x_anthropic_api_key: Annotated[str | None, Header(alias="X-Anthropic-API-Key")] = None,
) -> JobResponse:
    """Persist a queued generate_deck job; the worker picks it up next.

    BYOK precedence: X-Anthropic-API-Key header > body field. At least one
    must be set or the worker will fail on the first LLM call.
    """
    anthropic_key = x_anthropic_api_key or ""
    if not anthropic_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "LLM_API_KEY_MISSING",
                "message": "Anthropic API 키가 필요합니다. X-Anthropic-API-Key 헤더로 전달하세요.",
                "message_en": "Anthropic API key required. Pass via X-Anthropic-API-Key header.",
            },
        )

    params = {
        "source_asset_ids": [str(x) for x in body.source_asset_ids],
        "user_intent": body.user_intent,
        "target_pages": list(body.target_pages),
        "canvas_format": body.canvas_format,
        "style": body.style,
        "lang": body.lang,
        "template_name": body.template_name,
        "model": body.model,
        "output_basename": body.output_basename or "deck",
        "fail_on_quality_error": body.fail_on_quality_error,
        # BYOK key — worker reads + nulls this out on completion (M6 encrypts).
        "anthropic_api_key": anthropic_key,
    }
    job = await enqueue_job(
        session=session,
        tenant=tenant,
        kind=JobKind.generate_deck,
        params=params,
        project_id=body.project_id,
        arq_pool=None,  # M3.5: in-process queueing only; arq wiring lands in M6
    )
    return JobResponse.from_row(job)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get job status + cost + result",
)
async def get_job_status(
    job_id: uuid.UUID,
    tenant: CurrentTenant,
    session: DbSession,
) -> JobResponse:
    try:
        job = await get_job(session=session, tenant=tenant, job_id=job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": f"작업 {job_id} 를 찾을 수 없습니다.",
                "message_en": f"Job {job_id} not found.",
            },
        ) from exc
    return JobResponse.from_row(job)


@router.get(
    "/{job_id}/events",
    summary="Server-sent events: stream stage progress",
)
async def stream_job_events(
    job_id: uuid.UUID,
    tenant: CurrentTenant,
    session: DbSession,
    after_id: Annotated[uuid.UUID | None, Query(description="Resume after this event id")] = None,
):
    """SSE stream of all stage events for *job_id*.

    Replays the DB history first (so a freshly-connected client doesn't miss
    earlier stages), then keeps the connection open and tails new events via
    the JobBus. The stream closes when the job reaches a terminal status.

    Tail subscription requires the FakeJobBus or RedisJobBus to expose a
    `subscriber` context manager. Both implementations do (see services/jobs.py).
    """
    try:
        job = await get_job(session=session, tenant=tenant, job_id=job_id)
    except JobNotFound as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "JOB_NOT_FOUND",
                "message": f"작업 {job_id} 를 찾을 수 없습니다.",
                "message_en": f"Job {job_id} not found.",
            },
        ) from exc

    history = await list_past_events(session=session, job_id=job_id, after_id=after_id)
    # Snapshot the terminal status BEFORE we attach a subscriber — if the job
    # already finished, history alone is enough.
    terminal = job.status in (JobStatus.done, JobStatus.failed, JobStatus.cancelled)

    from ...services.jobs import get_default_bus

    bus = get_default_bus()

    async def event_generator() -> AsyncIterator[dict]:
        # 1. Replay history.
        for envelope in history:
            yield _sse_payload(envelope)
        if terminal:
            return
        # 2. Tail new events via the bus subscriber.
        # FakeJobBus and RedisJobBus both expose a `subscriber` context manager
        # yielding either an asyncio.Queue (Fake) or a redis pubsub (Redis).
        # We handle the Fake case here directly; Redis fan-out is implemented
        # inline so we don't depend on a unified interface yet.
        if hasattr(bus, "subscriber"):
            async with bus.subscriber(job_id) as subscriber:
                if hasattr(subscriber, "get"):  # asyncio.Queue (FakeJobBus)
                    while True:
                        item = await subscriber.get()
                        if item is None:
                            return
                        yield _sse_payload(item)
                else:  # redis pubsub
                    async for msg in subscriber.listen():  # pragma: no cover - prod path
                        if msg["type"] != "message":
                            continue
                        envelope = JobEventEnvelope(
                            job_id=job_id,
                            type=msg["data"]["type"],
                            payload=msg["data"]["payload"],
                            created_at=msg["data"]["created_at"],
                        )
                        yield _sse_payload(envelope)
        else:  # pragma: no cover
            return

    return EventSourceResponse(event_generator())


def _sse_payload(envelope: JobEventEnvelope) -> dict:
    return {
        "event": envelope.type.value,
        "data": json.dumps(envelope.to_jsonable(), ensure_ascii=False),
    }
