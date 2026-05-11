from __future__ import annotations

import json
import re

from app.config import settings


_SENSITIVE_QUERY_KEYS = (
    "key",
    "api_key",
    "apikey",
    "access_token",
    "token",
    "password",
    "client_secret",
    "secret",
)
_SENSITIVE_QUERY_RE = re.compile(
    r"([?&](?:" + "|".join(re.escape(key) for key in _SENSITIVE_QUERY_KEYS) + r")=)([^&\s\"'<>]+)",
    flags=re.IGNORECASE,
)


def sanitize_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = _SENSITIVE_QUERY_RE.sub(r"\1[redacted]", text)
    for secret in _configured_secrets():
        text = text.replace(secret, "[redacted]")
    return text


def sanitize_json_text(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return sanitize_text(value)
    return json.dumps(_sanitize_json_value(parsed), ensure_ascii=False)


def _sanitize_json_value(value):
    if isinstance(value, dict):
        return {key: _sanitize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_value(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def _configured_secrets() -> list[str]:
    candidates = [
        settings.zotero_api_key,
        settings.nextcloud_password,
        settings.formcycle_webhook_secret,
        settings.clarification_token_secret,
        settings.internal_api_token,
        settings.smtp_password,
    ]
    return [secret for secret in candidates if secret and len(secret) >= 6]
