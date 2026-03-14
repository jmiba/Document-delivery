from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.clients import FormCycleClient, NextcloudClient, OpenAlexClient, ZoteroClient
from app.config import settings
from app.db import session_scope
from app.models import DeliveryRequest, JobEvent, RequestItem
from app.schemas import (
    BibliographicData,
    DeliveryItemPayload,
    DeliveryNotificationPayload,
    FormCycleRequest,
    JobEventSummary,
    RequestItemSummary,
    RequestSummary,
)


def create_request(payload: FormCycleRequest) -> tuple[str, bool]:
    with session_scope() as session:
        existing = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == payload.request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if existing:
            log_event(
                session,
                request_id=existing.request_id,
                request_item_id=None,
                event_type="duplicate_request_ignored",
                payload={"request_id": payload.request_id},
            )
            return existing.request_id, False

        request = DeliveryRequest(
            request_id=payload.request_id,
            formcycle_submission_id=payload.formcycle_submission_id,
            user_email=payload.user_email,
            user_name=payload.user_name,
            status="RECEIVED",
            delivery_days=payload.delivery_days or settings.default_link_expiry_days,
        )
        session.add(request)
        session.flush()

        for item_payload in payload.items:
            bib = item_payload.bibliographic_data
            request.items.append(
                RequestItem(
                    item_index=item_payload.item_index or 0,
                    title=bib.title,
                    creators="; ".join(bib.creators),
                    publication_title=bib.publication_title,
                    year=bib.year,
                    volume=bib.volume,
                    issue=bib.issue,
                    pages=bib.pages,
                    doi=bib.doi,
                    language=bib.language,
                    abstract_note=bib.abstract_note,
                    item_type=bib.item_type,
                    status="PENDING_METADATA",
                )
            )

        log_event(
            session,
            request_id=request.request_id,
            request_item_id=None,
            event_type="request_created",
            payload={"item_count": len(request.items)},
        )
        _sync_request_status(session, request)
        return request.request_id, True


def list_requests() -> list[RequestSummary]:
    with session_scope() as session:
        requests = session.scalars(
            select(DeliveryRequest)
            .options(selectinload(DeliveryRequest.items))
            .order_by(DeliveryRequest.updated_at.desc())
        ).unique()
        return [build_request_summary(request) for request in requests]


def get_request_summary(request_id: str) -> RequestSummary | None:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return None
        return build_request_summary(request)


def list_job_events(request_id: str) -> list[JobEventSummary]:
    with session_scope() as session:
        events = session.scalars(
            select(JobEvent)
            .where(JobEvent.request_id == request_id)
            .order_by(JobEvent.created_at.desc())
        )
        return [
            JobEventSummary(
                id=event.id,
                request_id=event.request_id,
                request_item_id=event.request_item_id,
                level=event.level,
                event_type=event.event_type,
                payload_json=event.payload_json,
                created_at=event.created_at,
            )
            for event in events
        ]


def retry_request(request_id: str) -> bool:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False

        for item in request.items:
            if item.status == "FAILED":
                item.status = "WAITING_FOR_ATTACHMENT" if item.zotero_item_key else "PENDING_METADATA"
                item.last_error = None
                item.next_poll_at = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(session, request_id=request.request_id, request_item_id=None, event_type="request_retried")
        return True


def process_next_item() -> bool:
    snapshot = _claim_next_item_snapshot()
    if not snapshot:
        return False

    status = snapshot["status"]
    try:
        if status == "PENDING_METADATA":
            _process_metadata_stage(snapshot)
        elif status == "WAITING_FOR_ATTACHMENT":
            _process_attachment_stage(snapshot)
        elif status == "PROCESSING_PDF":
            _process_delivery_stage(snapshot)
        else:
            _update_item(snapshot["item_id"], status="FAILED", last_error=f"Unsupported status {status}")
        _maybe_finalize_request(snapshot["request_id"])
        return True
    except Exception as exc:
        _mark_item_failed(snapshot, exc)
        return True


