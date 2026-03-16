# Document Delivery Pipeline

Code-first starter for a university-library document delivery workflow:
- FormCycle sends delivery requests to FastAPI.
- FastAPI stores requests and requested items in SQLite.
- A polling worker normalizes metadata, checks Zotero, processes uploaded or existing PDF attachments, and delivers finished PDFs through Nextcloud.
- Streamlit provides the operator view for queue status, failures, and retries.
- Delivery notifications are sent directly by SMTP from the app.

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
7. Staff can upload a scan directly in Streamlit for items in `WAITING_FOR_ATTACHMENT`. As a fallback, the worker can still poll Zotero for an existing PDF attachment.
8. The worker optionally runs OCR through the native Tesseract overlay pass.
9. If the scan came through the app, the worker uploads the processed PDF back to Zotero as the canonical attachment for later reuse.
10. The worker uploads the processed PDF to Nextcloud and creates an expiring share link.
11. The worker sends the final requester email directly through SMTP when configured.

## Architecture

The active system has four roles:

- FormCycle handles intake.
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
            ├── ocr.py
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

3. If you want Streamlit authentication, create a secrets file:

```bash
mkdir -p .streamlit
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
```

The Streamlit auth model used here follows `/Users/jmittelbach/Gitlab/ai-service-chatbot`: it is Streamlit's built-in OIDC login flow, not direct SAML. If your institution exposes only SAML, place an OIDC-capable broker such as Authentik or Keycloak in front of it.

4. Start services:

```bash
docker compose up --build
```

The orchestrator image installs the OCR system dependencies itself:
- `poppler-utils`
- `tesseract-ocr`
- Tesseract language packs from `OCR_TESSERACT_LANG_PACKS`

So if you run the app with Docker Compose, you do not need to install Poppler or Tesseract on the host machine.

5. Open the operator UI:

```text
http://localhost:18501
```

6. Health check:

```bash
curl http://localhost:18000/health
```

## FormCycle webhook payload example

This is the preferred shape for new FormCycle requests:

```bash
curl -X POST http://localhost:18000/webhooks/formcycle/requests \
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
- Items in `WAITING_FOR_ATTACHMENT` can now take a PDF upload in the Streamlit UI. The app stores the uploaded scan locally, OCRs it if needed, pushes the processed PDF back to Zotero, and then continues delivery.
- Uploaded scans are visible in the item table as `App upload: <filename>`. Operators can replace or remove an uploaded scan before delivery if needed.
- `CITATION_STYLE` controls the CSL style Zotero uses when the app renders bibliography entries for the delivery mail.
- `CITATION_LOCALE_DE`, `CITATION_LOCALE_EN`, and `CITATION_LOCALE_PL` map the FormCycle form language to the Zotero citation locale.
- `SMTP_HOST` enables direct email delivery from the app.
- `SMTP_FROM_EMAIL` is required when SMTP is enabled.
- `SMTP_USE_TLS=true` with port `587` is the normal setup for authenticated submission.
- Streamlit authentication is configured through `/Users/jmittelbach/Github/Document delivery/.streamlit/secrets.toml` using Streamlit's OIDC settings (`redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`). Named providers are supported via `[auth.<provider>]`; this repo reads an optional `provider` key from `[auth]` and passes it to `st.login(provider)`.
- Email templates for German, English, and Polish are stored in SQLite and editable in the Streamlit `Email templates` page. Available placeholders are `{request_id}`, `{submission_id}`, `{user_email}`, `{user_name}`, `{greeting_name}`, `{item_count}`, `{items_text}`, `{items_html}`, and `{sender_name}`.
- `OCR_TESSERACT_LANG_PACKS` is a Docker build-time list of installed Tesseract language packs. Rebuild the image after changing it.
- `OCR_MODE=tesseract_overlay` enables the built-in Tesseract text-layer pass.
- `OCR_LANGUAGE_MODE=auto` samples a few pages, detects the primary language, and switches to a narrower OCR bundle for the full overlay pass.
- `OCR_LANGUAGE` controls the runtime fallback OCR language set, for example `deu+eng+pol`.
- `OCR_LANGUAGE_DETECT_SEED` controls the broader seed bundle used for the detection pass.
- `OCR_LANGUAGE_DETECT_PAGES` controls how many leading pages are sampled for language detection.
- `OCR_DPI` controls PDF rasterization resolution before OCR.
- `OCR_POPPLER_PATH` and `OCR_TESSERACT_CMD` are only needed if you run the worker outside Docker and the binaries are not on `PATH`.
- `OCR_SKIP_IF_TEXT_LAYER=true` skips OCR when the existing PDF text layer looks usable by heuristic checks.
- `OCR_TEXT_LAYER_MIN_CHARS_PER_PAGE`, `OCR_TEXT_LAYER_MIN_PAGE_RATIO`, and `OCR_TEXT_LAYER_MIN_ALPHA_RATIO` tune that heuristic.
- `INTERNAL_API_TOKEN` protects the Streamlit/API operator endpoints.
- When your FormCycle request form is multilingual, include the active language in the webhook payload, for example `"language": "[%lang%]"`, so the delivery mail and Zotero citation locale match the form language.
