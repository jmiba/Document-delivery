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
            "{bibtex_note_text}\n\n"
            "Mit freundlichen Grüßen\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Guten Tag {greeting_name},</p>"
            "<p>die angeforderte Dokumentlieferung ist bereit.</p>"
            "{items_html}"
            "<p>{bibtex_note_html}</p>"
            "<p>Mit freundlichen Grüßen<br>{sender_name}</p>"
        ),
    },
    "en": {
        "subject_template": "Your document delivery is ready ({request_id})",
        "body_text_template": (
            "Hello {greeting_name},\n\n"
            "your requested document delivery is ready.\n\n"
            "{items_text}\n\n"
            "{bibtex_note_text}\n\n"
            "Kind regards\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Hello {greeting_name},</p>"
            "<p>your requested document delivery is ready.</p>"
            "{items_html}"
            "<p>{bibtex_note_html}</p>"
            "<p>Kind regards<br>{sender_name}</p>"
        ),
    },
    "pl": {
        "subject_template": "Twoje zamowione dokumenty sa gotowe ({request_id})",
        "body_text_template": (
            "Dzien dobry {greeting_name},\n\n"
            "zamowione materialy sa gotowe do pobrania.\n\n"
            "{items_text}\n\n"
            "{bibtex_note_text}\n\n"
            "Z powazaniem\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Dzien dobry {greeting_name},</p>"
            "<p>zamowione materialy sa gotowe do pobrania.</p>"
            "{items_html}"
            "<p>{bibtex_note_html}</p>"
            "<p>Z powazaniem<br>{sender_name}</p>"
        ),
    },
}


DEFAULT_CLARIFICATION_TEMPLATES: dict[str, dict[str, str]] = {
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


DEFAULT_REJECTION_TEMPLATES: dict[str, dict[str, str]] = {
    "de": {
        "subject_template": "Ihre Dokumentlieferung kann nicht erfüllt werden ({request_id})",
        "body_text_template": (
            "Guten Tag {greeting_name},\n\n"
            "wir können die angefragte Literatur leider nicht liefern.\n\n"
            "Betroffener Titel:\n"
            "{item_description}\n\n"
            "Grund:\n"
            "{rejection_reason}\n\n"
            "Mit freundlichen Grüßen\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Guten Tag {greeting_name},</p>"
            "<p>wir können die angefragte Literatur leider nicht liefern.</p>"
            "<p><strong>Betroffener Titel:</strong><br>{item_description_html}</p>"
            "<p><strong>Grund:</strong><br>{rejection_reason_html}</p>"
            "<p>Mit freundlichen Grüßen<br>{sender_name}</p>"
        ),
    },
    "en": {
        "subject_template": "Your document delivery request cannot be fulfilled ({request_id})",
        "body_text_template": (
            "Hello {greeting_name},\n\n"
            "we are unable to supply the requested item.\n\n"
            "Affected item:\n"
            "{item_description}\n\n"
            "Reason:\n"
            "{rejection_reason}\n\n"
            "Kind regards\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Hello {greeting_name},</p>"
            "<p>we are unable to supply the requested item.</p>"
            "<p><strong>Affected item:</strong><br>{item_description_html}</p>"
            "<p><strong>Reason:</strong><br>{rejection_reason_html}</p>"
            "<p>Kind regards<br>{sender_name}</p>"
        ),
    },
    "pl": {
        "subject_template": "Nie mozemy zrealizowac zamowienia dokumentu ({request_id})",
        "body_text_template": (
            "Dzien dobry {greeting_name},\n\n"
            "niestety nie mozemy dostarczyc zamowionego materialu.\n\n"
            "Pozycja:\n"
            "{item_description}\n\n"
            "Powod:\n"
            "{rejection_reason}\n\n"
            "Z powazaniem\n"
            "{sender_name}"
        ),
        "body_html_template": (
            "<p>Dzien dobry {greeting_name},</p>"
            "<p>niestety nie mozemy dostarczyc zamowionego materialu.</p>"
            "<p><strong>Pozycja:</strong><br>{item_description_html}</p>"
            "<p><strong>Powod:</strong><br>{rejection_reason_html}</p>"
            "<p>Z powazaniem<br>{sender_name}</p>"
        ),
    },
}


def sanitize_template_placeholders(template: str) -> str:
    return template.replace("{followup_text}", "").replace("{followup_html}", "")


def render_template(template: str, values: Mapping[str, str]) -> str:
    return sanitize_template_placeholders(template).format_map(_SafeTemplateDict(values))