def build_request_summary(request: DeliveryRequest) -> RequestSummary:
    items = [
        RequestItemSummary(
            id=item.id,
            item_index=item.item_index,
            title=item.title,
            creators=item.creators,
            publication_title=item.publication_title,
            year=item.year,
            doi=item.doi,
            status=item.status,
            metadata_source=item.metadata_source,
            zotero_item_key=item.zotero_item_key,
            zotero_attachment_key=item.zotero_attachment_key,
            download_url=item.download_url,
            expires_on=item.expires_on,
            last_error=item.last_error,
            updated_at=item.updated_at,
        )
        for item in request.items
    ]
    return RequestSummary(
        request_id=request.request_id,
        formcycle_submission_id=request.formcycle_submission_id,
        user_email=request.user_email,
        user_name=request.user_name,
        status=request.status,
        delivery_days=request.delivery_days,
        notification_sent_at=request.notification_sent_at,
        created_at=request.created_at,
        updated_at=request.updated_at,
        items=items,
    )


def log_event(
    session,
    request_id: str,
    request_item_id: int | None,
    event_type: str,
    payload: dict | None = None,
    level: str = "INFO",
) -> None:
    session.add(
        JobEvent(
            request_id=request_id,
            request_item_id=request_item_id,
            level=level,
            event_type=event_type,
            payload_json=json.dumps(payload, ensure_ascii=True) if payload is not None else None,
        )
    )


def _claim_next_item_snapshot() -> dict | None:
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        item = session.scalar(
            select(RequestItem)
            .join(RequestItem.request)
            .options(joinedload(RequestItem.request))
            .where(
                (RequestItem.status == "PENDING_METADATA")
                | (
                    (RequestItem.status == "WAITING_FOR_ATTACHMENT")
                    & ((RequestItem.next_poll_at.is_(None)) | (RequestItem.next_poll_at <= now))
                )
                | (RequestItem.status == "PROCESSING_PDF")
            )
            .order_by(RequestItem.updated_at.asc())
        )
        if not item:
            return None
        item.request.status = "IN_PROGRESS"
        return {
            "item_id": item.id,
            "request_id": item.request.request_id,
            "request_db_id": item.request_db_id,
            "formcycle_submission_id": item.request.formcycle_submission_id,
            "user_email": item.request.user_email,
            "user_name": item.request.user_name,
            "delivery_days": item.request.delivery_days,
            "status": item.status,
            "item_index": item.item_index,
            "title": item.title,
            "creators": item.creators,
            "publication_title": item.publication_title,
            "year": item.year,
            "volume": item.volume,
            "issue": item.issue,
            "pages": item.pages,
            "doi": item.doi,
            "language": item.language,
            "abstract_note": item.abstract_note,
            "item_type": item.item_type,
            "zotero_item_key": item.zotero_item_key,
            "zotero_attachment_key": item.zotero_attachment_key,
        }


