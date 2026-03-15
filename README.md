# Document Delivery Pipeline

Code-first starter for a university-library document delivery workflow:
- FormCycle sends delivery requests to FastAPI.
- FastAPI stores requests and requested items in SQLite.
- A polling worker normalizes metadata, checks Zotero, waits for PDF attachments, and delivers finished PDFs through Nextcloud.
- Streamlit provides the operator view for queue status, failures, and retries.
- Delivery notifications are sent directly by SMTP from the app, with an optional personalized FormCycle follow-up link for later conversation steps.

## Stack

- FastAPI API service
- Python worker
- SQLite
- Streamlit operator UI

## Workflow

1. FormCycle posts a request with one or more bibliographic items.
2. FastAPI persists the request in SQLite.
3. The worker normalizes metadata through a multi-source resolver using Crossref and OpenAlex.
4. Low-confidence normalization results are held in `NEEDS_REVIEW` until an operator approves or edits them in Streamlit.
5. Once metadata is approved, the worker checks Zotero for an existing matching item.
6. If no match exists, the worker creates a new Zotero item tagged `in process`.
7. The worker polls Zotero until a PDF attachment exists.
8. The worker optionally runs OCR if `OCR_COMMAND_TEMPLATE` is configured.
9. The worker uploads the processed PDF to Nextcloud and creates an expiring share link.
10. The worker sends the final requester email directly through SMTP when configured.
11. The email can include a personalized FormCycle follow-up link for clarification, confirmation, or redelivery requests.

## Architecture

The active system has four roles:

- FormCycle handles intake and later user follow-up forms.
- FastAPI ingests requests and exposes operator endpoints.
- SQLite stores request state, item state, and job events.
- The worker performs metadata resolution, Zotero coordination, OCR, Nextcloud delivery, and SMTP notification.

The operator workflow is code-first:

- Streamlit is the review and retry interface.
- metadata resolution combines Crossref, OpenAlex, and, for book-like items, Lobid and GBV/K10plus
- delivery mails are sent directly by the app, not by an external low-code workflow

## Project layout

```text
.
├── docker-compose.yml
├── .env.example
├── docs/
│   ├── architecture.md
│   └── formcycle-forms.md
└── services/
    └── orchestrator/
        ├── Dockerfile
        ├── requirements.txt
        └── app/
            ├── clients.py
            ├── config.py
            ├── db.py
            ├── jobs.py
            ├── main.py
            ├── models.py
            ├── schemas.py
            ├── ui.py
            └── worker.py
```

## Quick start

1. Create local data folders:

```bash
mkdir -p data/scans data/app
```

2. Copy env file:

```bash
cp .env.example .env
```

3. Start services:

```bash
docker compose up --build
```

4. Open the operator UI:

```text
http://localhost:8501
```

5. Health check:

```bash
curl http://localhost:8000/health
```

## FormCycle webhook payload example

This is the preferred shape for new FormCycle requests:

```bash
curl -X POST http://localhost:8000/webhooks/formcycle/requests \
  -H "Content-Type: application/json" \
  -H "X-Formcycle-Secret: change-me" \
  -d '{
    "request_id": "DD-2026-0001",
    "formcycle_submission_id": "12345",
    "user_email": "user@example.edu",
    "user_name": "Jane User",
    "language": "de",
    "delivery_days": 14,
    "items": [
      {
        "item_index": 0,
        "bibliographic_data": {
          "item_type": "journalArticle",
          "title": "Digitization Pipeline Design",
          "creators": ["Miller, Sam", "Rossi, Lea"],
          "publication_title": "Library Technology Journal",
          "year": "2024",
          "volume": "12",
          "issue": "3",
          "pages": "44-59",
          "doi": "10.1234/example"
        }
      }
    ]
  }'
```

The API also accepts the older single-item shape with top-level `bibliographic_data`.
If FormCycle posts repeated items one by one with the same `request_id`, the app appends those items to the existing request instead of discarding them.

## SQLite state model

The database has three runtime tables:
- `delivery_requests`
- `request_items`
- `job_events`

Important item statuses:
- `PENDING_METADATA`
- `NEEDS_REVIEW`
- `PENDING_ZOTERO`
- `WAITING_FOR_ATTACHMENT`
- `PROCESSING_PDF`
- `READY_TO_NOTIFY`
- `DELIVERED`
- `FAILED`

## Configuration notes

- `DATABASE_URL` defaults to `sqlite:////app/data/delivery.sqlite3`.
- `OPENALEX_EMAIL` enables OpenAlex as a normalization source.
- `CROSSREF_MAILTO` is optional but recommended for polite Crossref API usage.
- `GBV_SRU_URL` configures the K10plus/GBV SRU endpoint used for book and book-section lookups.
- `RESOLUTION_PRIORITY_LOBID`, `RESOLUTION_PRIORITY_GBV`, `RESOLUTION_PRIORITY_CROSSREF`, and `RESOLUTION_PRIORITY_OPENALEX` control which validated source wins when multiple sources match. For `bookSection`, Lobid and GBV are evaluated ahead of Crossref/OpenAlex by default.
- `NORMALIZATION_AUTO_ACCEPT_THRESHOLD` controls when a metadata match can bypass human review.
- `ZOTERO_COLLECTION_KEY` is optional. Leave it empty to work in the Zotero library root.
- `CITATION_STYLE` controls the CSL style Zotero uses when the app renders bibliography entries for the delivery mail.
- `CITATION_LOCALE_DE`, `CITATION_LOCALE_EN`, and `CITATION_LOCALE_PL` map the FormCycle form language to the Zotero citation locale.
- `SMTP_HOST` enables direct email delivery from the app.
- `SMTP_FROM_EMAIL` is required when SMTP is enabled.
- `SMTP_USE_TLS=true` with port `587` is the normal setup for authenticated submission.
- `FORMCYCLE_FOLLOWUP_URL_TEMPLATE` can embed a personalized FormCycle follow-up link into the delivery mail. Supported placeholders are `{request_id}`, `{formcycle_submission_id}`, `{user_email}`, and URL-encoded variants `{request_id_q}`, `{formcycle_submission_id_q}`, `{user_email_q}`.
- `OCR_COMMAND_TEMPLATE` is optional. If empty, the worker uploads the original attachment PDF without OCR.
- `INTERNAL_API_TOKEN` protects the Streamlit/API operator endpoints.
- When your FormCycle request form is multilingual, include the active language in the webhook payload, for example `"language": "[%lang%]"`, so the delivery mail and Zotero citation locale match the form language.
