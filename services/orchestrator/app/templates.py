from __future__ import annotations

from collections.abc import Mapping


class _SafeTemplateDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "subject_template": "Ihre Dokumentlieferung ist bereit ({request_id})",
        "body_text_template": (
            "Guten Tag {greeting_name},\n\n"
            "die angeforderte Dokumentlieferung ist bereit.\n\n"
            "{items_text}\n\n"
            "{followup_text}"
            "Mit freundlichen Gruessen\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Guten Tag {greeting_name},</p>"
            "<p>die angeforderte Dokumentlieferung ist bereit.</p>"
            "{items_html}"
            "{followup_html}"
            "<p>Mit freundlichen Gruessen<br>{sender_name}</p>"
        ),
    },
    "en": {
        "subject_template": "Your document delivery is ready ({request_id})",
        "body_text_template": (
            "Hello {greeting_name},\n\n"
            "your requested document delivery is ready.\n\n"
            "{items_text}\n\n"
            "{followup_text}"
            "Kind regards\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Hello {greeting_name},</p>"
            "<p>your requested document delivery is ready.</p>"
            "{items_html}"
            "{followup_html}"
            "<p>Kind regards<br>{sender_name}</p>"
        ),
    },
    "pl": {
        "subject_template": "Twoje zamowione dokumenty sa gotowe ({request_id})",
        "body_text_template": (
            "Dzien dobry {greeting_name},\n\n"
            "zamowione materialy sa gotowe do pobrania.\n\n"
            "{items_text}\n\n"
            "{followup_text}"
            "Z powazaniem\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Dzien dobry {greeting_name},</p>"
            "<p>zamowione materialy sa gotowe do pobrania.</p>"
            "{items_html}"
            "{followup_html}"
            "<p>Z powazaniem<br>{sender_name}</p>"
        ),
    },
}


def render_template(template: str, values: Mapping[str, str]) -> str:
    return template.format_map(_SafeTemplateDict(values))
