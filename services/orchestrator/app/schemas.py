from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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


class FormCycleRequestItem(BaseModel):
    item_index: int | None = None
    bibliographic_data: BibliographicData


class FormCycleRequest(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    user_email: str
    user_name: str | None = None
    language: str | None = None
    delivery_days: int | None = None
    items: list[FormCycleRequestItem] = Field(default_factory=list)
    bibliographic_data: BibliographicData | None = None

    @model_validator(mode="after")
    def ensure_items(self) -> "FormCycleRequest":
        if not self.items and self.bibliographic_data:
            self.items = [FormCycleRequestItem(item_index=0, bibliographic_data=self.bibliographic_data)]
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


class DeliveryNotificationPayload(BaseModel):
    request_id: str
    formcycle_submission_id: str | None = None
    user_email: str
    user_name: str | None = None
    language: str | None = None
    status: str
    items: list[DeliveryItemPayload]


class ApproveMetadataRequest(BaseModel):
    bibliographic_data: BibliographicData
    review_notes: str | None = None


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


class EmailTemplateSummary(BaseModel):
    language: str
    subject_template: str
    body_text_template: str
    body_html_template: str
    updated_at: datetime | None = None


class UpdateEmailTemplateRequest(BaseModel):
    subject_template: str
    body_text_template: str
    body_html_template: str
