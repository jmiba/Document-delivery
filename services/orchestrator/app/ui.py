from __future__ import annotations

import os

import requests
import streamlit as st


API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")
INTERNAL_API_TOKEN = os.environ.get("INTERNAL_API_TOKEN", "")


def _headers() -> dict[str, str]:
    headers = {}
    if INTERNAL_API_TOKEN:
        headers["X-Internal-Token"] = INTERNAL_API_TOKEN
    return headers


def fetch_requests() -> list[dict]:
    response = requests.get(f"{API_BASE_URL}/requests", headers=_headers(), timeout=30)
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

st.title("Document Delivery Ops")
st.caption("FastAPI + worker + SQLite pipeline status")

_, right = st.columns([3, 1])
with right:
    if st.button("Refresh now", use_container_width=True):
        st.rerun()

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

if selected_request:
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
            "title": item["title"],
            "creators": item["creators"],
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

        with st.form(f"approve-item-{selected_review['id']}"):
            title = st.text_input("Title", value=selected_review["title"])
            creators = st.text_input("Creators (; separated)", value=selected_review["creators"])
            publication_title = st.text_input("Publication", value=selected_review["publication_title"])
            year = st.text_input("Year", value=selected_review["year"])
            volume = st.text_input("Volume", value=selected_review.get("volume") or "")
            issue = st.text_input("Issue", value=selected_review.get("issue") or "")
            pages = st.text_input("Pages", value=selected_review.get("pages") or "")
            doi = st.text_input("DOI", value=selected_review.get("doi") or "")
            review_notes = st.text_area("Review notes", value=selected_review.get("review_notes") or "")
            submitted = st.form_submit_button("Approve metadata")
            if submitted:
                approve_item(
                    request["request_id"],
                    selected_review["id"],
                    {
                        "bibliographic_data": {
                            "item_type": "journalArticle",
                            "title": title,
                            "creators": [part.strip() for part in creators.split(";") if part.strip()],
                            "publication_title": publication_title,
                            "year": year,
                            "volume": volume or None,
                            "issue": issue or None,
                            "pages": pages or None,
                            "doi": doi or None,
                        },
                        "review_notes": review_notes or None,
                    },
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
