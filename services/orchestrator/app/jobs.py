from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import case, select
from sqlalchemy.orm import joinedload, selectinload

from app.clients import NextcloudClient, NotificationClient, ZoteroClient
from app.clarification import build_clarification_form_url, build_clarification_token, verify_clarification_token
from app.config import settings
from app.db import session_scope
from app.delivery_pdf import prepend_delivery_cover_page
from app.models import DeliveryRequest, JobEvent, RequestItem
from app.ocr import OcrOverlayResult, create_tesseract_overlay_pdf
from app.resolution import ResolutionService
from app.schemas import (
    ApproveMetadataRequest,
    BibliographicData,
    ClarificationNotificationPayload,
    DeliveryItemPayload,
    DeliveryNotificationPayload,
    EmailTemplateSummary,
    FormCycleClarificationResponse,
    FormCycleRequest,
    JobEventSummary,
    PeriodStatisticsSummary,
    RejectRequestItemRequest,
    RejectionNotificationPayload,
    RequestClarificationRequest,
    RequestItemSummary,
    RequestSummary,
    UpdateEmailTemplateRequest,
)


def create_request(payload: FormCycleRequest) -> tuple[str, bool]:
    with session_scope() as session:
        existing = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == payload.request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if existing:
            added_items = _append_request_items(existing, payload.items)
            if payload.language and not existing.form_language:
                existing.form_language = payload.language
            event_type = "request_items_appended" if added_items else "duplicate_request_ignored"
            log_event(
                session,
                request_id=existing.request_id,
                request_item_id=None,
                event_type=event_type,
                payload={"request_id": payload.request_id, "added_item_count": added_items},
            )
            if added_items:
                existing.last_error = None
                _sync_request_status(session, existing)
            return existing.request_id, False

        request = DeliveryRequest(
            request_id=payload.request_id,
            formcycle_submission_id=payload.formcycle_submission_id,
            user_email=payload.user_email,
            user_name=payload.user_name,
            form_language=payload.language,
            status="RECEIVED",
            delivery_days=payload.delivery_days or settings.default_link_expiry_days,
        )
        session.add(request)
        session.flush()

        _append_request_items(request, payload.items)

        log_event(
            session,
            request_id=request.request_id,
            request_item_id=None,
            event_type="request_created",
            payload={"item_count": len(request.items)},
        )
        _sync_request_status(session, request)
        return request.request_id, True


def _append_request_items(request: DeliveryRequest, item_payloads: list) -> int:
    next_index = max((item.item_index for item in request.items), default=-1) + 1
    existing_signatures = {_item_signature(_item_to_bib(item)) for item in request.items}
    added = 0

    for item_payload in item_payloads:
        bib = item_payload.bibliographic_data
        signature = _item_signature(bib)
        if signature in existing_signatures:
            continue

        request.items.append(
            RequestItem(
                item_index=next_index,
                title=bib.title,
                creators="; ".join(bib.creators),
                editors="; ".join(bib.editors) if bib.editors else None,
                publication_title=bib.publication_title,
                year=bib.year,
                volume=bib.volume,
                issue=bib.issue,
                pages=bib.pages,
                doi=bib.doi,
                publisher=bib.publisher,
                place=bib.place,
                series=bib.series,
                edition=bib.edition,
                isbn=bib.isbn,
                language=bib.language,
                abstract_note=bib.abstract_note,
                item_type=bib.item_type,
                raw_json=bib.model_dump_json(),
                status="PENDING_METADATA",
            )
        )
        existing_signatures.add(signature)
        next_index += 1
        added += 1

    return added


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


def get_period_statistics(granularity: str = "month", periods: int = 12) -> list[PeriodStatisticsSummary]:
    normalized_granularity = granularity.strip().lower()
    if normalized_granularity not in {"month", "year"}:
        raise ValueError("Unsupported granularity")
    capped_periods = max(1, min(periods, 60))

    now = datetime.now(timezone.utc)
    period_starts = _period_starts(now, normalized_granularity, capped_periods)
    stats_map = {
        start: {
            "request_count": 0,
            "fulfilled_requests": 0,
            "fulfillment_hours_total": 0.0,
            "valid_metadata_items": set(),
            "invalid_metadata_items": set(),
            "clarification_requests": set(),
            "reused_items": set(),
        }
        for start in period_starts
    }

    with session_scope() as session:
        requests = list(
            session.scalars(
                select(DeliveryRequest)
                .where(DeliveryRequest.created_at >= period_starts[0])
                .options(selectinload(DeliveryRequest.items))
                .order_by(DeliveryRequest.created_at.asc())
            )
        )
        request_ids = [request.request_id for request in requests]
        events = []
        if request_ids:
            events = list(
                session.scalars(
                    select(JobEvent)
                    .where(JobEvent.request_id.in_(request_ids))
                    .order_by(JobEvent.created_at.asc())
                )
            )

    events_by_request: dict[str, list[JobEvent]] = {}
    for event in events:
        events_by_request.setdefault(event.request_id, []).append(event)

    for request in requests:
        period_start = _bucket_start(request.created_at, normalized_granularity)
        bucket = stats_map.get(period_start)
        if bucket is None:
            continue
        bucket["request_count"] += 1
        if request.notification_sent_at:
            bucket["fulfilled_requests"] += 1
            bucket["fulfillment_hours_total"] += max(
                0.0,
                (request.notification_sent_at - request.created_at).total_seconds() / 3600,
            )

        for event in events_by_request.get(request.request_id, []):
            payload = _parse_event_payload(event.payload_json)
            item_key = event.request_item_id
            if event.event_type == "resolution_selected" and item_key is not None:
                if payload.get("auto_accept") is True:
                    bucket["valid_metadata_items"].add(item_key)
                elif payload.get("auto_accept") is False:
                    bucket["invalid_metadata_items"].add(item_key)
            elif event.event_type == "clarification_requested" and item_key is not None:
                bucket["clarification_requests"].add(item_key)
            elif event.event_type == "zotero_existing_item_matched" and item_key is not None:
                bucket["reused_items"].add(item_key)

    summaries: list[PeriodStatisticsSummary] = []
    for start in period_starts:
        bucket = stats_map[start]
        request_count = bucket["request_count"]
        fulfilled_requests = bucket["fulfilled_requests"]
        summaries.append(
            PeriodStatisticsSummary(
                period_start=start,
                period_label=_period_label(start, normalized_granularity),
                request_count=request_count,
                fulfilled_requests=fulfilled_requests,
                fulfillment_rate=(fulfilled_requests / request_count) if request_count else 0.0,
                avg_fulfillment_hours=(
                    round(bucket["fulfillment_hours_total"] / fulfilled_requests, 2)
                    if fulfilled_requests
                    else None
                ),
                valid_metadata_items=len(bucket["valid_metadata_items"]),
                invalid_metadata_items=len(bucket["invalid_metadata_items"]),
                clarification_requests=len(bucket["clarification_requests"]),
                reused_items=len(bucket["reused_items"]),
            )
        )
    return summaries


