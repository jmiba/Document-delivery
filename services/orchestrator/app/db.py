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
