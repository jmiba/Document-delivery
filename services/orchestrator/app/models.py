from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DeliveryRequest(Base):
    __tablename__ = "delivery_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    formcycle_submission_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    user_email: Mapped[str] = mapped_column(String(255))
    user_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form_language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="RECEIVED")
    delivery_days: Mapped[int] = mapped_column(Integer, default=14)
    notification_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    items: Mapped[list["RequestItem"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        order_by="RequestItem.item_index",
    )


class RequestItem(Base):
    __tablename__ = "request_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_db_id: Mapped[int] = mapped_column(ForeignKey("delivery_requests.id"), index=True)
    item_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    creators: Mapped[str] = mapped_column(Text, default="")
    editors: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_title: Mapped[str] = mapped_column(Text)
    year: Mapped[str] = mapped_column(String(32))
    volume: Mapped[str | None] = mapped_column(String(64), nullable=True)
    issue: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pages: Mapped[str | None] = mapped_column(String(64), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publisher: Mapped[str | None] = mapped_column(Text, nullable=True)
    place: Mapped[str | None] = mapped_column(Text, nullable=True)
    series: Mapped[str | None] = mapped_column(Text, nullable=True)
    edition: Mapped[str | None] = mapped_column(String(64), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(64), nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abstract_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    item_type: Mapped[str] = mapped_column(String(64), default="journalArticle")
    status: Mapped[str] = mapped_column(String(64), default="PENDING_METADATA", index=True)
    metadata_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    normalization_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    normalized_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    zotero_item_key: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    zotero_attachment_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    uploaded_scan_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_scan_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    citation_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_on: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    request: Mapped[DeliveryRequest] = relationship(back_populates="items")


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(120), index=True)
    request_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(32), default="INFO")
    event_type: Mapped[str] = mapped_column(String(128))
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    language: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    subject_template: Mapped[str] = mapped_column(Text)
    body_text_template: Mapped[str] = mapped_column(Text)
    body_html_template: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ClarificationTemplate(Base):
    __tablename__ = "clarification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    language: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    subject_template: Mapped[str] = mapped_column(Text)
    body_text_template: Mapped[str] = mapped_column(Text)
    body_html_template: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class RejectionTemplate(Base):
    __tablename__ = "rejection_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    language: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    subject_template: Mapped[str] = mapped_column(Text)
    body_text_template: Mapped[str] = mapped_column(Text)
    body_html_template: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
