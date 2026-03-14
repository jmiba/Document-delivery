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

metrics = st.columns(4)
metrics[0].metric("Requests", len(requests_data))
metrics[1].metric("Waiting", status_counts.get("WAITING_FOR_ATTACHMENT", 0))
metrics[2].metric("Attention", status_counts.get("ATTENTION", 0))
metrics[3].metric("Processed", status_counts.get("PROCESSED", 0))

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
            "zotero_item_key": item["zotero_item_key"],
            "download_url": item["download_url"],
            "expires_on": item["expires_on"],
            "last_error": item["last_error"],
        }
        for item in request["items"]
    ]
    st.dataframe(item_rows, use_container_width=True, hide_index=True)

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
