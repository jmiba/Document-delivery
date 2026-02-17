from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.clients import FormCycleClient, NextcloudClient, ZoteroClient
from app.config import settings
from app.schemas import DeliveryResult, FormCycleEvent


def _resolve_scan_path(filename: str) -> Path:
    # Limit lookup to a configured scan input directory.
    sanitized_name = Path(filename).name
    scan_path = Path(settings.scan_input_dir) / sanitized_name
    if not scan_path.exists():
        raise FileNotFoundError(f"OCR PDF not found at {scan_path}")
    return scan_path


def process_digitization_job(event_payload: dict) -> dict:
    event = FormCycleEvent.model_validate(event_payload)

    if event.status.upper() != "SCAN_COMPLETE":
        return {"request_id": event.request_id, "status": "IGNORED"}

    scan_path = _resolve_scan_path(event.ocr_pdf_filename)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.default_link_expiry_days)

    nextcloud = NextcloudClient()
    remote_filename = f"{event.request_id}.pdf"
    remote_path = nextcloud.upload_pdf(scan_path, remote_filename)
    download_url, expires_on = nextcloud.create_share_link(remote_path, expires_at)

    zotero = ZoteroClient()
    zotero_item_key = zotero.create_item(
        bib=event.bibliographic_data,
        request_id=event.request_id,
        download_url=download_url,
        expires_on=expires_on,
    )

    formcycle = FormCycleClient()
    formcycle.send_delivery(
        request_id=event.request_id,
        submission_id=event.formcycle_submission_id,
        user_email=event.user_email,
        citation=event.bibliographic_data,
        download_url=download_url,
        expires_on=expires_on,
        zotero_item_key=zotero_item_key,
    )

    result = DeliveryResult(
        request_id=event.request_id,
        status="DELIVERED",
        download_url=download_url,
        expires_on=expires_on,
        zotero_item_key=zotero_item_key,
    )
    return result.model_dump()
