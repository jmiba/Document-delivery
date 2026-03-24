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
    _seed_operator_text_templates()


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
            "pdf_page_count": "ALTER TABLE request_items ADD COLUMN pdf_page_count INTEGER",
        },
        "operator_text_template_entries": {
            "group_key": "ALTER TABLE operator_text_template_entries ADD COLUMN group_key VARCHAR(128)",
            "operator_label": "ALTER TABLE operator_text_template_entries ADD COLUMN operator_label TEXT",
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

    legacy_defaults = {
        "de": {
            "subject_template": "Rueckfrage zu Ihrer Dokumentlieferung ({request_id})",
            "body_text_template": (
                "Guten Tag {greeting_name},\n\n"
                "wir konnten die angeforderte Literaturangabe noch nicht eindeutig verifizieren.\n\n"
                "{operator_message}\n\n"
                "Bitte verwenden Sie dieses Formular:\n"
                "{clarification_url}\n\n"
                "Mit freundlichen Grüßen\n"
                "{sender_name}"
            ),
            "body_html_template": (
                "<p>Guten Tag {greeting_name},</p>"
                "<p>wir konnten die angeforderte Literaturangabe noch nicht eindeutig verifizieren.</p>"
                "<p>{operator_message_html}</p>"
                '<p>Bitte verwenden Sie dieses Formular:<br><a href="{clarification_url}">{clarification_url}</a></p>'
                "<p>Mit freundlichen Grüßen<br>{sender_name}</p>"
            ),
        },
        "en": {
            "subject_template": "Question about your document delivery request ({request_id})",
            "body_text_template": (
                "Hello {greeting_name},\n\n"
                "we could not yet verify the requested citation unambiguously.\n\n"
                "{operator_message}\n\n"
                "Please use this form:\n"
                "{clarification_url}\n\n"
                "Kind regards\n"
                "{sender_name}"
            ),
            "body_html_template": (
                "<p>Hello {greeting_name},</p>"
                "<p>we could not yet verify the requested citation unambiguously.</p>"
                "<p>{operator_message_html}</p>"
                '<p>Please use this form:<br><a href="{clarification_url}">{clarification_url}</a></p>'
                "<p>Kind regards<br>{sender_name}</p>"
            ),
        },
        "pl": {
            "subject_template": "Pytanie dotyczace zamowienia dokumentu ({request_id})",
            "body_text_template": (
                "Dzien dobry {greeting_name},\n\n"
                "nie udalo nam sie jeszcze jednoznacznie potwierdzic zamowionego opisu bibliograficznego.\n\n"
                "{operator_message}\n\n"
                "Prosze skorzystac z tego formularza:\n"
                "{clarification_url}\n\n"
                "Z powazaniem\n"
                "{sender_name}"
            ),
            "body_html_template": (
                "<p>Dzien dobry {greeting_name},</p>"
                "<p>nie udalo nam sie jeszcze jednoznacznie potwierdzic zamowionego opisu bibliograficznego.</p>"
                "<p>{operator_message_html}</p>"
                '<p>Prosze skorzystac z tego formularza:<br><a href="{clarification_url}">{clarification_url}</a></p>'
                "<p>Z powazaniem<br>{sender_name}</p>"
            ),
        },
    }

    with SessionLocal() as session:
        for language, template in DEFAULT_CLARIFICATION_TEMPLATES.items():
            existing = (
                session.query(ClarificationTemplate)
                .filter(ClarificationTemplate.language == language)
                .one_or_none()
            )
            if existing:
                legacy = legacy_defaults.get(language, {})
                if existing.subject_template == legacy.get("subject_template"):
                    existing.subject_template = sanitize_template_placeholders(template["subject_template"])
                else:
                    existing.subject_template = sanitize_template_placeholders(existing.subject_template)
                if existing.body_text_template == legacy.get("body_text_template"):
                    existing.body_text_template = sanitize_template_placeholders(template["body_text_template"])
                else:
                    existing.body_text_template = sanitize_template_placeholders(existing.body_text_template)
                if existing.body_html_template == legacy.get("body_html_template"):
                    existing.body_html_template = sanitize_template_placeholders(template["body_html_template"])
                else:
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


def _seed_operator_text_templates() -> None:
    from app.models import OperatorTextTemplateEntry
    from app.templates import DEFAULT_OPERATOR_TEXT_TEMPLATE_GROUPS

    with SessionLocal() as session:
        legacy_rows = session.scalar(
            text(
                "SELECT 1 FROM operator_text_template_entries "
                "WHERE group_key IS NULL OR operator_label IS NULL LIMIT 1"
            )
        )
        if legacy_rows:
            session.execute(text("DELETE FROM operator_text_template_entries"))
            session.flush()

        for template_kind, entries in DEFAULT_OPERATOR_TEXT_TEMPLATE_GROUPS.items():
            exists = session.scalar(
                text(
                    "SELECT 1 FROM operator_text_template_entries "
                    "WHERE template_kind = :template_kind LIMIT 1"
                ),
                {"template_kind": template_kind},
            )
            if exists:
                continue
            for index, entry in enumerate(entries):
                operator_label = str(entry.get("operator_label") or "").strip()
                group_key = str(entry.get("group_key") or f"{template_kind}_{index}").strip()
                texts = entry.get("texts") or {}
                for language, text_value in texts.items():
                    text_value = str(text_value or "").strip()
                    if not text_value:
                        continue
                    session.add(
                        OperatorTextTemplateEntry(
                            template_kind=template_kind,
                            group_key=group_key,
                            language=language,
                            operator_label=operator_label,
                            label=operator_label,
                            text_value=text_value,
                            sort_order=index,
                        )
                    )
        session.commit()
