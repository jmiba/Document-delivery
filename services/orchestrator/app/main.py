from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException

from app.config import settings
from app.db import init_db
from app.jobs import (
    approve_metadata_item,
    create_request,
    get_request_summary,
    list_job_events,
    list_requests,
    retry_request,
)
from app.schemas import ApproveMetadataRequest, FormCycleRequest


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Document Delivery Orchestrator", lifespan=lifespan)


def _check_token(provided: str | None, expected: str | None, name: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail=f"Invalid {name}")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/formcycle/requests")
def formcycle_webhook(
    payload: FormCycleRequest,
    x_formcycle_secret: str | None = Header(default=None),
) -> dict:
    _check_token(x_formcycle_secret, settings.formcycle_webhook_secret, "FormCycle secret")
    request_id, created = create_request(payload)
    return {"request_id": request_id, "created": created}


@app.get("/requests")
def get_requests(x_internal_token: str | None = Header(default=None)) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    return [request.model_dump(mode="json") for request in list_requests()]


@app.get("/requests/{request_id}")
def get_request(request_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    request = get_request_summary(request_id)
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    return request.model_dump(mode="json")


@app.get("/requests/{request_id}/events")
def get_request_events(request_id: str, x_internal_token: str | None = Header(default=None)) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    return [event.model_dump(mode="json") for event in list_job_events(request_id)]


@app.post("/requests/{request_id}/retry")
def retry_request_endpoint(request_id: str, x_internal_token: str | None = Header(default=None)) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    if not retry_request(request_id):
        raise HTTPException(status_code=404, detail="Request not found")
    return {"request_id": request_id, "queued": True}


@app.post("/requests/{request_id}/items/{item_id}/approve")
def approve_request_item(
    request_id: str,
    item_id: int,
    approval: ApproveMetadataRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    if not approve_metadata_item(request_id, item_id, approval):
        raise HTTPException(status_code=404, detail="Request item not found")
    return {"request_id": request_id, "item_id": item_id, "approved": True}
