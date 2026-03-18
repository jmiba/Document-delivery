from __future__ import annotations

from contextlib import asynccontextmanager
import logging
from urllib.parse import parse_qsl

from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from pydantic import ValidationError

from app.clarification import parse_clarification_token
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
logger = logging.getLogger(__name__)


def _check_token(provided: str | None, expected: str | None, name: str) -> None:
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail=f"Invalid {name}")


def _build_request_payload(payload: dict) -> dict:
    if payload.get("items"):
        return payload

    bibliographic_data = None
    bibtex = (payload.get("bibtex") or "").strip() or None
    author = payload.get("author")
    if not bibtex:
        title = payload.get("workTitle") or payload.get("title")
        publication_title = payload.get("container_title") or payload.get("publication_title")
        year = payload.get("issued") or payload.get("year")
        if title and publication_title and year:
            bibliographic_data = {
                "item_type": payload.get("item_type") or "journalArticle",
                "title": title,
                "creators": [author] if author else [],
                "publication_title": publication_title,
                "year": year,
                "volume": payload.get("volume") or None,
                "issue": payload.get("issue") or None,
                "pages": payload.get("page") or payload.get("pages") or None,
                "doi": payload.get("DOI") or payload.get("doi") or None,
                "publisher": payload.get("publisher") or None,
                "place": payload.get("place") or None,
                "series": payload.get("series") or None,
                "edition": payload.get("edition") or None,
                "isbn": payload.get("isbn") or None,
            }

    item_index_raw = payload.get("item_index")
    try:
        item_index = int(item_index_raw) if item_index_raw not in (None, "") else 0
    except (TypeError, ValueError):
        item_index = 0

    request_id = payload.get("request_id") or payload.get("formcycle_submission_id")
    formcycle_submission_id = payload.get("formcycle_submission_id")
    user_email = payload.get("user_email") or payload.get("email")
    user_name = payload.get("user_name")
    if not user_name:
        given_name = (payload.get("givenName") or "").strip()
        surname = (payload.get("surname") or "").strip()
        user_name = " ".join(part for part in (given_name, surname) if part).strip() or None

    return {
        "request_id": request_id,
        "formcycle_submission_id": formcycle_submission_id,
        "user_email": user_email,
        "user_name": user_name,
        "language": payload.get("language"),
        "delivery_days": payload.get("delivery_days"),
        "items": [
            {
                "item_index": item_index,
                "bibliographic_data": bibliographic_data,
                "bibtex": bibtex,
            }
        ],
    }


def _build_clarification_payload(payload: dict) -> dict:
    token = payload.get("token")
    token_claims = parse_clarification_token(token) if token else None
    author = payload.get("author")
    return {
        "request_id": payload.get("request_id") or (token_claims or {}).get("request_id"),
        "item_id": payload.get("item_id") or (token_claims or {}).get("item_id"),
        "token": token,
        "operator_message": payload.get("operator_message"),
        "user_note": payload.get("user_note"),
        "bibliographic_data": {
            "item_type": payload.get("item_type") or "journalArticle",
            "title": payload.get("workTitle") or payload.get("title") or "",
            "creators": [author] if author else [],
            "publication_title": payload.get("container_title") or payload.get("publication_title") or "",
            "year": payload.get("issued") or payload.get("year") or "",
            "volume": payload.get("volume") or None,
            "issue": payload.get("issue") or None,
            "pages": payload.get("page") or payload.get("pages") or None,
            "doi": payload.get("DOI") or payload.get("doi") or None,
            "publisher": payload.get("publisher") or None,
            "place": payload.get("place") or None,
            "series": payload.get("series") or None,
            "edition": payload.get("edition") or None,
            "isbn": payload.get("isbn") or None,
        },
    }


async def _parse_formcycle_request(request: Request) -> FormCycleRequest:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON payload must be an object")
        logger.info("FormCycle request webhook received JSON payload with keys: %s", sorted(payload.keys()))
        return FormCycleRequest(**_build_request_payload(payload))

    raw_body = await request.body()
    form = await request.form()
    payload = {key: value for key, value in form.multi_items()}
    if not payload:
        body = raw_body.decode("utf-8", errors="ignore").strip()
        if body:
            payload = dict(parse_qsl(body, keep_blank_values=True))
    logger.warning(
        "FormCycle request webhook content-type=%s form payload keys=%s raw_body=%r",
        content_type,
        sorted(payload.keys()),
        raw_body[:1000],
    )
    return FormCycleRequest(**_build_request_payload(payload))


async def _parse_formcycle_clarification(request: Request) -> FormCycleClarificationResponse:
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="JSON payload must be an object")
        logger.info("FormCycle clarification webhook received JSON payload with keys: %s", sorted(payload.keys()))
        return FormCycleClarificationResponse(**payload)

    raw_body = await request.body()
    form = await request.form()
    payload = {key: value for key, value in form.multi_items()}
    if not payload:
        body = raw_body.decode("utf-8", errors="ignore").strip()
        if body:
            payload = dict(parse_qsl(body, keep_blank_values=True))
    logger.warning(
        "FormCycle clarification webhook content-type=%s form payload keys=%s raw_body=%r",
        content_type,
        sorted(payload.keys()),
        raw_body[:1000],
    )
    return FormCycleClarificationResponse(**_build_clarification_payload(payload))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/webhooks/formcycle/requests")
async def formcycle_webhook(
    request: Request,
    x_formcycle_secret: str | None = Header(default=None),
) -> dict:
    _check_token(x_formcycle_secret, settings.formcycle_webhook_secret, "FormCycle secret")
    try:
        payload = await _parse_formcycle_request(request)
    except ValidationError as exc:
        logger.warning("FormCycle request validation failed: %s", exc.errors())
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        logger.warning("FormCycle request payload rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request_id, created = create_request(payload)
    return {"request_id": request_id, "created": created}


@app.post("/webhooks/formcycle/clarifications")
async def formcycle_clarification_webhook(
    request: Request,
    x_formcycle_secret: str | None = Header(default=None),
) -> dict:
    _check_token(x_formcycle_secret, settings.formcycle_webhook_secret, "FormCycle secret")
    try:
        payload = await _parse_formcycle_clarification(request)
    except ValidationError as exc:
        logger.warning("FormCycle clarification validation failed: %s", exc.errors())
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        logger.warning("FormCycle clarification payload rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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
