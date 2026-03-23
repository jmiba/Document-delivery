from __future__ import annotations

import hashlib
import hmac
import json
import os

import altair as alt
import pandas as pd
import requests
import streamlit as st


API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")
PASSWORD_AUTH_SESSION_KEY = "password_auth_authenticated"
PASSWORD_AUTH_USER_KEY = "password_auth_user"

SUPPORTED_MESSAGE_LANGUAGES = {"de", "en", "pl"}
LANGUAGE_LABELS = {"de": "German", "en": "English", "pl": "Polish"}
OPERATOR_TEXT_TEMPLATE_KIND_LABELS = {
    "rejection_reason": "Rejection reasons",
    "clarification_detail": "Clarification details",
}

REJECTION_REASON_TEMPLATES = {
    "de": [
        {
            "label": "Lizenzrechtliche Bedingungen",
            "text": "Die Anfrage kann aufgrund lizenzrechtlicher Bedingungen leider nicht erfüllt werden.",
        },
        {
            "label": "Kein lokaler Print-Bestand / Fernleihe",
            "text": "Der Titel ist lokal nicht im Print-Bestand vorhanden. Bitte nutzen Sie die Fernleihe als Alternative.",
        },
        {
            "label": "Online bereits zugänglich",
            "text": "Der angefragte Titel ist bereits online zugänglich.",
        },
    ],
    "en": [
        {
            "label": "Licensing restrictions",
            "text": "Unfortunately, we cannot fulfill this request due to licensing restrictions.",
        },
        {
            "label": "No local print holdings / Interlibrary loan",
            "text": "The title is not available in our local print holdings. Please use interlibrary loan as an alternative.",
        },
        {
            "label": "Already available online",
            "text": "The requested title is already available online.",
        },
    ],
    "pl": [
        {
            "label": "Ograniczenia licencyjne",
            "text": "Niestety nie mozemy zrealizowac tej prosby ze wzgledu na ograniczenia licencyjne.",
        },
        {
            "label": "Brak lokalnego egzemplarza drukowanego / Wypozyczenie miedzybiblioteczne",
            "text": "Tytul nie jest dostepny w lokalnym zbiorze drukowanym. Prosze skorzystac z wypozyczenia miedzybibliotecznego jako alternatywy.",
        },
        {
            "label": "Pozycja dostepna online",
            "text": "Zamawiany tytul jest juz dostepny online.",
        },
    ],
}

CLARIFICATION_DETAIL_TEMPLATES = {
    "de": [
        {
            "label": "Autorennamen prüfen",
            "text": "Bitte überprüfen Sie die Autorennamen.",
        },
        {
            "label": "Titel prüfen",
            "text": "Bitte überprüfen Sie den Titel.",
        },
        {
            "label": "Seitenzahlen/weitere Angaben prüfen",
            "text": "Bitte überprüfen Sie die Seitenzahlen oder ergänzen Sie weitere Angaben zur sicheren Identifikation des gewünschten Titels.",
        },
    ],
    "en": [
        {
            "label": "Verify author names",
            "text": "Please verify the author names.",
        },
        {
            "label": "Verify title",
            "text": "Please verify the title.",
        },
        {
            "label": "Verify pages/other identifying details",
            "text": "Please verify the page numbers or provide additional details so we can identify the requested title reliably.",
        },
    ],
    "pl": [
        {
            "label": "Sprawdz nazwiska autorow",
            "text": "Prosze sprawdzic nazwiska autorow.",
        },
        {
            "label": "Sprawdz tytul",
            "text": "Prosze sprawdzic tytul.",
        },
        {
            "label": "Sprawdz strony/inne dane identyfikacyjne",
            "text": "Prosze sprawdzic numery stron lub podac dodatkowe informacje, aby mozna bylo jednoznacznie zidentyfikowac zamawiany tytul.",
        },
    ],
}


def _auth_settings() -> dict:
    try:
        auth_settings = st.secrets.get("auth", {})
    except Exception:
        return {}
    if hasattr(auth_settings, "to_dict"):
        auth_settings = auth_settings.to_dict()
    return auth_settings if isinstance(auth_settings, dict) else {}


def _password_auth_settings() -> dict:
    try:
        password_settings = st.secrets.get("password_auth", {})
    except Exception:
        return {}
    if hasattr(password_settings, "to_dict"):
        password_settings = password_settings.to_dict()
    return password_settings if isinstance(password_settings, dict) else {}


def _password_auth_enabled() -> bool:
    password_settings = _password_auth_settings()
    if password_settings.get("enabled") is False:
        return False
    return bool(password_settings.get("username")) and bool(
        password_settings.get("password_sha256") or password_settings.get("password")
    )


def _password_auth_username() -> str:
    return str(_password_auth_settings().get("username") or "").strip()


def _password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _password_matches(password: str) -> bool:
    settings = _password_auth_settings()
    expected_hash = str(settings.get("password_sha256") or "").strip().lower()
    if expected_hash:
        return hmac.compare_digest(_password_hash(password), expected_hash)
    expected_password = str(settings.get("password") or "")
    return bool(expected_password) and hmac.compare_digest(password, expected_password)


def _is_password_authenticated() -> bool:
    return bool(st.session_state.get(PASSWORD_AUTH_SESSION_KEY))


def _password_logout() -> None:
    st.session_state.pop(PASSWORD_AUTH_SESSION_KEY, None)
    st.session_state.pop(PASSWORD_AUTH_USER_KEY, None)


def _auth_enabled() -> bool:
    auth_settings = _auth_settings()
    return bool(auth_settings.get("redirect_uri") and auth_settings.get("cookie_secret"))


def _auth_provider() -> str | None:
    provider = _auth_settings().get("provider")
    if isinstance(provider, str) and provider.strip():
        return provider.strip()
    return None


