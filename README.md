# Document Delivery Pipeline

Code-first starter for a university-library document delivery workflow:
- FormCycle sends delivery requests to FastAPI.
- FastAPI stores requests and requested items in SQLite.
- A polling worker normalizes metadata, checks Zotero, waits for PDF attachments, and delivers finished PDFs through Nextcloud.
- Streamlit provides the operator view for queue status, failures, and retries.

## Stack

- FastAPI API service
- Python worker
- SQLite
- Streamlit operator UI

## Workflow

1. FormCycle posts a request with one or more bibliographic items.
2. FastAPI persists the request in SQLite.
3. The worker normalizes metadata through OpenAlex when configured.
4. The worker checks Zotero for an existing matching item.
5. If no match exists, the worker creates a new Zotero item tagged `in process`.
6. The worker polls Zotero until a PDF attachment exists.
7. The worker optionally runs OCR if `OCR_COMMAND_TEMPLATE` is configured.
8. The worker uploads the processed PDF to Nextcloud and creates an expiring share link.
9. The worker notifies FormCycle with normalized citation text plus the download links.

## Project layout

```text
.
├── docker-compose.yml
├── .env.example
├── docs/
│   ├── budibase-setup.md
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

## SQLite state model

The database has three runtime tables:
- `delivery_requests`
- `request_items`
- `job_events`

Important item statuses:
- `PENDING_METADATA`
- `WAITING_FOR_ATTACHMENT`
- `PROCESSING_PDF`
- `READY_TO_NOTIFY`
- `DELIVERED`
- `FAILED`

## Configuration notes

- `DATABASE_URL` defaults to `sqlite:////app/data/delivery.sqlite3`.
- `OPENALEX_EMAIL` enables metadata normalization against OpenAlex. Leave it empty to skip enrichment.
- `ZOTERO_COLLECTION_KEY` is optional. Leave it empty to work in the Zotero library root.
- `FORMCYCLE_NOTIFY_URL` is optional. Leave it empty if you are not ready to call back into FormCycle yet.
- `OCR_COMMAND_TEMPLATE` is optional. If empty, the worker uploads the original attachment PDF without OCR.
- `INTERNAL_API_TOKEN` protects the Streamlit/API operator endpoints.
