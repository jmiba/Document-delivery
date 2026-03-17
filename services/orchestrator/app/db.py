from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope() -> Session:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    _migrate_sqlite()
    _seed_email_templates()
    _seed_clarification_templates()
    _seed_rejection_templates()


def _migrate_sqlite() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    migrations = {
        "delivery_requests": {
            "form_language": "ALTER TABLE delivery_requests ADD COLUMN form_language VARCHAR(16)",
        },
        "request_items": {
            "normalization_confidence": "ALTER TABLE request_items ADD COLUMN normalization_confidence VARCHAR(32)",
            "raw_json": "ALTER TABLE request_items ADD COLUMN raw_json TEXT",
            "review_notes": "ALTER TABLE request_items ADD COLUMN review_notes TEXT",
            "resolution_json": "ALTER TABLE request_items ADD COLUMN resolution_json TEXT",
            "uploaded_scan_path": "ALTER TABLE request_items ADD COLUMN uploaded_scan_path TEXT",
            "uploaded_scan_filename": "ALTER TABLE request_items ADD COLUMN uploaded_scan_filename TEXT",
            "editors": "ALTER TABLE request_items ADD COLUMN editors TEXT",
            "publisher": "ALTER TABLE request_items ADD COLUMN publisher TEXT",
            "place": "ALTER TABLE request_items ADD COLUMN place TEXT",
            "series": "ALTER TABLE request_items ADD COLUMN series TEXT",
            "edition": "ALTER TABLE request_items ADD COLUMN edition VARCHAR(64)",
            "isbn": "ALTER TABLE request_items ADD COLUMN isbn VARCHAR(64)",
        }
    }

    with engine.begin() as connection:
        for table_name, statements in migrations.items():
            columns = {
                row[1]
                for row in connection.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
            }
            for column_name, statement in statements.items():
                if column_name not in columns:
                    connection.execute(text(statement))


def _seed_email_templates() -> None:
    from app.models import EmailTemplate
    from app.templates import DEFAULT_EMAIL_TEMPLATES, sanitize_template_placeholders

    with SessionLocal() as session:
        for language, template in DEFAULT_EMAIL_TEMPLATES.items():
            existing = session.query(EmailTemplate).filter(EmailTemplate.language == language).one_or_none()
            if existing:
                existing.subject_template = sanitize_template_placeholders(existing.subject_template)
                existing.body_text_template = sanitize_template_placeholders(existing.body_text_template)
                existing.body_html_template = sanitize_template_placeholders(existing.body_html_template)
                continue
            session.add(
                EmailTemplate(
                    language=language,
                    subject_template=sanitize_template_placeholders(template["subject_template"]),
                    body_text_template=sanitize_template_placeholders(template["body_text_template"]),
                    body_html_template=sanitize_template_placeholders(template["body_html_template"]),
                )
            )
        session.commit()


def _seed_clarification_templates() -> None:
    from app.models import ClarificationTemplate
    from app.templates import DEFAULT_CLARIFICATION_TEMPLATES, sanitize_template_placeholders

    with SessionLocal() as session:
        for language, template in DEFAULT_CLARIFICATION_TEMPLATES.items():
            existing = (
                session.query(ClarificationTemplate)
                .filter(ClarificationTemplate.language == language)
                .one_or_none()
            )
            if existing:
                existing.subject_template = sanitize_template_placeholders(existing.subject_template)
                existing.body_text_template = sanitize_template_placeholders(existing.body_text_template)
                existing.body_html_template = sanitize_template_placeholders(existing.body_html_template)
                continue
            session.add(
                ClarificationTemplate(
                    language=language,
                    subject_template=sanitize_template_placeholders(template["subject_template"]),
                    body_text_template=sanitize_template_placeholders(template["body_text_template"]),
                    body_html_template=sanitize_template_placeholders(template["body_html_template"]),
                )
            )
        session.commit()


def _seed_rejection_templates() -> None:
    from app.models import RejectionTemplate
    from app.templates import DEFAULT_REJECTION_TEMPLATES, sanitize_template_placeholders

    with SessionLocal() as session:
        for language, template in DEFAULT_REJECTION_TEMPLATES.items():
            existing = session.query(RejectionTemplate).filter(RejectionTemplate.language == language).one_or_none()
            if existing:
                existing.subject_template = sanitize_template_placeholders(existing.subject_template)
                existing.body_text_template = sanitize_template_placeholders(existing.body_text_template)
                existing.body_html_template = sanitize_template_placeholders(existing.body_html_template)
                continue
            session.add(
                RejectionTemplate(
                    language=language,
                    subject_template=sanitize_template_placeholders(template["subject_template"]),
                    body_text_template=sanitize_template_placeholders(template["body_text_template"]),
                    body_html_template=sanitize_template_placeholders(template["body_html_template"]),
                )
            )
        session.commit()
