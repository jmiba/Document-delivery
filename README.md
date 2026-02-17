# Document Digitization Delivery Starter

Minimal starter stack for a university-library print-digitization pipeline:
- FormCycle triggers orchestration when a scan is complete.
- FastAPI enqueues a background job.
- Worker uploads OCR PDF to Nextcloud, creates expiring share link, writes Zotero metadata, and sends delivery callback to FormCycle.
- Budibase provides a low-code operator dashboard.

## Stack

- FastAPI API service
- Python worker (RQ + Redis)
- Redis queue
- Budibase dashboard

## Project layout

```text
.
├── docker-compose.yml
├── .env.example
├── docs/
│   ├── formcycle-forms.md
│   └── budibase-setup.md
└── services/
    └── orchestrator/
        ├── Dockerfile
        ├── requirements.txt
        └── app/
            ├── clients.py
            ├── config.py
            ├── jobs.py
            ├── main.py
            ├── schemas.py
            └── worker.py
```

## Quick start (Docker)

1. Copy env file:

```bash
cp .env.example .env
```

2. Create a local scan drop folder:

```bash
mkdir -p data/scans
```

3. Start services:

```bash
docker compose up --build
```

4. Health check:

```bash
curl http://localhost:8000/health
```

## Run in a local venv (API + worker outside Docker)

1. Copy env file:

```bash
cp .env.example .env
```

2. Create local scan folder:

```bash
mkdir -p data/scans
```

3. Create and activate virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

4. Install dependencies:

```bash
pip install -r services/orchestrator/requirements.txt
```

5. Start Redis (still via Docker):

```bash
docker compose up -d redis
```

6. Run API (terminal 1):

```bash
PYTHONPATH=services/orchestrator uvicorn app.main:app --host 0.0.0.0 --port 8000
```

7. Run worker (terminal 2):

```bash
PYTHONPATH=services/orchestrator python -m app.worker
```

8. Optional: start Budibase only:

```bash
docker compose up -d budibase
```

## Webhook payload example (FormCycle -> orchestrator)

```bash
curl -X POST http://localhost:8000/webhooks/formcycle \
  -H "Content-Type: application/json" \
  -H "X-Formcycle-Secret: change-me" \
  -d '{
    "request_id": "DD-2026-0001",
    "formcycle_submission_id": "12345",
    "event_type": "STATUS_CHANGED",
    "status": "SCAN_COMPLETE",
    "user_email": "user@example.edu",
    "user_name": "Jane User",
    "ocr_pdf_filename": "DD-2026-0001.pdf",
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
  }'
```

## Notes

- `ocr_pdf_filename` must exist in `data/scans/`.
- Default link expiry is controlled by `DEFAULT_LINK_EXPIRY_DAYS` in `.env`.
- FormCycle callback endpoint is configured via `FORMCYCLE_NOTIFY_URL`.
- Budibase is exposed on `http://localhost:10000`.