def list_email_templates() -> list[EmailTemplateSummary]:
    from app.models import EmailTemplate
    from app.templates import DEFAULT_EMAIL_TEMPLATES

    templates: list[EmailTemplateSummary] = []
    with session_scope() as session:
        stored = {
            template.language: template
            for template in session.scalars(select(EmailTemplate).order_by(EmailTemplate.language.asc()))
        }
        for language in ("de", "en", "pl"):
            template = stored.get(language)
            if template:
                templates.append(
                    EmailTemplateSummary(
                        template_kind="delivery",
                        language=template.language,
                        subject_template=template.subject_template,
                        body_text_template=template.body_text_template,
                        body_html_template=template.body_html_template,
                        updated_at=template.updated_at,
                    )
                )
            else:
                default_template = DEFAULT_EMAIL_TEMPLATES[language]
                templates.append(
                    EmailTemplateSummary(
                        template_kind="delivery",
                        language=language,
                        subject_template=default_template["subject_template"],
                        body_text_template=default_template["body_text_template"],
                        body_html_template=default_template["body_html_template"],
                        updated_at=None,
                    )
                )
    return templates


def update_email_template(language: str, payload: UpdateEmailTemplateRequest) -> EmailTemplateSummary:
    from app.models import EmailTemplate

    normalized_language = language.strip().lower()
    if normalized_language not in {"de", "en", "pl"}:
        raise ValueError("Unsupported language")

    with session_scope() as session:
        template = session.scalar(select(EmailTemplate).where(EmailTemplate.language == normalized_language))
        if template is None:
            template = EmailTemplate(language=normalized_language)
            session.add(template)
        template.subject_template = payload.subject_template
        template.body_text_template = payload.body_text_template
        template.body_html_template = payload.body_html_template
        session.flush()
        return EmailTemplateSummary(
            template_kind="delivery",
            language=template.language,
            subject_template=template.subject_template,
            body_text_template=template.body_text_template,
            body_html_template=template.body_html_template,
            updated_at=template.updated_at,
        )


def list_clarification_templates() -> list[EmailTemplateSummary]:
    from app.models import ClarificationTemplate
    from app.templates import DEFAULT_CLARIFICATION_TEMPLATES

    templates: list[EmailTemplateSummary] = []
    with session_scope() as session:
        stored = {
            template.language: template
            for template in session.scalars(select(ClarificationTemplate).order_by(ClarificationTemplate.language.asc()))
        }
        for language in ("de", "en", "pl"):
            template = stored.get(language)
            if template:
                templates.append(
                    EmailTemplateSummary(
                        template_kind="clarification",
                        language=template.language,
                        subject_template=template.subject_template,
                        body_text_template=template.body_text_template,
                        body_html_template=template.body_html_template,
                        updated_at=template.updated_at,
                    )
                )
            else:
                default_template = DEFAULT_CLARIFICATION_TEMPLATES[language]
                templates.append(
                    EmailTemplateSummary(
                        template_kind="clarification",
                        language=language,
                        subject_template=default_template["subject_template"],
                        body_text_template=default_template["body_text_template"],
                        body_html_template=default_template["body_html_template"],
                        updated_at=None,
                    )
                )
    return templates


def list_rejection_templates() -> list[EmailTemplateSummary]:
    from app.models import RejectionTemplate
    from app.templates import DEFAULT_REJECTION_TEMPLATES

    templates: list[EmailTemplateSummary] = []
    with session_scope() as session:
        stored = {
            template.language: template
            for template in session.scalars(select(RejectionTemplate).order_by(RejectionTemplate.language.asc()))
        }
        for language in ("de", "en", "pl"):
            template = stored.get(language)
            if template:
                templates.append(
                    EmailTemplateSummary(
                        template_kind="rejection",
                        language=template.language,
                        subject_template=template.subject_template,
                        body_text_template=template.body_text_template,
                        body_html_template=template.body_html_template,
                        updated_at=template.updated_at,
                    )
                )
            else:
                default_template = DEFAULT_REJECTION_TEMPLATES[language]
                templates.append(
                    EmailTemplateSummary(
                        template_kind="rejection",
                        language=language,
                        subject_template=default_template["subject_template"],
                        body_text_template=default_template["body_text_template"],
                        body_html_template=default_template["body_html_template"],
                        updated_at=None,
                    )
                )
    return templates