def _process_metadata_stage(snapshot: dict) -> None:
    source_bib = _snapshot_to_bib(snapshot)
    normalized_bib, metadata_source = OpenAlexClient().normalize(source_bib)
    citation_text = _format_citation(normalized_bib)
    zotero = ZoteroClient()
    existing_item = zotero.find_existing_item(normalized_bib)

    if existing_item:
        attachment_key = zotero.find_pdf_attachment(existing_item["key"])
        _update_item(
            snapshot["item_id"],
            title=normalized_bib.title,
            creators="; ".join(normalized_bib.creators),
            publication_title=normalized_bib.publication_title,
            year=normalized_bib.year,
            volume=normalized_bib.volume,
            issue=normalized_bib.issue,
            pages=normalized_bib.pages,
            doi=normalized_bib.doi,
            language=normalized_bib.language,
            abstract_note=normalized_bib.abstract_note,
            metadata_source=metadata_source,
            normalized_json=normalized_bib.model_dump_json(),
            citation_text=citation_text,
            zotero_item_key=existing_item["key"],
            zotero_attachment_key=attachment_key,
            status="PROCESSING_PDF" if attachment_key else "WAITING_FOR_ATTACHMENT",
            next_poll_at=None if attachment_key else _next_poll_time(),
        )
        return

    zotero_item_key = zotero.create_item(normalized_bib, snapshot["request_id"])
    _update_item(
        snapshot["item_id"],
        title=normalized_bib.title,
        creators="; ".join(normalized_bib.creators),
        publication_title=normalized_bib.publication_title,
        year=normalized_bib.year,
        volume=normalized_bib.volume,
        issue=normalized_bib.issue,
        pages=normalized_bib.pages,
        doi=normalized_bib.doi,
        language=normalized_bib.language,
        abstract_note=normalized_bib.abstract_note,
        metadata_source=metadata_source,
        normalized_json=normalized_bib.model_dump_json(),
        citation_text=citation_text,
        zotero_item_key=zotero_item_key,
        status="WAITING_FOR_ATTACHMENT",
        next_poll_at=_next_poll_time(),
    )


def _process_attachment_stage(snapshot: dict) -> None:
    if not snapshot["zotero_item_key"]:
        raise RuntimeError("Missing Zotero item key for attachment lookup.")

    attachment_key = ZoteroClient().find_pdf_attachment(snapshot["zotero_item_key"])
    if not attachment_key:
        _update_item(
            snapshot["item_id"],
            status="WAITING_FOR_ATTACHMENT",
            next_poll_at=_next_poll_time(),
        )
        return

    _update_item(
        snapshot["item_id"],
        zotero_attachment_key=attachment_key,
        status="PROCESSING_PDF",
        next_poll_at=None,
    )


def _process_delivery_stage(snapshot: dict) -> None:
    attachment_key = snapshot["zotero_attachment_key"]
    if not attachment_key and snapshot["zotero_item_key"]:
        attachment_key = ZoteroClient().find_pdf_attachment(snapshot["zotero_item_key"])
    if not attachment_key:
        _update_item(
            snapshot["item_id"],
            status="WAITING_FOR_ATTACHMENT",
            next_poll_at=_next_poll_time(),
        )
        return

    work_dir = Path(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    source_pdf = work_dir / f"{snapshot['request_id']}-{snapshot['item_index']}.source.pdf"
    ZoteroClient().download_attachment(attachment_key, source_pdf)
    processed_pdf = _maybe_run_ocr(source_pdf)

    expires_at = datetime.now(timezone.utc) + timedelta(days=snapshot["delivery_days"])
    remote_filename = f"{snapshot['request_id']}-{snapshot['item_index']}.pdf"
    nextcloud = NextcloudClient()
    remote_path = nextcloud.upload_pdf(processed_pdf, remote_filename)
    download_url, expires_on = nextcloud.create_share_link(remote_path, expires_at)

    _update_item(
        snapshot["item_id"],
        zotero_attachment_key=attachment_key,
        download_url=download_url,
        expires_on=expires_on,
        status="READY_TO_NOTIFY",
        next_poll_at=None,
    )


def _maybe_finalize_request(request_id: str) -> None:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return

        _sync_request_status(session, request)
        if request.notification_sent_at:
            return

        if not request.items:
            return

        if any(item.status not in {"READY_TO_NOTIFY", "DELIVERED"} for item in request.items):
            return

        payload = DeliveryNotificationPayload(
            request_id=request.request_id,
            formcycle_submission_id=request.formcycle_submission_id,
            user_email=request.user_email,
            user_name=request.user_name,
            status="DELIVERED",
            items=[
                DeliveryItemPayload(
                    item_index=item.item_index,
                    citation_text=item.citation_text or _format_citation(_item_to_bib(item)),
                    download_url=item.download_url or "",
                    expires_on=item.expires_on or "",
                    zotero_item_key=item.zotero_item_key or "",
                )
                for item in request.items
            ],
        )

    FormCycleClient().send_delivery(payload)

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return
        for item in request.items:
            item.status = "DELIVERED"
        request.notification_sent_at = datetime.now(timezone.utc)
        request.status = "PROCESSED"
        request.last_error = None
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=None,
            event_type="request_notified",
            payload={"item_count": len(request.items)},
        )


