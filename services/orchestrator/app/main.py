from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from redis import Redis
from rq import Queue
from rq.job import Job

from app.config import settings
from app.jobs import process_digitization_job
from app.schemas import FormCycleEvent

app = FastAPI(title="Digitization Orchestrator")

redis_conn = Redis.from_url(settings.redis_url)
queue = Queue(settings.queue_name, connection=redis_conn)


def _check_token(provided: str | None, expected: str | None, name: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail=f"Invalid {name}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/formcycle")
def formcycle_webhook(
    event: FormCycleEvent,
    x_formcycle_secret: str | None = Header(default=None),
) -> dict:
    _check_token(x_formcycle_secret, settings.formcycle_webhook_secret, "FormCycle secret")
    job = queue.enqueue(process_digitization_job, event.model_dump())
    return {"queued": True, "job_id": job.id, "request_id": event.request_id}


@app.post("/deliver/manual")
def manual_deliver(
    event: FormCycleEvent,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    job = queue.enqueue(process_digitization_job, event.model_dump())
    return {"queued": True, "job_id": job.id, "request_id": event.request_id}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    job = Job.fetch(job_id, connection=redis_conn)
    return {
        "job_id": job.id,
        "status": job.get_status(refresh=True),
        "result": job.result,
        "enqueued_at": str(job.enqueued_at),
        "ended_at": str(job.ended_at),
    }


@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    previous = Job.fetch(job_id, connection=redis_conn)
    if not previous.args:
        raise HTTPException(status_code=400, detail="Original job has no payload.")
    new_job = queue.enqueue(process_digitization_job, *previous.args)
    return {"queued": True, "job_id": new_job.id, "retry_of": job_id}