def _login() -> None:
    provider = _auth_provider()
    if provider:
        st.login(provider)
    else:
        st.login()


def _current_user_label() -> str:
    if _password_auth_enabled() and _is_password_authenticated():
        return str(st.session_state.get(PASSWORD_AUTH_USER_KEY) or _password_auth_username() or "Authenticated user")
    if hasattr(st.user, "name") and st.user.name:
        return str(st.user.name)
    if hasattr(st.user, "email") and st.user.email:
        return str(st.user.email)
    if hasattr(st.user, "sub") and st.user.sub:
        return str(st.user.sub)
    return "Authenticated user"


def _require_authentication() -> None:
    if _password_auth_enabled():
        if _is_password_authenticated():
            return
        st.title("Document Delivery Ops")
        st.caption("Authentication required")
        with st.form("password-auth-form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
        if submitted:
            if (
                hmac.compare_digest(username.strip(), _password_auth_username())
                and _password_matches(password)
            ):
                st.session_state[PASSWORD_AUTH_SESSION_KEY] = True
                st.session_state[PASSWORD_AUTH_USER_KEY] = _password_auth_username()
                st.rerun()
            st.error("Invalid username or password.")
        st.stop()
    if not _auth_enabled():
        return
    if getattr(st.user, "is_logged_in", False):
        return
    st.title("Document Delivery Ops")
    st.caption("Authentication required")
    st.info("Sign in to access the operator interface.")
    st.button(
        "Log in",
        type="primary",
        use_container_width=True,
        icon=":material/login:",
        on_click=_login,
    )
    st.stop()


def _parse_json_object(payload: str | None) -> dict | None:
    if not payload:
        return None
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _creators_to_string(value) -> str:
    if isinstance(value, list):
        return "; ".join(str(part).strip() for part in value if str(part).strip())
    return str(value or "").strip()


def _request_language(request: dict) -> str:
    language = str(request.get("language") or "").strip().lower()
    if language in SUPPORTED_MESSAGE_LANGUAGES:
        return language
    return "de"


def _compose_message(
    selected_labels: list[str],
    templates: list[dict[str, str]],
    free_text: str,
) -> str:
    text_by_label = {template["label"]: template["text"] for template in templates}
    selected_texts = [text_by_label[label] for label in selected_labels if label in text_by_label]
    free_text_clean = free_text.strip()
    if free_text_clean:
        selected_texts.append(free_text_clean)
    return "\n\n".join(part for part in selected_texts if part.strip())


def _human_readable_bib_rows(payload: dict) -> list[dict]:
    seed = _review_seed_from_bib(payload)
    labels = [
        ("item_type", "Item type"),
        ("title", "Title"),
        ("creators", "Creators"),
        ("editors", "Editors"),
        ("publication_title", "Publication"),
        ("year", "Year"),
        ("volume", "Volume"),
        ("issue", "Issue"),
        ("pages", "Pages"),
        ("doi", "DOI"),
        ("publisher", "Publisher"),
        ("place", "Place"),
        ("series", "Series"),
        ("edition", "Edition"),
        ("isbn", "ISBN"),
    ]
    return [
        {"Field": label, "Value": seed.get(key) or ""}
        for key, label in labels
        if seed.get(key) not in (None, "")
    ]


def _render_bib_payload(label: str, payload: dict | None, key: str) -> None:
    st.caption(label)
    if not payload:
        st.write("No payload stored")
        return
    show_raw = st.toggle("Show raw JSON", value=False, key=key)
    if show_raw:
        st.code(json.dumps(payload, ensure_ascii=False, indent=2), language="json")
        return
    rows = _human_readable_bib_rows(payload)
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_key_value_payload(label: str, payload: dict, key: str) -> None:
    st.caption(label)
    show_raw = st.toggle("Show raw JSON", value=False, key=key)
    if show_raw:
        st.code(json.dumps(payload, ensure_ascii=False, indent=2), language="json")
        return
    rows = [{"Field": field, "Value": value} for field, value in payload.items()]
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _payloads_effectively_equal(left: dict | None, right: dict | None) -> bool:
    if not left or not right:
        return False
    return _review_seed_from_bib(left) == _review_seed_from_bib(right)


def _review_seed_from_item(item: dict) -> dict:
    return {
        "item_type": item.get("item_type") or "journalArticle",
        "title": item.get("title") or "",
        "creators": item.get("creators") or "",
        "editors": item.get("editors") or "",
        "publication_title": item.get("publication_title") or "",
        "year": item.get("year") or "",
        "volume": item.get("volume") or "",
        "issue": item.get("issue") or "",
        "pages": item.get("pages") or "",
        "doi": item.get("doi") or "",
        "publisher": item.get("publisher") or "",
        "place": item.get("place") or "",
        "series": item.get("series") or "",
        "edition": item.get("edition") or "",
        "isbn": item.get("isbn") or "",
    }


def _review_seed_from_bib(payload: dict) -> dict:
    return {
        "item_type": payload.get("item_type") or "journalArticle",
        "title": payload.get("title") or "",
        "creators": _creators_to_string(payload.get("creators")),
        "editors": _creators_to_string(payload.get("editors")),
        "publication_title": payload.get("publication_title") or "",
        "year": payload.get("year") or "",
        "volume": payload.get("volume") or "",
        "issue": payload.get("issue") or "",
        "pages": payload.get("pages") or "",
        "doi": payload.get("doi") or "",
        "publisher": payload.get("publisher") or "",
        "place": payload.get("place") or "",
        "series": payload.get("series") or "",
        "edition": payload.get("edition") or "",
        "isbn": payload.get("isbn") or "",
    }


def _approval_payload_from_seed(seed: dict, review_notes: str | None = None) -> dict:
    creators = [part.strip() for part in (seed.get("creators") or "").split(";") if part.strip()]
    editors = [part.strip() for part in (seed.get("editors") or "").split(";") if part.strip()]
    return {
        "bibliographic_data": {
            "item_type": seed.get("item_type") or "journalArticle",
            "title": seed.get("title") or "",
            "creators": creators,
            "editors": editors,
            "publication_title": seed.get("publication_title") or "",
            "year": seed.get("year") or "",
            "volume": seed.get("volume") or None,
            "issue": seed.get("issue") or None,
            "pages": seed.get("pages") or None,
            "doi": seed.get("doi") or None,
            "publisher": seed.get("publisher") or None,
            "place": seed.get("place") or None,
            "series": seed.get("series") or None,
            "edition": seed.get("edition") or None,
            "isbn": seed.get("isbn") or None,
        },
        "review_notes": review_notes or None,
    }


def _headers() -> dict[str, str]:
    headers = {}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN
    return headers


def fetch_requests() -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/requests", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_email_templates() -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/email-templates", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_clarification_templates() -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/clarification-templates", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_rejection_templates() -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/rejection-templates", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_operator_text_templates(template_kind: str, language: str) -> list[dict]:
    response = requests.get(
        f"{API_BASE_URL}/operator-text-templates/{template_kind}/{language}",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    return [{"label": row.get("label", ""), "text": row.get("text", "")} for row in payload]


def save_operator_text_templates(template_kind: str, language: str, entries: list[dict]) -> list[dict]:
    response = requests.put(
        f"{API_BASE_URL}/operator-text-templates/{template_kind}/{language}",
        headers={**_headers(), "Content-Type": "application/json"},
        json={"entries": entries},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def save_email_template(language: str, payload: dict, template_kind: str = "delivery") -> dict:
    if template_kind == "delivery":
        base_path = "/email-templates"
    elif template_kind == "clarification":
        base_path = "/clarification-templates"
    else:
        base_path = "/rejection-templates"
    response = requests.put(
        f"{API_BASE_URL}{base_path}/{language}",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_request(request_id: str) -> dict:
    response = requests.get(f"{API_BASE_URL}/requests/{request_id}", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_events(request_id: str) -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/requests/{request_id}/events", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_statistics(granularity: str, periods: int) -> list[dict]:
    response = requests.get(
        f"{API_BASE_URL}/statistics",
        headers=_headers(),
        params={"granularity": granularity, "periods": periods},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def retry_request(request_id: str) -> None:
    response = requests.post(f"{API_BASE_URL}/requests/{request_id}/retry", headers=_headers(), timeout=30)
    response.raise_for_status()


def approve_item(request_id: str, item_id: int, payload: dict) -> None:
    response = requests.post(
        f"{API_BASE_URL}/requests/{request_id}/items/{item_id}/approve",
        headers={**_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def request_clarification(request_id: str, item_id: int, operator_message: str) -> None:
    response = requests.post(
        f"{API_BASE_URL}/requests/{request_id}/items/{item_id}/clarification-request",
        headers={**_headers(), "Content-Type": "application/json"},
        json={"operator_message": operator_message},
        timeout=30,
    )
    response.raise_for_status()


def reject_item(request_id: str, item_id: int, rejection_reason: str) -> None:
    response = requests.post(
        f"{API_BASE_URL}/requests/{request_id}/items/{item_id}/reject",
        headers={**_headers(), "Content-Type": "application/json"},
        json={"rejection_reason": rejection_reason},
        timeout=30,
    )
    response.raise_for_status()


def upload_scan(request_id: str, item_id: int, uploaded_file) -> None:
    response = requests.post(
        f"{API_BASE_URL}/requests/{request_id}/items/{item_id}/scan",
        headers=_headers(),
        files={
            "file": (
                uploaded_file.name,
                uploaded_file.getvalue(),
                uploaded_file.type or "application/pdf",
            )
        },
        timeout=300,
    )
    response.raise_for_status()


def remove_scan(request_id: str, item_id: int) -> None:
    response = requests.delete(
        f"{API_BASE_URL}/requests/{request_id}/items/{item_id}/scan",
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()


def _attachment_state(item: dict) -> str:
    if item.get("uploaded_scan_filename"):
        return f"App upload: {item['uploaded_scan_filename']}"
    if item.get("zotero_attachment_key"):
        return "Zotero attachment"
    return "Waiting"


def _attachment_timeline_rows(request: dict, events: list[dict]) -> list[dict]:
    interesting = {
        "scan_uploaded": "Scan uploaded",
        "scan_removed": "Scan removed",
        "zotero_attachment_uploaded": "Attachment uploaded to Zotero",
        "zotero_attachment_reused": "Existing Zotero attachment reused",
    }
    items_by_id = {item["id"]: item for item in request["items"]}
    rows: list[dict] = []
    for event in events:
        event_type = event.get("event_type")
        if event_type not in interesting:
            continue
        payload = _parse_json_object(event.get("payload_json")) or {}
        item = items_by_id.get(event.get("request_item_id"))
        item_label = (
            f"#{item['item_index']} {item['title']}"
            if item
            else f"Item {event.get('request_item_id')}"
        )
        details = ""
        if event_type == "scan_uploaded":
            details = payload.get("filename") or ""
        elif event_type == "scan_removed":
            details = payload.get("filename") or ""
        elif event_type in {"zotero_attachment_uploaded", "zotero_attachment_reused"}:
            attachment_key = payload.get("zotero_attachment_key") or ""
            reason = payload.get("reason") or ""
            filename = payload.get("filename") or ""
            details = " | ".join(part for part in [filename, attachment_key, reason] if part)
        rows.append(
            {
                "created_at": event.get("created_at"),
                "item": item_label,
                "action": interesting[event_type],
                "details": details,
            }
        )
    return rows


def _latest_user_clarification(events: list[dict], item_id: int) -> dict | None:
    for event in events:
        if event.get("request_item_id") != item_id:
            continue
        if event.get("event_type") != "clarification_received":
            continue
        payload = _parse_json_object(event.get("payload_json")) or {}
        user_note = (payload.get("user_note") or "").strip()
        if not user_note:
            continue
        return {
            "created_at": event.get("created_at"),
            "user_note": user_note,
            "operator_message": payload.get("operator_message") or "",
        }
    return None


def _query_request_id() -> str | None:
    value = st.query_params.get("request_id")
    if isinstance(value, list):
        return value[0] if value else None
    return str(value) if value else None


def _render_bar_chart(
    rows: list[dict],
    x_field: str,
    y_field: str,
    title: str,
    color: str,
    x_title: str = "Period",
    y_title: str | None = None,
) -> None:
    dataframe = pd.DataFrame(rows)
    chart = (
        alt.Chart(dataframe)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2, color=color)
        .encode(
            x=alt.X(f"{x_field}:N", sort=None, axis=alt.Axis(labelAngle=-35), title=x_title),
            y=alt.Y(f"{y_field}:Q", title=y_title),
            tooltip=[
                alt.Tooltip(f"{x_field}:N", title=x_title),
                alt.Tooltip(f"{y_field}:Q", title=y_title or title),
            ],
        )
        .properties(title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_line_chart(
    rows: list[dict],
    x_field: str,
    y_field: str,
    title: str,
    color: str,
    x_title: str = "Period",
    y_title: str | None = None,
) -> None:
    dataframe = pd.DataFrame(rows)
    chart = (
        alt.Chart(dataframe)
        .mark_line(point=True, strokeWidth=3, color=color)
        .encode(
            x=alt.X(f"{x_field}:N", sort=None, axis=alt.Axis(labelAngle=-35), title=x_title),
            y=alt.Y(f"{y_field}:Q", title=y_title),
            tooltip=[
                alt.Tooltip(f"{x_field}:N", title=x_title),
                alt.Tooltip(f"{y_field}:Q", title=y_title or title),
            ],
        )
        .properties(title=title)
    )
    st.altair_chart(chart, use_container_width=True)


def _render_grouped_bar_chart(
    rows: list[dict],
    title: str,
    color_scale: dict[str, str] | None = None,
    legend_title: str = "Category",
    x_title: str = "Period",
    y_title: str = "Count",
) -> None:
    dataframe = pd.DataFrame(rows)
    color = alt.Color("series:N")
    if color_scale:
        color = alt.Color(
            "series:N",
            scale=alt.Scale(
                domain=list(color_scale.keys()),
                range=list(color_scale.values()),
            ),
            legend=alt.Legend(title=legend_title),
        )
    else:
        color = alt.Color("series:N", legend=alt.Legend(title=legend_title))
    chart = (
        alt.Chart(dataframe)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("period:N", sort=None, axis=alt.Axis(labelAngle=-35), title=x_title),
            xOffset=alt.XOffset("series:N"),
            y=alt.Y("value:Q", title=y_title),
            color=color,
            tooltip=[
                alt.Tooltip("period:N", title=x_title),
                alt.Tooltip("series:N", title=legend_title),
                alt.Tooltip("value:Q", title=y_title),
            ],
        )
        .properties(title=title)
    )
    st.altair_chart(chart, use_container_width=True)


st.set_page_config(page_title="Document Delivery Ops", page_icon="DD", layout="wide")

st.markdown(
    """
    <style>
    h1, h2, h3 {
      letter-spacing: -0.03em;
    }
    [data-testid="stMetricValue"] {
      letter-spacing: -0.02em;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

_require_authentication()

st.title("Document Delivery Ops")
st.caption("FastAPI + worker + SQLite pipeline status")

with st.sidebar:
    page = st.radio("Page", ["Requests", "Statistics", "Email templates"], index=0)
    if _password_auth_enabled() or _auth_enabled():
        st.divider()
        st.subheader("Account")
        st.caption(_current_user_label())
        if _password_auth_enabled():
            st.button(
                "Log out",
                use_container_width=True,
                icon=":material/logout:",
                on_click=_password_logout,
            )
        else:
            st.button(
                "Log out",
                use_container_width=True,
                icon=":material/logout:",
                on_click=st.logout,
            )


def _render_template_editor() -> None:
    st.subheader("Email templates")
    template_kind = st.selectbox(
        "Template type",
        ["delivery", "clarification", "rejection"],
        format_func=lambda value: {
            "delivery": "Delivery mail",
            "clarification": "Clarification mail",
            "rejection": "Rejection mail",
        }[value],
    )
    if template_kind == "delivery":
        st.caption(
            "Available placeholders: {request_id}, {submission_id}, {user_email}, {user_name}, {greeting_name}, {item_count}, {items_text}, {items_html}, {bibtex_filename}, {bibtex_note_text}, {bibtex_note_html}, {sender_name}"
        )
        templates = fetch_email_templates()
    elif template_kind == "clarification":
        st.caption(
            "Available placeholders: {request_id}, {submission_id}, {user_email}, {user_name}, {greeting_name}, {item_id}, {operator_message}, {operator_message_html}, {clarification_url}, {sender_name}"
        )
        templates = fetch_clarification_templates()
    else:
        st.caption(
            "Available placeholders: {request_id}, {submission_id}, {user_email}, {user_name}, {greeting_name}, {item_id}, {item_title}, {item_description}, {item_description_html}, {rejection_reason}, {rejection_reason_html}, {sender_name}"
        )
        templates = fetch_rejection_templates()
    templates_by_language = {template["language"]: template for template in templates}
    language = st.selectbox("Language", ["de", "en", "pl"], format_func=lambda value: LANGUAGE_LABELS[value])
    template = templates_by_language[language]
    with st.form(f"email-template-{template_kind}-{language}"):
        subject_template = st.text_input("Subject template", value=template["subject_template"])
        body_text_template = st.text_area("Text template", value=template["body_text_template"], height=300)
        body_html_template = st.text_area("HTML template", value=template["body_html_template"], height=300)
        submitted = st.form_submit_button("Save template")
        if submitted:
            save_email_template(
                language,
                {
                    "subject_template": subject_template,
                    "body_text_template": body_text_template,
                    "body_html_template": body_html_template,
                },
                template_kind=template_kind,
            )
            st.success("Template saved")
            st.rerun()

    st.caption(f"Last updated: {template['updated_at'] or 'default template'}")

    st.divider()
    st.subheader("Predefined text blocks")
    st.caption("Manage selectable texts for rejection reasons and clarification details.")

    template_kind = st.selectbox(
        "Text block type",
        ["rejection_reason", "clarification_detail"],
        format_func=lambda value: OPERATOR_TEXT_TEMPLATE_KIND_LABELS[value],
        key="operator-text-template-kind",
    )
    template_language = st.selectbox(
        "Text block language",
        ["de", "en", "pl"],
        format_func=lambda value: LANGUAGE_LABELS[value],
        key="operator-text-template-language",
    )

    try:
        existing_entries = fetch_operator_text_templates(template_kind, template_language)
    except requests.RequestException as exc:
        st.error(f"Could not load predefined text blocks: {exc}")
        return

    editor_df = pd.DataFrame(existing_entries)
    if editor_df.empty:
        editor_df = pd.DataFrame(columns=["label", "text"])
    else:
        editor_df = editor_df[["label", "text"]]

    edited_df = st.data_editor(
        editor_df,
        use_container_width=True,
        hide_index=True,
        num_rows="dynamic",
        key=f"operator-text-editor-{template_kind}-{template_language}",
        column_config={
            "label": st.column_config.TextColumn("Label", required=True),
            "text": st.column_config.TextColumn("Text", required=True, width="large"),
        },
    )

    if st.button("Save predefined texts", key=f"save-operator-texts-{template_kind}-{template_language}"):
        entries: list[dict] = []
        for _, row in edited_df.iterrows():
            label = str(row.get("label") or "").strip()
            text = str(row.get("text") or "").strip()
            if not label and not text:
                continue
            if not label or not text:
                st.error("Each predefined text row needs both label and text.")
                st.stop()
            entries.append({"label": label, "text": text})
        save_operator_text_templates(template_kind, template_language, entries)
        st.success("Predefined texts saved")
        st.rerun()


def _render_requests_page() -> None:
    requests_data = fetch_requests()
    status_counts: dict[str, int] = {}
    for request in requests_data:
        status_counts[request["status"]] = status_counts.get(request["status"], 0) + 1

    metrics = st.columns(6)
    metrics[0].metric("Requests", len(requests_data))
    metrics[1].metric("Waiting", status_counts.get("WAITING_FOR_ATTACHMENT", 0))
    metrics[2].metric("Review", status_counts.get("NEEDS_REVIEW", 0))
    metrics[3].metric("Awaiting User", status_counts.get("AWAITING_USER", 0))
    metrics[4].metric("Rejected", status_counts.get("REJECTED", 0))
    metrics[5].metric("Processed", status_counts.get("PROCESSED", 0))

    table_rows = [
        {
            "select": False,
            "request_id": request["request_id"],
            "status": request["status"],
            "user_email": request["user_email"],
            "items": len(request["items"]),
            "updated_at": request["updated_at"],
        }
        for request in requests_data
    ]
    request_ids = [request["request_id"] for request in requests_data]
    requested_request_id = _query_request_id()
    selected_request = None
    st.subheader("Queue")
    queue_df = pd.DataFrame([{key: value for key, value in row.items() if key != "select"} for row in table_rows])
    event = st.dataframe(
        queue_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="request-queue-selection",
    )

    if request_ids:
        selected_request = st.session_state.get("selected_request_id")
        if requested_request_id and requested_request_id in request_ids:
            selected_request = requested_request_id
        selection_rows = event.selection.rows if hasattr(event, "selection") else []
        if selection_rows:
            try:
                selected_request = str(queue_df.iloc[selection_rows[0]]["request_id"])
            except Exception:
                pass
        if selected_request not in request_ids:
            selected_request = request_ids[0]

    st.session_state["selected_request_id"] = selected_request
    if selected_request:
        st.query_params["request_id"] = selected_request
    if requested_request_id and requested_request_id not in request_ids:
        st.warning(f"Request {requested_request_id} was not found.")

    if not selected_request:
        return

    request = fetch_request(selected_request)
    request_language = _request_language(request)
    try:
        clarification_text_templates = fetch_operator_text_templates("clarification_detail", request_language)
        rejection_text_templates = fetch_operator_text_templates("rejection_reason", request_language)
    except requests.RequestException:
        clarification_text_templates = CLARIFICATION_DETAIL_TEMPLATES[request_language]
        rejection_text_templates = REJECTION_REASON_TEMPLATES[request_language]
    events = fetch_events(selected_request)

    summary_col, action_col = st.columns([4, 1])
    with summary_col:
        st.subheader(f"Request {request['request_id']}")
        _render_key_value_payload(
            "Request details",
            {
                "status": request["status"],
                "submission_id": request["formcycle_submission_id"],
                "user_email": request["user_email"],
                "delivery_days": request["delivery_days"],
                "notification_sent_at": request["notification_sent_at"],
                "last_error": request["last_error"],
            },
            key=f"request-summary-{request['request_id']}",
        )
    with action_col:
        retryable_request_statuses = {"FAILED", "NEEDS_REVIEW", "AWAITING_USER", "ATTENTION"}
        retryable_item_statuses = {"FAILED", "NEEDS_REVIEW", "AWAITING_USER", "READY_TO_NOTIFY"}
        can_retry = request["status"] in retryable_request_statuses or any(
            item["status"] in retryable_item_statuses for item in request["items"]
        )
        if can_retry:
            if st.button("Retry request", use_container_width=True):
                retry_request(selected_request)
                st.rerun()

    st.subheader("Items")
    item_rows = [
        {
            "item_index": item["item_index"],
            "item_type": item["item_type"],
            "title": item["title"],
            "creators": item["creators"],
            "publisher": item["publisher"],
            "series": item["series"],
            "status": item["status"],
            "metadata_source": item["metadata_source"],
            "confidence": item["normalization_confidence"],
            "zotero_item_key": item["zotero_item_key"],
            "attachment_state": _attachment_state(item),
            "download_url": item["download_url"],
            "expires_on": item["expires_on"],
            "last_error": item["last_error"],
        }
        for item in request["items"]
    ]
    st.dataframe(item_rows, use_container_width=True, hide_index=True)

    rejectable_items = [
        item
        for item in request["items"]
        if item["status"] not in {"DELIVERED", "REJECTED"}
    ]

    review_candidates = [item for item in request["items"] if item["status"] == "NEEDS_REVIEW"]
    if review_candidates:
        st.subheader("Review metadata")
        selected_review = st.selectbox(
            "Item needing review",
            review_candidates,
            format_func=lambda item: f"#{item['item_index']} {item['title']}",
        )
        raw_json = selected_review.get("raw_json")
        normalized_json = selected_review.get("normalized_json")
        original_bib = _parse_json_object(raw_json)
        normalized_bib = _parse_json_object(normalized_json)
        has_effective_normalization = bool(normalized_bib and not _payloads_effectively_equal(original_bib, normalized_bib))
        left, right = st.columns(2)
        with left:
            _render_bib_payload("Original payload", original_bib, f"raw-json-{selected_review['id']}")
        with right:
            if has_effective_normalization:
                _render_bib_payload("Proposed normalization", normalized_bib, f"normalized-json-{selected_review['id']}")
            else:
                st.caption("Proposed normalization")
                st.info("No effective normalization changes proposed.")

        latest_clarification = _latest_user_clarification(events, selected_review["id"])
        if latest_clarification:
            st.caption("Latest user clarification")
            if latest_clarification["operator_message"]:
                st.info(latest_clarification["operator_message"])
            st.success(latest_clarification["user_note"])
            st.caption(f"Received: {latest_clarification['created_at']}")

        review_presets: dict[str, dict] = {"Current item values": _review_seed_from_item(selected_review)}
        if original_bib:
            review_presets["Original submission"] = _review_seed_from_bib(original_bib)
        if has_effective_normalization and normalized_bib:
            review_presets["Proposed normalization"] = _review_seed_from_bib(normalized_bib)

        resolution_json = selected_review.get("resolution_json")
        quick_actions: list[tuple[str, dict, str]] = []
        if resolution_json:
            st.caption("Source evidence")
            try:
                evidence = json.loads(resolution_json)
            except json.JSONDecodeError:
                evidence = []
                st.code(resolution_json, language="json")
            if evidence:
                evidence_rows = [
                    {
                        "source": item.get("source"),
                        "status": item.get("status"),
                        "score": item.get("score"),
                        "explanation": item.get("explanation"),
                    }
                    for item in evidence
                ]
                st.dataframe(evidence_rows, use_container_width=True, hide_index=True)
                for evidence_item in evidence:
                    candidate_json = evidence_item.get("candidate_json")
                    candidate_bib = None
                    if candidate_json:
                        candidate_bib = _parse_json_object(candidate_json)
                        if candidate_bib:
                            candidate_seed = _review_seed_from_bib(candidate_bib)
                            source_name = evidence_item.get("source") or "source"
                            review_presets[f"{source_name} candidate"] = candidate_seed
                            if evidence_item.get("status") == "validated":
                                quick_actions.append(
                                    (
                                        f"Accept {source_name}",
                                        candidate_seed,
                                        f"Accepted {source_name} candidate directly",
                                    )
                                )
                        with st.expander(f"{evidence_item.get('source')} candidate"):
                            _render_bib_payload(
                                f"{evidence_item.get('source')} candidate",
                                candidate_bib,
                                f"candidate-json-{selected_review['id']}-{evidence_item.get('source')}",
                            )

        if original_bib:
            quick_actions.insert(
                0,
                (
                    "Accept original submission",
                    _review_seed_from_bib(original_bib),
                    "Accepted original submission directly",
                ),
            )

        if has_effective_normalization and normalized_bib:
            quick_actions.append(
                (
                    "Accept proposed normalization",
                    _review_seed_from_bib(normalized_bib),
                    "Accepted proposed normalization directly",
                )
            )

        if quick_actions:
            st.caption("Quick review actions")
            action_columns = st.columns(len(quick_actions))
            for idx, (label, seed, notes) in enumerate(quick_actions):
                with action_columns[idx]:
                    if st.button(label, key=f"quick-approve-{selected_review['id']}-{idx}", use_container_width=True):
                        approve_item(
                            request["request_id"],
                            selected_review["id"],
                            _approval_payload_from_seed(seed, notes),
                        )
                        st.rerun()

        with st.form(f"clarification-item-{selected_review['id']}"):
            clarification_templates = clarification_text_templates
            selected_clarification_labels = st.multiselect(
                "Predefined clarification details",
                options=[template["label"] for template in clarification_templates],
                key=f"clarification-templates-{selected_review['id']}",
            )
            clarification_message_free_text = st.text_area(
                "Additional clarification details",
                value="",
                help="Free text in addition to predefined clarification details.",
                height=140,
            )
            clarification_submitted = st.form_submit_button("Request clarification")
            if clarification_submitted:
                clarification_message = _compose_message(
                    selected_clarification_labels,
                    clarification_templates,
                    clarification_message_free_text,
                )
                if not clarification_message.strip():
                    st.error("Select at least one predefined detail or enter free text.")
                    st.stop()
                request_clarification(request["request_id"], selected_review["id"], clarification_message)
                st.success("Clarification request sent.")
                st.rerun()

        preset_name = st.selectbox(
            "Load review form from",
            options=list(review_presets.keys()),
            key=f"review-preset-{selected_review['id']}",
        )
        preset = review_presets[preset_name]

        with st.form(f"approve-item-{selected_review['id']}"):
            item_type = st.selectbox(
                "Item type",
                options=["journalArticle", "bookSection"],
                index=0 if preset.get("item_type") != "bookSection" else 1,
            )
            title = st.text_input("Title", value=preset["title"])
            creators = st.text_input("Creators (; separated)", value=preset["creators"])
            editors = st.text_input("Editors (; separated)", value=preset["editors"])
            publication_title = st.text_input("Publication", value=preset["publication_title"])
            year = st.text_input("Year", value=preset["year"])
            volume = st.text_input("Volume", value=preset["volume"])
            issue = st.text_input("Issue", value=preset["issue"])
            pages = st.text_input("Pages", value=preset["pages"])
            doi = st.text_input("DOI", value=preset["doi"])
            publisher = st.text_input("Publisher", value=preset["publisher"])
            place = st.text_input("Place", value=preset["place"])
            series = st.text_input("Series", value=preset["series"])
            edition = st.text_input("Edition", value=preset["edition"])
            isbn = st.text_input("ISBN", value=preset["isbn"])
            review_notes = st.text_area("Review notes", value=selected_review.get("review_notes") or "")
            submitted = st.form_submit_button("Approve metadata")
            if submitted:
                approve_item(
                    request["request_id"],
                    selected_review["id"],
                    _approval_payload_from_seed(
                        {
                            "item_type": item_type,
                            "title": title,
                            "creators": creators,
                            "editors": editors,
                            "publication_title": publication_title,
                            "year": year,
                            "volume": volume,
                            "issue": issue,
                            "pages": pages,
                            "doi": doi,
                            "publisher": publisher,
                            "place": place,
                            "series": series,
                            "edition": edition,
                            "isbn": isbn,
                        },
                        review_notes,
                    ),
                )
                st.rerun()

    if rejectable_items:
        st.subheader("Reject item")
        rejected_item = st.selectbox(
            "Item to reject",
            rejectable_items,
            format_func=lambda item: f"#{item['item_index']} {item['title']} ({item['status']})",
            key=f"reject-item-{request['request_id']}",
        )
        with st.form(f"reject-item-form-{rejected_item['id']}"):
            rejection_templates = rejection_text_templates
            selected_rejection_labels = st.multiselect(
                "Predefined rejection reasons",
                options=[template["label"] for template in rejection_templates],
                key=f"rejection-templates-{rejected_item['id']}",
            )
            rejection_reason_free_text = st.text_area(
                "Additional rejection reason",
                value="",
                help="Free text in addition to predefined rejection reasons.",
                height=120,
            )
            reject_submitted = st.form_submit_button("Reject item")
            if reject_submitted:
                rejection_reason = _compose_message(
                    selected_rejection_labels,
                    rejection_templates,
                    rejection_reason_free_text,
                )
                if not rejection_reason.strip():
                    st.error("Select at least one predefined reason or enter free text.")
                    st.stop()
                reject_item(request["request_id"], rejected_item["id"], rejection_reason)
                st.success("Rejection mail sent and item rejected.")
                st.rerun()

    upload_candidates = [
        item
        for item in request["items"]
        if item["status"] in {"WAITING_FOR_ATTACHMENT", "FAILED"} and item["zotero_item_key"]
    ]
    if upload_candidates:
        st.subheader("Upload scans")
        st.caption("Preferred path: upload the scanned PDF here. The worker will OCR it if needed, attach the processed PDF to Zotero, and continue delivery.")
        for item in upload_candidates:
            st.markdown(f"**#{item['item_index']} {item['title']}**")
            if item.get("uploaded_scan_filename"):
                st.caption(f"Current uploaded scan: {item['uploaded_scan_filename']}")
            uploader_col, remove_col = st.columns([4, 1])
            with uploader_col:
                uploader = st.file_uploader(
                    "PDF scan",
                    type=["pdf"],
                    key=f"scan-upload-{item['id']}",
                    help="Upload the scanned PDF for this Zotero item.",
                )
                upload_label = "Replace scan" if item.get("uploaded_scan_filename") else "Upload scan"
                if st.button(upload_label, key=f"scan-upload-button-{item['id']}"):
                    if uploader is None:
                        st.warning("Choose a PDF first.")
                    else:
                        upload_scan(request["request_id"], item["id"], uploader)
                        st.success("Scan uploaded. The worker will process it next.")
                        st.rerun()
            with remove_col:
                if item.get("uploaded_scan_filename"):
                    st.write("")
                    if st.button("Remove scan", key=f"scan-remove-{item['id']}", use_container_width=True):
                        remove_scan(request["request_id"], item["id"])
                        st.success("Uploaded scan removed.")
                        st.rerun()

    attachment_timeline = _attachment_timeline_rows(request, events)
    if attachment_timeline:
        st.subheader("Attachment timeline")
        st.dataframe(attachment_timeline, use_container_width=True, hide_index=True)

    st.subheader("Events")
    event_rows = [
        {
            "created_at": event["created_at"],
            "level": event["level"],
            "event_type": event["event_type"],
            "payload_json": event["payload_json"],
        }
        for event in events
    ]
    st.dataframe(event_rows, use_container_width=True, hide_index=True)


def _render_statistics_page() -> None:
    st.subheader("Statistics")
    granularity_labels = {
        "day": "Daily",
        "week": "Weekly",
        "month": "Monthly",
        "year": "Yearly",
    }
    control_col1, control_col2 = st.columns(2)
    with control_col1:
        granularity = st.selectbox(
            "Granularity",
            ["day", "week", "month", "year"],
            format_func=lambda value: granularity_labels[value],
        )
    with control_col2:
        period_max = {"day": 90, "week": 52, "month": 36, "year": 10}[granularity]
        period_default = {"day": 30, "week": 12, "month": 12, "year": 5}[granularity]
        periods = st.slider(
            "Periods",
            min_value=3,
            max_value=period_max,
            value=period_default,
        )

    stats = fetch_statistics(granularity=granularity, periods=periods)
    if not stats:
        st.info("No statistics available yet.")
        return

    total_requests = sum(row["request_count"] for row in stats)
    total_fulfilled = sum(row["fulfilled_requests"] for row in stats)
    total_rejected_requests = sum(row["rejected_requests"] for row in stats)
    total_rejected_items = sum(row["rejected_items"] for row in stats)
    fulfillment_rate = (total_fulfilled / total_requests) if total_requests else 0.0
    rejection_rate = (total_rejected_requests / total_requests) if total_requests else 0.0
    weighted_duration_hours = sum(
        (row["avg_fulfillment_hours"] or 0.0) * row["fulfilled_requests"] for row in stats
    )
    avg_duration = round(weighted_duration_hours / total_fulfilled, 2) if total_fulfilled else None
    total_invalid_metadata = sum(row["invalid_metadata_items"] for row in stats)
    total_clarifications = sum(row["clarification_requests"] for row in stats)
    total_reused = sum(row["reused_items"] for row in stats)

    metrics = st.columns(6)
    metrics[0].metric("Requests", total_requests)
    metrics[1].metric("Fulfillment rate", f"{fulfillment_rate * 100:.1f}%")
    metrics[2].metric("Rejection rate", f"{rejection_rate * 100:.1f}%")
    metrics[3].metric("Avg fulfillment hours", f"{avg_duration:.1f}" if avg_duration is not None else "n/a")
    metrics[4].metric("Invalid metadata", total_invalid_metadata)
    metrics[5].metric("Reused items", total_reused)

    st.caption(
        f"Clarification requests in selected periods: {total_clarifications} | "
        f"Valid metadata items: {sum(row['valid_metadata_items'] for row in stats)} | "
        f"Rejected requests: {total_rejected_requests} | "
        f"Rejected items: {total_rejected_items}"
    )

    table_rows = [
        {
            "period": row["period_label"],
            "requests": row["request_count"],
            "fulfilled": row["fulfilled_requests"],
            "rejected_requests": row["rejected_requests"],
            "rejected_items": row["rejected_items"],
            "fulfillment_rate_pct": round(row["fulfillment_rate"] * 100, 1),
            "rejection_rate_pct": round(row["rejection_rate"] * 100, 1),
            "avg_fulfillment_hours": row["avg_fulfillment_hours"],
            "valid_metadata_items": row["valid_metadata_items"],
            "invalid_metadata_items": row["invalid_metadata_items"],
            "clarification_requests": row["clarification_requests"],
            "reused_items": row["reused_items"],
        }
        for row in stats
    ]

    st.subheader("Charts")
    chart_col1, chart_col2 = st.columns(2)
    with chart_col1:
        _render_bar_chart(
            table_rows,
            "period",
            "requests",
            "Requests per Period",
            "#b14d31",
            y_title="Requests",
        )
    with chart_col2:
        _render_line_chart(
            table_rows,
            "period",
            "fulfillment_rate_pct",
            "Fulfillment Rate (%)",
            "#4c7a6f",
            y_title="Fulfillment rate (%)",
        )

    outcome_rows = []
    for row in table_rows:
        outcome_rows.extend(
            [
                {"period": row["period"], "series": "Fulfilled requests", "value": row["fulfilled"]},
                {"period": row["period"], "series": "Rejected requests", "value": row["rejected_requests"]},
            ]
        )
    metadata_rows = []
    for row in table_rows:
        metadata_rows.extend(
            [
                {"period": row["period"], "series": "Valid metadata", "value": row["valid_metadata_items"]},
                {"period": row["period"], "series": "Invalid metadata", "value": row["invalid_metadata_items"]},
                {"period": row["period"], "series": "Clarifications", "value": row["clarification_requests"]},
            ]
        )
    secondary_chart_col1, secondary_chart_col2 = st.columns(2)
    with secondary_chart_col1:
        _render_grouped_bar_chart(
            outcome_rows,
            "Request Outcomes",
            color_scale={
                "Fulfilled requests": "#4c7a6f",
                "Rejected requests": "#9b2c2c",
            },
            legend_title="Request outcome",
            y_title="Requests",
        )
    with secondary_chart_col2:
        _render_grouped_bar_chart(
            metadata_rows,
            "Metadata Outcomes",
            color_scale={
                "Valid metadata": "#2E8B57",
                "Invalid metadata": "#C0392B",
                "Clarifications": "#D4A017",
            },
            legend_title="Metadata status",
            y_title="Items",
        )
    tertiary_chart_col1, tertiary_chart_col2 = st.columns(2)
    with tertiary_chart_col1:
        _render_bar_chart(
            table_rows,
            "period",
            "reused_items",
            "Reused Zotero Items",
            "#7b5ea7",
            y_title="Reused items",
        )
    with tertiary_chart_col2:
        _render_bar_chart(
            table_rows,
            "period",
            "rejected_items",
            "Rejected Items",
            "#9b2c2c",
            y_title="Rejected items",
        )

    st.subheader("Table")
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
_, right = st.columns([3, 1])
with right:
    if st.button("Refresh now", use_container_width=True):
        st.rerun()

if page == "Email templates":
    _render_template_editor()
elif page == "Statistics":
    _render_statistics_page()
else:
    _render_requests_page()