def _mark_item_failed(snapshot: dict, exc: Exception) -> None:
    message = str(exc)
    with session_scope() as session:
        item = session.get(RequestItem, snapshot["item_id"])
        if not item:
            return
        item.status = "FAILED"
        item.last_error = message
        request = item.request
        request.status = "ATTENTION"
        request.last_error = message
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="item_failed",
            payload={"error": message},
            level="ERROR",
        )


def _update_item(item_id: int, **changes) -> None:
    with session_scope() as session:
        item = session.get(RequestItem, item_id)
        if not item:
            return
        for key, value in changes.items():
            setattr(item, key, value)
        request = item.request
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="item_updated",
            payload={key: _serialize_value(value) for key, value in changes.items()},
        )


def _sync_request_status(session, request: DeliveryRequest) -> None:
    statuses = {item.status for item in request.items}
    if not statuses:
        request.status = "RECEIVED"
    elif statuses == {"DELIVERED"} and request.notification_sent_at:
        request.status = "PROCESSED"
    elif "FAILED" in statuses:
        request.status = "ATTENTION"
    elif statuses <= {"WAITING_FOR_ATTACHMENT"}:
        request.status = "WAITING_FOR_ATTACHMENT"
    elif "READY_TO_NOTIFY" in statuses and statuses <= {"READY_TO_NOTIFY", "DELIVERED"}:
        request.status = "READY_TO_NOTIFY"
    else:
        request.status = "IN_PROGRESS"


def _snapshot_to_bib(snapshot: dict) -> BibliographicData:
    creators = [creator.strip() for creator in snapshot["creators"].split(";") if creator.strip()]
    return BibliographicData(
        item_type=snapshot["item_type"],
        title=snapshot["title"],
        creators=creators,
        publication_title=snapshot["publication_title"],
        year=snapshot["year"],
        volume=snapshot["volume"],
        issue=snapshot["issue"],
        pages=snapshot["pages"],
        doi=snapshot["doi"],
        language=snapshot["language"],
        abstract_note=snapshot["abstract_note"],
    )


def _item_to_bib(item: RequestItem) -> BibliographicData:
    creators = [creator.strip() for creator in item.creators.split(";") if creator.strip()]
    return BibliographicData(
        item_type=item.item_type,
        title=item.title,
        creators=creators,
        publication_title=item.publication_title,
        year=item.year,
        volume=item.volume,
        issue=item.issue,
        pages=item.pages,
        doi=item.doi,
        language=item.language,
        abstract_note=item.abstract_note,
    )


def _format_citation(bib: BibliographicData) -> str:
    creators = ", ".join(bib.creators) if bib.creators else "Unknown author"
    details = f"{bib.publication_title} ({bib.year})"
    volume_issue = " ".join(part for part in [bib.volume, f"({bib.issue})" if bib.issue else None] if part)
    pages = f": {bib.pages}" if bib.pages else ""
    return " ".join(part for part in [creators + ".", bib.title + ".", details, volume_issue + pages] if part).strip()


def _next_poll_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.attachment_poll_interval_seconds)


def _maybe_run_ocr(source_pdf: Path) -> Path:
    if not settings.ocr_command_template:
        return source_pdf

    output_pdf = source_pdf.with_name(f"{source_pdf.stem}.ocr.pdf")
    command = settings.ocr_command_template.format(input=source_pdf, output=output_pdf)
    subprocess.run(shlex.split(command), check=True)
    if output_pdf.exists():
        return output_pdf
    return source_pdf


def _serialize_value(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)
