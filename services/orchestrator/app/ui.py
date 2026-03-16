from __future__ import annotations

import json
import os

import requests
import streamlit as st


API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")


def _auth_settings() -> dict:
    try:
        auth_settings = st.secrets.get("auth", {})
    except Exception:
        return {}
    if hasattr(auth_settings, "to_dict"):
        auth_settings = auth_settings.to_dict()
    return auth_settings if isinstance(auth_settings, dict) else {}


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
    if hasattr(st.user, "name") and st.user.name:
        return str(st.user.name)
    if hasattr(st.user, "email") and st.user.email:
        return str(st.user.email)
    if hasattr(st.user, "sub") and st.user.sub:
        return str(st.user.sub)
    return "Authenticated user"


def _require_authentication() -> None:
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


def save_email_template(language: str, payload: dict) -> dict:
    response = requests.put(
        f"{API_BASE_URL}/email-templates/{language}",
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


st.set_page_config(page_title="Document Delivery Ops", page_icon="DD", layout="wide")

st.markdown(
    """
    <style>
    :root {
      --ink: #11243a;
      --paper: #f5f0e8;
      --accent: #b14d31;
      --accent-soft: #f1d6b8;
      --line: #d8c9b9;
    }
    .stApp {
      background:
        radial-gradient(circle at top right, rgba(177, 77, 49, 0.16), transparent 32%),
        linear-gradient(180deg, #f7f3eb 0%, #efe5d6 100%);
      color: var(--ink);
    }
    h1, h2, h3 {
      color: var(--ink);
      letter-spacing: -0.03em;
    }
    [data-testid="stMetricValue"] {
      color: var(--accent);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

_require_authentication()

st.title("Document Delivery Ops")
st.caption("FastAPI + worker + SQLite pipeline status")

with st.sidebar:
    page = st.radio("Page", ["Requests", "Email templates"], index=0)
    if _auth_enabled():
        st.divider()
        st.subheader("Account")
        st.caption(_current_user_label())
        st.button(
            "Log out",
            use_container_width=True,
            icon=":material/logout:",
            on_click=st.logout,
        )


def _render_template_editor() -> None:
    st.subheader("Email templates")
    st.caption("Available placeholders: {request_id}, {submission_id}, {user_email}, {user_name}, {greeting_name}, {item_count}, {items_text}, {items_html}, {followup_text}, {followup_html}, {sender_name}")
    templates = fetch_email_templates()
    templates_by_language = {template["language"]: template for template in templates}
    language = st.selectbox("Language", ["de", "en", "pl"], format_func=lambda value: {"de": "German", "en": "English", "pl": "Polish"}[value])
    template = templates_by_language[language]
    with st.form(f"email-template-{language}"):
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
            )
            st.success("Template saved")
            st.rerun()

    st.caption(f"Last updated: {template['updated_at'] or 'default template'}")


def _render_requests_page() -> None:
    requests_data = fetch_requests()
    status_counts: dict[str, int] = {}
    for request in requests_data:
        status_counts[request["status"]] = status_counts.get(request["status"], 0) + 1

    metrics = st.columns(5)
    metrics[0].metric("Requests", len(requests_data))
    metrics[1].metric("Waiting", status_counts.get("WAITING_FOR_ATTACHMENT", 0))
    metrics[2].metric("Review", status_counts.get("NEEDS_REVIEW", 0))
    metrics[3].metric("Notify Failed", status_counts.get("NOTIFY_FAILED", 0))
    metrics[4].metric("Processed", status_counts.get("PROCESSED", 0))

    table_rows = [
        {
            "request_id": request["request_id"],
            "status": request["status"],
            "user_email": request["user_email"],
            "items": len(request["items"]),
            "updated_at": request["updated_at"],
        }
        for request in requests_data
    ]
    st.subheader("Queue")
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    request_ids = [request["request_id"] for request in requests_data]
    selected_request = st.selectbox("Request", request_ids) if request_ids else None

    if not selected_request:
        return

    request = fetch_request(selected_request)
    events = fetch_events(selected_request)

    summary_col, action_col = st.columns([4, 1])
    with summary_col:
        st.subheader(f"Request {request['request_id']}")
        st.write(
            {
                "status": request["status"],
                "submission_id": request["formcycle_submission_id"],
                "user_email": request["user_email"],
                "delivery_days": request["delivery_days"],
                "notification_sent_at": request["notification_sent_at"],
                "last_error": request["last_error"],
            }
        )
    with action_col:
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
            "download_url": item["download_url"],
            "expires_on": item["expires_on"],
            "last_error": item["last_error"],
        }
        for item in request["items"]
    ]
    st.dataframe(item_rows, use_container_width=True, hide_index=True)

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
        left, right = st.columns(2)
        with left:
            st.caption("Original payload")
            st.code(raw_json or "No raw payload stored", language="json")
        with right:
            st.caption("Proposed normalization")
            st.code(normalized_json or "No normalized payload stored", language="json")

        review_presets: dict[str, dict] = {"Current item values": _review_seed_from_item(selected_review)}
        original_bib = _parse_json_object(raw_json)
        if original_bib:
            review_presets["Original submission"] = _review_seed_from_bib(original_bib)
        normalized_bib = _parse_json_object(normalized_json)
        if normalized_bib:
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
                    if candidate_json:
                        candidate_bib = _parse_json_object(candidate_json)
                        if candidate_bib:
                            candidate_seed = _review_seed_from_bib(candidate_bib)
                            source_name = evidence_item.get("source") or "source"
                            review_presets[f"{source_name} candidate"] = candidate_seed
                            quick_actions.append(
                                (
                                    f"Accept {source_name}",
                                    candidate_seed,
                                    f"Accepted {source_name} candidate directly",
                                )
                            )
                        with st.expander(f"{evidence_item.get('source')} candidate"):
                            st.code(candidate_json, language="json")

        if original_bib:
            quick_actions.insert(
                0,
                (
                    "Accept original submission",
                    _review_seed_from_bib(original_bib),
                    "Accepted original submission directly",
                ),
            )

        if normalized_bib:
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
_, right = st.columns([3, 1])
with right:
    if st.button("Refresh now", use_container_width=True):
        st.rerun()

if page == "Email templates":
    _render_template_editor()
else:
    _render_requests_page()