def update_clarification_template(language: str, payload: UpdateEmailTemplateRequest) -> EmailTemplateSummary:
    from app.models import ClarificationTemplate

    normalized_language = language.strip().lower()
    if normalized_language not in {"de", "en", "pl"}:
        raise ValueError("Unsupported language")

    with session_scope() as session:
        template = session.scalar(
            select(ClarificationTemplate).where(ClarificationTemplate.language == normalized_language)
        )
        if template is None:
            template = ClarificationTemplate(language=normalized_language)
            session.add(template)
        template.subject_template = payload.subject_template
        template.body_text_template = payload.body_text_template
        template.body_html_template = payload.body_html_template
        session.flush()
        return EmailTemplateSummary(
            template_kind="clarification",
            language=template.language,
            subject_template=template.subject_template,
            body_text_template=template.body_text_template,
            body_html_template=template.body_html_template,
            updated_at=template.updated_at,
        )


def update_rejection_template(language: str, payload: UpdateEmailTemplateRequest) -> EmailTemplateSummary:
    from app.models import RejectionTemplate

    normalized_language = language.strip().lower()
    if normalized_language not in {"de", "en", "pl"}:
        raise ValueError("Unsupported language")

    with session_scope() as session:
        template = session.scalar(select(RejectionTemplate).where(RejectionTemplate.language == normalized_language))
        if template is None:
            template = RejectionTemplate(language=normalized_language)
            session.add(template)
        template.subject_template = payload.subject_template
        template.body_text_template = payload.body_text_template
        template.body_html_template = payload.body_html_template
        session.flush()
        return EmailTemplateSummary(
            template_kind="rejection",
            language=template.language,
            subject_template=template.subject_template,
            body_text_template=template.body_text_template,
            body_html_template=template.body_html_template,
            updated_at=template.updated_at,
        )


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
                if item.uploaded_scan_path:
                    item.status = "PROCESSING_PDF"
                else:
                    item.status = "WAITING_FOR_ATTACHMENT" if item.zotero_item_key else "PENDING_METADATA"
                item.last_error = None
                item.next_poll_at = None
            elif item.status == "NEEDS_REVIEW":
                item.status = "PENDING_METADATA"
                item.last_error = None
                item.next_poll_at = None
            elif item.status == "AWAITING_USER":
                item.status = "NEEDS_REVIEW"
                item.last_error = None
                item.next_poll_at = None
            elif item.status == "READY_TO_NOTIFY":
                item.last_error = None
                item.next_poll_at = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(session, request_id=request.request_id, request_item_id=None, event_type="request_retried")
        return True


def approve_metadata_item(request_id: str, item_id: int, approval: ApproveMetadataRequest) -> bool:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False

        bib = approval.bibliographic_data
        item.title = bib.title
        item.creators = "; ".join(bib.creators)
        item.editors = "; ".join(bib.editors) if bib.editors else None
        item.publication_title = bib.publication_title
        item.year = bib.year
        item.volume = bib.volume
        item.issue = bib.issue
        item.pages = bib.pages
        item.doi = bib.doi
        item.publisher = bib.publisher
        item.place = bib.place
        item.series = bib.series
        item.edition = bib.edition
        item.isbn = bib.isbn
        item.language = bib.language
        item.abstract_note = bib.abstract_note
        item.item_type = bib.item_type
        item.normalized_json = bib.model_dump_json()
        item.metadata_source = "manual-review"
        item.normalization_confidence = "1.00"
        item.review_notes = approval.review_notes
        item.status = "PENDING_ZOTERO"
        item.last_error = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="metadata_approved",
            payload={"review_notes": approval.review_notes},
        )
        return True


def request_item_clarification(request_id: str, item_id: int, payload: RequestClarificationRequest) -> bool:
    operator_message = payload.operator_message.strip()
    if not operator_message:
        raise ValueError("A clarification message is required.")

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        if item.status not in {"NEEDS_REVIEW", "AWAITING_USER"}:
            raise ValueError("Clarification can only be requested for items in review.")

        token, expires_at = build_clarification_token(request.request_id, item.id)
        clarification_url = build_clarification_form_url(
            request.request_id,
            item.id,
            token,
            _item_to_bib(item),
            operator_message,
        )
        notification_payload = ClarificationNotificationPayload(
            request_id=request.request_id,
            formcycle_submission_id=request.formcycle_submission_id,
            item_id=item.id,
            user_email=request.user_email,
            user_name=request.user_name,
            language=request.form_language,
            operator_message=operator_message,
            clarification_url=clarification_url,
        )

    NotificationClient().send_clarification_request(notification_payload)

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        item.status = "AWAITING_USER"
        item.last_error = None
        item.next_poll_at = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="clarification_requested",
            payload={
                "expires_at": expires_at.isoformat(),
                "message": operator_message,
            },
        )
        return True


def reject_request_item(request_id: str, item_id: int, payload: RejectRequestItemRequest) -> bool:
    rejection_reason = payload.rejection_reason.strip()
    if not rejection_reason:
        raise ValueError("A rejection reason is required.")

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        if item.status in {"DELIVERED", "REJECTED"}:
            raise ValueError("Item is already finalized.")

        notification_payload = RejectionNotificationPayload(
            request_id=request.request_id,
            formcycle_submission_id=request.formcycle_submission_id,
            item_id=item.id,
            user_email=request.user_email,
            user_name=request.user_name,
            language=request.form_language,
            item_title=item.title,
            item_description=_format_citation(_item_to_bib(item)),
            rejection_reason=rejection_reason,
        )

    NotificationClient().send_rejection(notification_payload)

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        item.status = "REJECTED"
        item.last_error = None
        item.next_poll_at = None
        item.review_notes = _append_rejection_note(item.review_notes, rejection_reason)
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="item_rejected",
            payload={"reason": rejection_reason},
        )
        return True


