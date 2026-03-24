from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.bibtex import parse_bibtex_entry


def _clean_person_name(value: str | None) -> str:
    return (value or "").strip()


def _normalize_person_name(value: str) -> str:
    cleaned = _clean_person_name(value)
    if not cleaned:
        return ""
    if ";" in cleaned:
        return cleaned
    if "," in cleaned:
        parts = [part.strip() for part in cleaned.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            return f"{parts[0]}, {parts[1]}"
        return cleaned
    tokens = [token for token in cleaned.split() if token]
    if len(tokens) >= 2:
        return f"{tokens[-1]}, {' '.join(tokens[:-1])}"
    return cleaned


def _looks_like_multi_author_display_string(value: str) -> bool:
    if ";" in value:
        return False
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) < 2:
        return False
    # Treat comma-separated display names as multiple authors only when each segment
    # still looks like a personal name chunk, not "Last, First".
    if any(len(part.split()) < 2 for part in parts):
        return False
    return True


def _normalize_people_list(value) -> list[str]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    normalized: list[str] = []
    for raw_item in raw_items:
        if raw_item is None:
            continue
        text = str(raw_item).strip()
        if not text:
            continue
        if _looks_like_multi_author_display_string(text):
            parts = [part.strip() for part in text.split(",") if part.strip()]
            normalized.extend(_normalize_person_name(part) for part in parts if _normalize_person_name(part))
            continue
        if ";" in text:
            normalized.extend(
                _normalize_person_name(part)
                for part in re.split(r"\s*;\s*", text)
                if _normalize_person_name(part)
            )
            continue
        normalized_name = _normalize_person_name(text)
        if normalized_name:
            normalized.append(normalized_name)
    return normalized


class BibliographicData(BaseModel):
    item_type: str = "journalArticle"
    title: str
    creators: list[str] = Field(default_factory=list)
    editors: list[str] = Field(default_factory=list)
    publication_title: str
    year: str
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    doi: str | None = None
    publisher: str | None = None
    place: str | None = None
    series: str | None = None
    edition: str | None = None
    isbn: str | None = None
    language: str | None = None
    abstract_note: str | None = None

    @field_validator("creators", "editors", mode="before")
    @classmethod
    def normalize_people(cls, value):
        return _normalize_people_list(value)


class FormCycleRequestItem(BaseModel):
    item_index: int | None = None
    bibliographic_data: BibliographicData | None = None
    bibtex: str | None = None

    @model_validator(mode="after")
    def ensure_bibliographic_data(self) -> "FormCycleRequestItem":
        if (self.bibtex or "").strip():
            self.bibliographic_data = BibliographicData(**parse_bibtex_entry(self.bibtex or ""))
            return self
        if self.bibliographic_data is None:
            raise ValueError("Either bibliographic_data or bibtex is required.")
        return self


