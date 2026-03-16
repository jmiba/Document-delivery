from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from app.config import settings


def build_clarification_token(request_id: str, item_id: int) -> tuple[str, datetime]:
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.clarification_token_ttl_hours)
    payload = {
        "request_id": request_id,
        "item_id": item_id,
        "exp": int(expires_at.timestamp()),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}", expires_at


def verify_clarification_token(request_id: str, item_id: int, token: str) -> bool:
    try:
        encoded_payload, signature = token.split(".", 1)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _sign(encoded_payload)):
        return False
    try:
        payload = json.loads(_decode(encoded_payload))
    except Exception:
        return False
    if payload.get("request_id") != request_id or int(payload.get("item_id", -1)) != item_id:
        return False
    expires_at = int(payload.get("exp", 0))
    return expires_at >= int(datetime.now(timezone.utc).timestamp())


def build_clarification_form_url(request_id: str, item_id: int, token: str) -> str:
    template = (settings.clarification_form_url_template or "").strip()
    if not template:
        raise RuntimeError("CLARIFICATION_FORM_URL_TEMPLATE must be configured.")
    return template.format(
        request_id=request_id,
        request_id_q=quote_plus(request_id),
        item_id=item_id,
        item_id_q=quote_plus(str(item_id)),
        token=token,
        token_q=quote_plus(token),
    )


def _sign(encoded_payload: str) -> str:
    secret = (settings.clarification_token_secret or "").encode("utf-8")
    if not secret:
        raise RuntimeError("CLARIFICATION_TOKEN_SECRET must be configured.")
    return hmac.new(secret, encoded_payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