def ingest_clarification_response(payload: FormCycleClarificationResponse) -> bool:
    if not verify_clarification_token(payload.request_id, payload.item_id, payload.token):
        raise ValueError("Invalid or expired clarification token.")

    user_note = (payload.user_note or "").strip()
    corrected_bib = payload.bibliographic_data

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == payload.request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == payload.item_id), None)
        if not item:
            return False

        item.title = corrected_bib.title
        item.creators = "; ".join(corrected_bib.creators)
        item.editors = "; ".join(corrected_bib.editors) if corrected_bib.editors else None
        item.publication_title = corrected_bib.publication_title
        item.year = corrected_bib.year
        item.volume = corrected_bib.volume
        item.issue = corrected_bib.issue
        item.pages = corrected_bib.pages
        item.doi = corrected_bib.doi
        item.publisher = corrected_bib.publisher
        item.place = corrected_bib.place
        item.series = corrected_bib.series
        item.edition = corrected_bib.edition
        item.isbn = corrected_bib.isbn
        item.language = corrected_bib.language
        item.abstract_note = corrected_bib.abstract_note
        item.item_type = corrected_bib.item_type
        item.raw_json = corrected_bib.model_dump_json()
        item.normalized_json = None
        item.resolution_json = None
        item.metadata_source = "user-clarification"
        item.normalization_confidence = None
        if user_note:
            item.review_notes = _append_clarification_note(item.review_notes, user_note)
        item.status = "PENDING_METADATA"
        item.last_error = None
        item.next_poll_at = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="clarification_received",
            payload={
                "user_note": user_note or None,
                "operator_message": payload.operator_message,
                "bibliographic_data": corrected_bib.model_dump(mode="json"),
                "requeued_for_resolution": True,
            },
        )
        return True


def upload_scan_for_item(request_id: str, item_id: int, filename: str, pdf_bytes: bytes) -> bool:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        if not item.zotero_item_key:
            raise ValueError("The Zotero item must exist before a scan can be uploaded.")
        if item.status == "DELIVERED":
            raise ValueError("Delivered items cannot accept a replacement scan.")

        relative_path = _uploaded_scan_relative_path(request_id, item.id)
        destination = Path(settings.work_dir) / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(pdf_bytes)

        item.uploaded_scan_path = relative_path
        item.uploaded_scan_filename = _sanitize_upload_filename(filename)
        item.status = "PROCESSING_PDF"
        item.last_error = None
        item.next_poll_at = None
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="scan_uploaded",
            payload={
                "filename": item.uploaded_scan_filename,
                "stored_path": relative_path,
                "bytes": len(pdf_bytes),
            },
        )
        return True


def remove_uploaded_scan_for_item(request_id: str, item_id: int) -> bool:
    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return False
        item = next((candidate for candidate in request.items if candidate.id == item_id), None)
        if not item:
            return False
        if not item.uploaded_scan_path:
            raise ValueError("No uploaded scan is stored for this item.")
        if item.status == "DELIVERED":
            raise ValueError("Delivered items cannot remove the uploaded scan.")

        scan_path = Path(settings.work_dir) / item.uploaded_scan_path
        if scan_path.exists():
            scan_path.unlink()

        removed_filename = item.uploaded_scan_filename
        item.uploaded_scan_path = None
        item.uploaded_scan_filename = None
        item.last_error = None
        item.next_poll_at = None
        if item.zotero_attachment_key:
            item.status = "PROCESSING_PDF"
        elif item.zotero_item_key:
            item.status = "WAITING_FOR_ATTACHMENT"
            item.next_poll_at = _next_poll_time()
        else:
            item.status = "PENDING_METADATA"
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=item.id,
            event_type="scan_removed",
            payload={"filename": removed_filename},
        )
        return True


def process_next_item() -> bool:
    snapshot = _claim_next_item_snapshot()
    if not snapshot:
        return False

    status = snapshot["status"]
    try:
        if status == "PENDING_METADATA":
            _process_metadata_stage(snapshot)
            _attempt_request_notification(snapshot["request_id"], snapshot["item_id"])
        elif status == "PENDING_ZOTERO":
            _process_zotero_stage(snapshot)
            _attempt_request_notification(snapshot["request_id"], snapshot["item_id"])
        elif status == "WAITING_FOR_ATTACHMENT":
            _process_attachment_stage(snapshot)
            _attempt_request_notification(snapshot["request_id"], snapshot["item_id"])
        elif status == "PROCESSING_PDF":
            _process_delivery_stage(snapshot)
            _attempt_request_notification(snapshot["request_id"], snapshot["item_id"])
        elif status == "READY_TO_NOTIFY":
            _process_notification_stage(snapshot)
        else:
            _update_item(snapshot["item_id"], status="FAILED", last_error=f"Unsupported status {status}")
        return True
    except Exception as exc:
        _mark_item_failed(snapshot, exc)
        return True


