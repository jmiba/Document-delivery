from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Header, HTTPException, UploadFile

from app.config import settings
from app.db import init_db
from app.jobs import (
    approve_metadata_item,
    create_request,
    get_request_summary,
    ingest_clarification_response,
    get_period_statistics,
    list_clarification_templates,
    list_email_templates,
    list_rejection_templates,
    list_job_events,
    list_requests,
    remove_uploaded_scan_for_item,
    reject_request_item,
    request_item_clarification,
    retry_request,
    upload_scan_for_item,
    update_clarification_template,
    update_email_template,
    update_rejection_template,
)
from app.schemas import (
    ApproveMetadataRequest,
    FormCycleClarificationResponse,
    FormCycleRequest,
    RejectRequestItemRequest,
    RequestClarificationRequest,
    UpdateEmailTemplateRequest,
)


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


@app.post("/webhooks/formcycle/clarifications")
def formcycle_clarification_webhook(
    payload: FormCycleClarificationResponse,
    x_formcycle_secret: str | None = Header(default=None),
) -> dict:
    _check_token(x_formcycle_secret, settings.formcycle_webhook_secret, "FormCycle secret")
    try:
        accepted = ingest_clarification_response(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not accepted:
        raise HTTPException(status_code=404, detail="Clarification target not found")
    return {"request_id": payload.request_id, "item_id": payload.item_id, "accepted": True}


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


@app.get("/statistics")
def get_statistics(
    granularity: str = "month",
    periods: int = 12,
    x_internal_token: str | None = Header(default=None),
) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        return [row.model_dump(mode="json") for row in get_period_statistics(granularity=granularity, periods=periods)]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


@app.post("/requests/{request_id}/items/{item_id}/clarification-request")
def request_item_clarification_endpoint(
    request_id: str,
    item_id: int,
    payload: RequestClarificationRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        requested = request_item_clarification(request_id, item_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not requested:
        raise HTTPException(status_code=404, detail="Request item not found")
    return {"request_id": request_id, "item_id": item_id, "requested": True}


@app.post("/requests/{request_id}/items/{item_id}/reject")
def reject_request_item_endpoint(
    request_id: str,
    item_id: int,
    payload: RejectRequestItemRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        rejected = reject_request_item(request_id, item_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not rejected:
        raise HTTPException(status_code=404, detail="Request item not found")
    return {"request_id": request_id, "item_id": item_id, "rejected": True}


@app.post("/requests/{request_id}/items/{item_id}/scan")
async def upload_request_item_scan(
    request_id: str,
    item_id: int,
    file: UploadFile = File(...),
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF uploads are supported")
    header = await file.read(5)
    if header != b"%PDF-":
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid PDF")
    await file.seek(0)
    pdf_bytes = await file.read()
    try:
        stored = upload_scan_for_item(request_id, item_id, filename, pdf_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not stored:
        raise HTTPException(status_code=404, detail="Request item not found")
    return {"request_id": request_id, "item_id": item_id, "uploaded": True}


@app.delete("/requests/{request_id}/items/{item_id}/scan")
def delete_request_item_scan(
    request_id: str,
    item_id: int,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        removed = remove_uploaded_scan_for_item(request_id, item_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Request item not found")
    return {"request_id": request_id, "item_id": item_id, "removed": True}


@app.get("/email-templates")
def get_email_templates(x_internal_token: str | None = Header(default=None)) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    return [template.model_dump(mode="json") for template in list_email_templates()]


@app.get("/clarification-templates")
def get_clarification_templates(x_internal_token: str | None = Header(default=None)) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    return [template.model_dump(mode="json") for template in list_clarification_templates()]


@app.get("/rejection-templates")
def get_rejection_templates(x_internal_token: str | None = Header(default=None)) -> list[dict]:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    return [template.model_dump(mode="json") for template in list_rejection_templates()]


@app.put("/email-templates/{language}")
def put_email_template(
    language: str,
    payload: UpdateEmailTemplateRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        template = update_email_template(language, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return template.model_dump(mode="json")


@app.put("/clarification-templates/{language}")
def put_clarification_template(
    language: str,
    payload: UpdateEmailTemplateRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        template = update_clarification_template(language, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return template.model_dump(mode="json")


@app.put("/rejection-templates/{language}")
def put_rejection_template(
    language: str,
    payload: UpdateEmailTemplateRequest,
    x_internal_token: str | None = Header(default=None),
) -> dict:
    _check_token(x_internal_token, settings.internal_api_token, "internal token")
    try:
        template = update_rejection_template(language, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return template.model_dump(mode="json")