class FormCycleRequest(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    user_email: str
    user_name: str | None = None
    language: str | None = None
    delivery_days: int | None = None
    items: list[FormCycleRequestItem] = Field(default_factory=list)
    bibliographic_data: BibliographicData | None = None
    bibtex: str | None = None

    @model_validator(mode="after")
    def ensure_items(self) -> "FormCycleRequest":
        if not self.items and (self.bibliographic_data or (self.bibtex or "").strip()):
            self.items = [
                FormCycleRequestItem(
                    item_index=0,
                    bibliographic_data=self.bibliographic_data,
                    bibtex=self.bibtex,
                )
            ]
        if not self.items:
            raise ValueError("At least one bibliographic item is required.")
        for index, item in enumerate(self.items):
            if item.item_index is None:
                item.item_index = index
        return self


class DeliveryItemPayload(BaseModel):
    item_index: int
    citation_text: str
    download_url: str
    expires_on: str
    zotero_item_key: str
    bibliographic_data: BibliographicData


class DeliveryNotificationPayload(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    user_email: str
    user_name: str | None = None
    language: str | None = None
    status: str
    items: list[DeliveryItemPayload]
    bibtex_filename: str | None = None


class ClarificationNotificationPayload(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    item_id: int
    user_email: str
    user_name: str | None = None
    language: str | None = None
    operator_message: str
    clarification_url: str


class RejectionNotificationPayload(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    item_id: int
    user_email: str
    user_name: str | None = None
    language: str | None = None
    item_title: str
    item_description: str
    rejection_reason: str


class ApproveMetadataRequest(BaseModel):
    bibliographic_data: BibliographicData
    review_notes: str | None = None


class RequestClarificationRequest(BaseModel):
    operator_message: str = Field(min_length=1)


class RejectRequestItemRequest(BaseModel):
    rejection_reason: str = Field(min_length=1)


class FormCycleClarificationResponse(BaseModel):
    request_id: str
    item_id: int
    token: str
    bibliographic_data: BibliographicData
    user_note: str | None = None
    operator_message: str | None = None


class ResolutionEvidence(BaseModel):
    source: str
    status: str
    score: float
    explanation: str
    candidate_json: str | None = None


class NormalizationResult(BaseModel):
    bibliographic_data: BibliographicData
    source: str
    confidence: float
    notes: str | None = None
    evidence: list[ResolutionEvidence] = Field(default_factory=list)


class RequestItemSummary(BaseModel):
    id: int
    item_index: int
    item_type: str
    title: str
    creators: str
    editors: str | None
    publication_title: str
    year: str
    volume: str | None
    issue: str | None
    pages: str | None
    doi: str | None
    publisher: str | None
    place: str | None
    series: str | None
    edition: str | None
    isbn: str | None
    status: str
    metadata_source: str | None
    normalization_confidence: str | None
    zotero_item_key: str | None
    zotero_attachment_key: str | None
    uploaded_scan_filename: str | None
    download_url: str | None
    expires_on: str | None
    download_deleted_at: datetime | None = None
    last_error: str | None
    review_notes: str | None
    raw_json: str | None
    normalized_json: str | None
    resolution_json: str | None
    updated_at: datetime


class RequestSummary(BaseModel):
    request_id: str
    formcycle_submission_id: str | None
    user_email: str
    user_name: str | None
    language: str | None
    status: str
    delivery_days: int
    last_error: str | None
    notification_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[RequestItemSummary]


class JobEventSummary(BaseModel):
    id: int
    request_id: str
    request_item_id: int | None
    level: str
    event_type: str
    payload_json: str | None
    created_at: datetime


class PeriodStatisticsSummary(BaseModel):
    period_start: datetime
    period_label: str
    request_count: int
    fulfilled_requests: int
    rejected_requests: int
    rejected_items: int
    fulfillment_rate: float
    rejection_rate: float
    avg_fulfillment_hours: float | None
    valid_metadata_items: int
    invalid_metadata_items: int
    clarification_requests: int
    reused_items: int


class EmailTemplateSummary(BaseModel):
    template_kind: str = "delivery"
    language: str
    subject_template: str
    body_text_template: str
    body_html_template: str
    updated_at: datetime | None = None


class UpdateEmailTemplateRequest(BaseModel):
    subject_template: str
    body_text_template: str
    body_html_template: str


class OperatorTextTemplateEntryInput(BaseModel):
    label: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ReplaceOperatorTextTemplatesRequest(BaseModel):
    entries: list[OperatorTextTemplateEntryInput] = Field(default_factory=list)


class OperatorTextTemplateEntrySummary(BaseModel):
    template_kind: str
    language: str
    label: str
    text: str
    sort_order: int


class OperatorTextTemplateGroupInput(BaseModel):
    operator_label: str = Field(min_length=1)
    text_de: str = ""
    text_en: str = ""
    text_pl: str = ""

    @model_validator(mode="after")
    def validate_texts(self):
        self.operator_label = self.operator_label.strip()
        self.text_de = self.text_de.strip()
        self.text_en = self.text_en.strip()
        self.text_pl = self.text_pl.strip()
        if not any([self.text_de, self.text_en, self.text_pl]):
            raise ValueError("At least one localized text is required.")
        return self


class ReplaceOperatorTextTemplateGroupsRequest(BaseModel):
    entries: list[OperatorTextTemplateGroupInput] = Field(default_factory=list)


class OperatorTextTemplateGroupSummary(BaseModel):
    template_kind: str
    operator_label: str
    text_de: str = ""
    text_en: str = ""
    text_pl: str = ""
    sort_order: int