def build_request_summary(request: DeliveryRequest) -> RequestSummary:
    items = [
        RequestItemSummary(
            id=item.id,
            item_index=item.item_index,
            item_type=item.item_type,
            title=item.title,
            creators=item.creators,
            editors=item.editors,
            publication_title=item.publication_title,
            year=item.year,
            volume=item.volume,
            issue=item.issue,
            pages=item.pages,
            doi=item.doi,
            publisher=item.publisher,
            place=item.place,
            series=item.series,
            edition=item.edition,
            isbn=item.isbn,
            status=item.status,
            metadata_source=item.metadata_source,
            normalization_confidence=item.normalization_confidence,
            zotero_item_key=item.zotero_item_key,
            zotero_attachment_key=item.zotero_attachment_key,
            uploaded_scan_filename=item.uploaded_scan_filename,
            download_url=item.download_url,
            expires_on=item.expires_on,
            last_error=item.last_error,
            review_notes=item.review_notes,
            raw_json=item.raw_json,
            normalized_json=item.normalized_json,
            resolution_json=item.resolution_json,
            updated_at=item.updated_at,
        )
        for item in request.items
    ]
    return RequestSummary(
        request_id=request.request_id,
        formcycle_submission_id=request.formcycle_submission_id,
        user_email=request.user_email,
        user_name=request.user_name,
        language=request.form_language,
        status=request.status,
        delivery_days=request.delivery_days,
        last_error=request.last_error,
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
    status_priority = case(
        (RequestItem.status == "PENDING_METADATA", 1),
        (RequestItem.status == "PENDING_ZOTERO", 2),
        (RequestItem.status == "WAITING_FOR_ATTACHMENT", 3),
        (RequestItem.status == "PROCESSING_PDF", 4),
        (RequestItem.status == "READY_TO_NOTIFY", 5),
        else_=99,
    )
    with session_scope() as session:
        item = session.scalar(
            select(RequestItem)
            .join(RequestItem.request)
            .options(joinedload(RequestItem.request))
            .where(
                (RequestItem.status == "PENDING_METADATA")
                | (RequestItem.status == "PENDING_ZOTERO")
                | (
                    (RequestItem.status == "WAITING_FOR_ATTACHMENT")
                    & ((RequestItem.next_poll_at.is_(None)) | (RequestItem.next_poll_at <= now))
                )
                | (RequestItem.status == "PROCESSING_PDF")
                | (
                    (RequestItem.status == "READY_TO_NOTIFY")
                    & ((RequestItem.next_poll_at.is_(None)) | (RequestItem.next_poll_at <= now))
                )
            )
            .order_by(status_priority.asc(), RequestItem.updated_at.asc())
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
            "request_created_at": item.request.created_at,
            "request_language": item.request.form_language,
            "delivery_days": item.request.delivery_days,
            "status": item.status,
            "item_index": item.item_index,
            "title": item.title,
            "creators": item.creators,
            "editors": item.editors,
            "publication_title": item.publication_title,
            "year": item.year,
            "volume": item.volume,
            "issue": item.issue,
            "pages": item.pages,
            "doi": item.doi,
            "publisher": item.publisher,
            "place": item.place,
            "series": item.series,
            "edition": item.edition,
            "isbn": item.isbn,
            "language": item.language,
            "abstract_note": item.abstract_note,
            "item_type": item.item_type,
            "normalized_json": item.normalized_json,
            "zotero_item_key": item.zotero_item_key,
            "zotero_attachment_key": item.zotero_attachment_key,
            "uploaded_scan_path": item.uploaded_scan_path,
            "uploaded_scan_filename": item.uploaded_scan_filename,
        }


def _process_metadata_stage(snapshot: dict) -> None:
    source_bib = _snapshot_to_bib(snapshot)
    result = ResolutionService().normalize(source_bib)
    normalized_bib = result.bibliographic_data
    citation_text = _format_citation(normalized_bib)
    confidence = f"{result.confidence:.2f}"
    _log_resolution_events(snapshot["request_id"], snapshot["item_id"], result)

    if result.confidence < settings.normalization_auto_accept_threshold:
        _update_item(
            snapshot["item_id"],
            title=normalized_bib.title,
            creators="; ".join(normalized_bib.creators),
            editors="; ".join(normalized_bib.editors) if normalized_bib.editors else None,
            publication_title=normalized_bib.publication_title,
            year=normalized_bib.year,
            volume=normalized_bib.volume,
            issue=normalized_bib.issue,
            pages=normalized_bib.pages,
            doi=normalized_bib.doi,
            publisher=normalized_bib.publisher,
            place=normalized_bib.place,
            series=normalized_bib.series,
            edition=normalized_bib.edition,
            isbn=normalized_bib.isbn,
            language=normalized_bib.language,
            abstract_note=normalized_bib.abstract_note,
            metadata_source=result.source,
            normalization_confidence=confidence,
            normalized_json=normalized_bib.model_dump_json(),
            resolution_json=json.dumps(
                [evidence.model_dump(mode="json") for evidence in result.evidence],
                ensure_ascii=True,
            ),
            citation_text=citation_text,
            review_notes=result.notes,
            status="NEEDS_REVIEW",
            next_poll_at=None,
        )
        return

    _update_item(
        snapshot["item_id"],
        title=normalized_bib.title,
        creators="; ".join(normalized_bib.creators),
        editors="; ".join(normalized_bib.editors) if normalized_bib.editors else None,
        publication_title=normalized_bib.publication_title,
        year=normalized_bib.year,
        volume=normalized_bib.volume,
        issue=normalized_bib.issue,
        pages=normalized_bib.pages,
        doi=normalized_bib.doi,
        publisher=normalized_bib.publisher,
        place=normalized_bib.place,
        series=normalized_bib.series,
        edition=normalized_bib.edition,
        isbn=normalized_bib.isbn,
        language=normalized_bib.language,
        abstract_note=normalized_bib.abstract_note,
        metadata_source=result.source,
        normalization_confidence=confidence,
        normalized_json=normalized_bib.model_dump_json(),
        resolution_json=json.dumps(
            [evidence.model_dump(mode="json") for evidence in result.evidence],
            ensure_ascii=True,
        ),
        citation_text=citation_text,
        review_notes=result.notes,
        status="PENDING_ZOTERO",
        next_poll_at=None,
    )


def _process_zotero_stage(snapshot: dict) -> None:
    bib = _snapshot_to_bib(snapshot)
    zotero = ZoteroClient()
    has_uploaded_scan = _uploaded_scan_source(snapshot) is not None

    if snapshot["zotero_item_key"]:
        attachment_key = None if has_uploaded_scan else zotero.find_pdf_attachment(snapshot["zotero_item_key"])
        _update_item(
            snapshot["item_id"],
            citation_text=_format_citation(bib),
            zotero_item_key=snapshot["zotero_item_key"],
            zotero_attachment_key=attachment_key,
            status="PROCESSING_PDF" if attachment_key or has_uploaded_scan else "WAITING_FOR_ATTACHMENT",
            next_poll_at=None if attachment_key or has_uploaded_scan else _next_poll_time(),
        )
        return

    existing_item = zotero.find_existing_item(bib)

    if existing_item:
        _log_zotero_match_event(
            snapshot["request_id"],
            snapshot["item_id"],
            existing_item["key"],
            existing_item.get("score"),
            existing_item.get("reason"),
        )
        attachment_key = None if has_uploaded_scan else zotero.find_pdf_attachment(existing_item["key"])
        _update_item(
            snapshot["item_id"],
            citation_text=_format_citation(bib),
            zotero_item_key=existing_item["key"],
            zotero_attachment_key=attachment_key,
            status="PROCESSING_PDF" if attachment_key or has_uploaded_scan else "WAITING_FOR_ATTACHMENT",
            next_poll_at=None if attachment_key or has_uploaded_scan else _next_poll_time(),
        )
        return

    zotero_item_key = zotero.create_item(bib, snapshot["request_id"])
    _update_item(
        snapshot["item_id"],
        citation_text=_format_citation(bib),
        zotero_item_key=zotero_item_key,
        status="PROCESSING_PDF" if has_uploaded_scan else "WAITING_FOR_ATTACHMENT",
        next_poll_at=None if has_uploaded_scan else _next_poll_time(),
    )


def _process_attachment_stage(snapshot: dict) -> None:
    if not snapshot["zotero_item_key"]:
        raise RuntimeError("Missing Zotero item key for attachment lookup.")

    uploaded_scan = _uploaded_scan_source(snapshot)
    if uploaded_scan is not None:
        _update_item(
            snapshot["item_id"],
            status="PROCESSING_PDF",
            next_poll_at=None,
        )
        return

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
    work_dir = Path(settings.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    zotero = ZoteroClient()
    uploaded_scan = _uploaded_scan_source(snapshot)
    attachment_key = snapshot["zotero_attachment_key"]

    if uploaded_scan is not None:
        source_pdf = uploaded_scan
    else:
        if not attachment_key and snapshot["zotero_item_key"]:
            attachment_key = zotero.find_pdf_attachment(snapshot["zotero_item_key"])
        if not attachment_key:
            _update_item(
                snapshot["item_id"],
                status="WAITING_FOR_ATTACHMENT",
                next_poll_at=_next_poll_time(),
            )
            return
        source_pdf = work_dir / f"{snapshot['request_id']}-{snapshot['item_index']}.source.pdf"
        zotero.download_attachment(attachment_key, source_pdf)

    processed_pdf, ocr_result = _maybe_run_ocr(source_pdf)
    if ocr_result is not None:
        _log_ocr_event(snapshot["request_id"], snapshot["item_id"], ocr_result)

    if uploaded_scan is not None and not attachment_key:
        if snapshot["zotero_item_key"]:
            attachment_key = zotero.find_pdf_attachment(snapshot["zotero_item_key"])
        if attachment_key:
            _log_zotero_attachment_reused_event(
                snapshot["request_id"],
                snapshot["item_id"],
                attachment_key,
                "existing_parent_attachment",
            )
        else:
            attachment_key = zotero.upload_pdf_attachment(
                snapshot["zotero_item_key"],
                processed_pdf,
                title=f"{snapshot['title']} PDF",
            )
            _log_zotero_attachment_upload_event(
                snapshot["request_id"],
                snapshot["item_id"],
                attachment_key,
                processed_pdf.name,
            )

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=snapshot["delivery_days"])
    remote_filename = f"{snapshot['request_id']}-{snapshot['item_index']}.pdf"
    delivery_pdf = prepend_delivery_cover_page(
        processed_pdf,
        work_dir / f"{snapshot['request_id']}-{snapshot['item_index']}.delivery.pdf",
        request_id=snapshot["request_id"],
        item_index=snapshot["item_index"],
        bibliographic_data=_snapshot_to_bib(snapshot),
        order_date=snapshot["request_created_at"],
        delivery_date=now,
        language=snapshot.get("request_language"),
    )
    nextcloud = NextcloudClient()
    remote_path = nextcloud.upload_pdf(delivery_pdf, remote_filename)
    download_url, expires_on = nextcloud.create_share_link(remote_path, expires_at)

    _update_item(
        snapshot["item_id"],
        zotero_attachment_key=attachment_key,
        download_url=download_url,
        expires_on=expires_on,
        status="READY_TO_NOTIFY",
        next_poll_at=datetime.now(timezone.utc),
    )


def _process_notification_stage(snapshot: dict) -> None:
    _attempt_request_notification(snapshot["request_id"], snapshot["item_id"])


def _attempt_request_notification(request_id: str, request_item_id: int) -> None:
    try:
        _maybe_finalize_request(request_id)
    except Exception as exc:
        retry_at = _next_notification_retry_time()
        with session_scope() as session:
            request = session.scalar(
                select(DeliveryRequest)
                .where(DeliveryRequest.request_id == request_id)
                .options(selectinload(DeliveryRequest.items))
            )
            if not request:
                return
            request.last_error = str(exc)
            for item in request.items:
                if item.status == "READY_TO_NOTIFY":
                    item.last_error = str(exc)
                    item.next_poll_at = retry_at
            _sync_request_status(session, request)
            log_event(
                session,
                request_id=request.request_id,
                request_item_id=request_item_id,
                event_type="notification_failed",
                payload={"error": str(exc), "retry_at": retry_at.isoformat()},
                level="ERROR",
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

        if any(item.status not in {"READY_TO_NOTIFY", "DELIVERED", "REJECTED"} for item in request.items):
            return

        deliverable_items = [item for item in request.items if item.status in {"READY_TO_NOTIFY", "DELIVERED"}]
        if not deliverable_items:
            request.status = "REJECTED"
            return

        payload = DeliveryNotificationPayload(
            request_id=request.request_id,
            formcycle_submission_id=request.formcycle_submission_id,
            user_email=request.user_email,
            user_name=request.user_name,
            language=request.form_language,
            status="DELIVERED",
            items=[
                DeliveryItemPayload(
                    item_index=item.item_index,
                    citation_text=item.citation_text or _format_citation(_item_to_bib(item)),
                    download_url=item.download_url or "",
                    expires_on=item.expires_on or "",
                    zotero_item_key=item.zotero_item_key or "",
                )
                for item in deliverable_items
            ],
        )

    NotificationClient().send_delivery(payload)

    with session_scope() as session:
        request = session.scalar(
            select(DeliveryRequest)
            .where(DeliveryRequest.request_id == request_id)
            .options(selectinload(DeliveryRequest.items))
        )
        if not request:
            return
        delivered_count = 0
        rejected_count = 0
        for item in request.items:
            if item.status == "READY_TO_NOTIFY":
                item.status = "DELIVERED"
                delivered_count += 1
            elif item.status == "REJECTED":
                rejected_count += 1
        request.notification_sent_at = datetime.now(timezone.utc)
        request.last_error = None
        _sync_request_status(session, request)
        log_event(
            session,
            request_id=request.request_id,
            request_item_id=None,
            event_type="request_notified",
            payload={"item_count": delivered_count, "rejected_item_count": rejected_count},
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


def _log_resolution_events(request_id: str, request_item_id: int, result) -> None:
    with session_scope() as session:
        winner_source = result.source if result.source != "original" else None
        for evidence in result.evidence:
            payload = {
                "source": evidence.source,
                "status": evidence.status,
                "score": evidence.score,
                "explanation": evidence.explanation,
                "candidate_json": evidence.candidate_json,
            }
            log_event(
                session,
                request_id=request_id,
                request_item_id=request_item_id,
                event_type="resolution_source_evaluated",
                payload=payload,
            )

        log_event(
            session,
            request_id=request_id,
            request_item_id=request_item_id,
            event_type="resolution_selected",
            payload={
                "source": result.source,
                "confidence": result.confidence,
                "auto_accept": result.confidence >= settings.normalization_auto_accept_threshold,
                "winner_source": winner_source,
            },
        )


def _log_zotero_match_event(
    request_id: str,
    request_item_id: int,
    zotero_item_key: str,
    score: float | None,
    reason: str | None,
) -> None:
    with session_scope() as session:
        log_event(
            session,
            request_id=request_id,
        request_item_id=request_item_id,
        event_type="zotero_existing_item_matched",
            payload={
                "zotero_item_key": zotero_item_key,
                "score": score,
                "reason": reason,
            },
        )


def _log_zotero_attachment_upload_event(
    request_id: str,
    request_item_id: int,
    attachment_key: str,
    filename: str,
) -> None:
    with session_scope() as session:
        log_event(
            session,
            request_id=request_id,
            request_item_id=request_item_id,
            event_type="zotero_attachment_uploaded",
            payload={
                "zotero_attachment_key": attachment_key,
                "filename": filename,
            },
        )


def _log_zotero_attachment_reused_event(
    request_id: str,
    request_item_id: int,
    attachment_key: str,
    reason: str,
) -> None:
    with session_scope() as session:
        log_event(
            session,
            request_id=request_id,
            request_item_id=request_item_id,
            event_type="zotero_attachment_reused",
            payload={
                "zotero_attachment_key": attachment_key,
                "reason": reason,
            },
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
    elif statuses == {"REJECTED"}:
        request.status = "REJECTED"
    elif statuses == {"DELIVERED"} and request.notification_sent_at:
        request.status = "PROCESSED"
    elif statuses <= {"DELIVERED", "REJECTED"} and request.notification_sent_at:
        request.status = "PROCESSED"
    elif "FAILED" in statuses:
        request.status = "ATTENTION"
    elif request.last_error and statuses <= {"READY_TO_NOTIFY", "DELIVERED"}:
        request.status = "NOTIFY_FAILED"
    elif "AWAITING_USER" in statuses:
        request.status = "AWAITING_USER"
    elif "NEEDS_REVIEW" in statuses:
        request.status = "NEEDS_REVIEW"
    elif statuses <= {"WAITING_FOR_ATTACHMENT"}:
        request.status = "WAITING_FOR_ATTACHMENT"
    elif "READY_TO_NOTIFY" in statuses and statuses <= {"READY_TO_NOTIFY", "DELIVERED"}:
        request.status = "READY_TO_NOTIFY"
    else:
        request.status = "IN_PROGRESS"


def _append_rejection_note(existing: str | None, reason: str) -> str:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    block = f"Item rejected ({timestamp}):\n{reason}"
    return f"{existing.rstrip()}\n\n{block}" if existing and existing.strip() else block


def _snapshot_to_bib(snapshot: dict) -> BibliographicData:
    creators = [creator.strip() for creator in snapshot["creators"].split(";") if creator.strip()]
    editors = [editor.strip() for editor in (snapshot.get("editors") or "").split(";") if editor.strip()]
    return BibliographicData(
        item_type=snapshot["item_type"],
        title=snapshot["title"],
        creators=creators,
        editors=editors,
        publication_title=snapshot["publication_title"],
        year=snapshot["year"],
        volume=snapshot["volume"],
        issue=snapshot["issue"],
        pages=snapshot["pages"],
        doi=snapshot["doi"],
        publisher=snapshot.get("publisher"),
        place=snapshot.get("place"),
        series=snapshot.get("series"),
        edition=snapshot.get("edition"),
        isbn=snapshot.get("isbn"),
        language=snapshot["language"],
        abstract_note=snapshot["abstract_note"],
    )


def _item_to_bib(item: RequestItem) -> BibliographicData:
    creators = [creator.strip() for creator in item.creators.split(";") if creator.strip()]
    editors = [editor.strip() for editor in (item.editors or "").split(";") if editor.strip()]
    return BibliographicData(
        item_type=item.item_type,
        title=item.title,
        creators=creators,
        editors=editors,
        publication_title=item.publication_title,
        year=item.year,
        volume=item.volume,
        issue=item.issue,
        pages=item.pages,
        doi=item.doi,
        publisher=item.publisher,
        place=item.place,
        series=item.series,
        edition=item.edition,
        isbn=item.isbn,
        language=item.language,
        abstract_note=item.abstract_note,
    )


def _item_signature(bib: BibliographicData) -> tuple[str, ...]:
    normalized_creators = ";".join(creator.strip().casefold() for creator in bib.creators if creator.strip())
    normalized_editors = ";".join(editor.strip().casefold() for editor in bib.editors if editor.strip())
    return (
        (bib.item_type or "").strip().casefold(),
        bib.title.strip().casefold(),
        normalized_creators,
        normalized_editors,
        bib.publication_title.strip().casefold(),
        (bib.year or "").strip().casefold(),
        (bib.volume or "").strip().casefold(),
        (bib.issue or "").strip().casefold(),
        (bib.pages or "").strip().casefold(),
        (bib.doi or "").strip().casefold(),
        (bib.publisher or "").strip().casefold(),
        (bib.place or "").strip().casefold(),
        (bib.series or "").strip().casefold(),
        (bib.edition or "").strip().casefold(),
        (bib.isbn or "").strip().casefold(),
    )


def _format_citation(bib: BibliographicData) -> str:
    creators = ", ".join(bib.creators) if bib.creators else "Unknown author"
    details = f"{bib.publication_title} ({bib.year})"
    volume_issue = " ".join(part for part in [bib.volume, f"({bib.issue})" if bib.issue else None] if part)
    pages = f": {bib.pages}" if bib.pages else ""
    return " ".join(part for part in [creators + ".", bib.title + ".", details, volume_issue + pages] if part).strip()


def _next_poll_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.attachment_poll_interval_seconds)


def _next_notification_retry_time() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=settings.notification_retry_interval_seconds)


def _parse_event_payload(payload_json: str | None) -> dict:
    if not payload_json:
        return {}
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _bucket_start(value: datetime, granularity: str) -> datetime:
    normalized = value.astimezone(timezone.utc)
    if granularity == "year":
        return normalized.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return normalized.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_starts(now: datetime, granularity: str, periods: int) -> list[datetime]:
    starts: list[datetime] = []
    current = _bucket_start(now, granularity)
    for offset in reversed(range(periods)):
        if granularity == "year":
            starts.append(current.replace(year=current.year - offset))
        else:
            year = current.year
            month = current.month - offset
            while month <= 0:
                month += 12
                year -= 1
            starts.append(current.replace(year=year, month=month))
    return starts


def _period_label(start: datetime, granularity: str) -> str:
    if granularity == "year":
        return start.strftime("%Y")
    return start.strftime("%Y-%m")


def _uploaded_scan_relative_path(request_id: str, item_id: int) -> str:
    return f"uploads/{request_id}/{item_id}.source.pdf"


def _uploaded_scan_source(snapshot: dict) -> Path | None:
    relative_path = (snapshot.get("uploaded_scan_path") or "").strip()
    if not relative_path:
        return None
    path = Path(settings.work_dir) / relative_path
    if path.exists():
        return path
    raise RuntimeError(f"Uploaded scan is missing on disk: {path}")


def _sanitize_upload_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._")
    return cleaned or "scan.pdf"


def _append_clarification_note(existing: str | None, response_message: str) -> str:
    timestamp = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    note = f"User clarification ({timestamp}):\n{response_message}"
    if existing and existing.strip():
        return f"{existing.rstrip()}\n\n{note}"
    return note


def _maybe_run_ocr(source_pdf: Path) -> tuple[Path, OcrOverlayResult | None]:
    mode = (settings.ocr_mode or "off").strip().lower()
    if mode in {"", "off", "none", "disabled"}:
        return source_pdf, None
    if mode == "tesseract_overlay":
        result = create_tesseract_overlay_pdf(
            source_pdf,
            source_pdf.with_name(f"{source_pdf.stem}.ocr.pdf"),
            language=settings.ocr_language,
            dpi=settings.ocr_dpi,
            language_mode=settings.ocr_language_mode,
            detect_seed_language=settings.ocr_language_detect_seed,
            detect_sample_pages=settings.ocr_language_detect_pages,
            poppler_path=settings.ocr_poppler_path,
            tesseract_cmd=settings.ocr_tesseract_cmd,
            skip_if_text_layer=settings.ocr_skip_if_text_layer,
            text_layer_min_chars_per_page=settings.ocr_text_layer_min_chars_per_page,
            text_layer_min_page_ratio=settings.ocr_text_layer_min_page_ratio,
            text_layer_min_alpha_ratio=settings.ocr_text_layer_min_alpha_ratio,
        )
        return result.output_pdf, result
    raise RuntimeError(f"Unsupported OCR mode: {settings.ocr_mode}")


def _log_ocr_event(request_id: str, request_item_id: int, result: OcrOverlayResult) -> None:
    with session_scope() as session:
        log_event(
            session,
            request_id=request_id,
            request_item_id=request_item_id,
            event_type="ocr_skipped" if result.skipped else "ocr_applied",
            payload={
                "language_bundle": result.language_bundle,
                "detected_language": result.detected_language,
                "output_pdf": str(result.output_pdf),
                "skip_reason": result.skip_reason,
                "text_layer_avg_chars_per_page": result.text_layer_avg_chars_per_page,
                "text_layer_page_ratio": result.text_layer_page_ratio,
                "text_layer_alpha_ratio": result.text_layer_alpha_ratio,
            },
        )


def _serialize_value(value) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)
