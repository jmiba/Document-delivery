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
10. The worker prepends a delivery-only cover page with order date, delivery date, and bibliographic data, then uploads that delivered PDF to Nextcloud and creates an expiring share link.
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

1. Prerequisites

- Docker Engine
- Docker Compose v2

2. Create local data folders:

```bash
mkdir -p data/scans data/app
```

3. Copy env file:

```bash
cp .env.example .env
```

4. Edit `/Users/jmittelbach/Github/Document delivery/.env`.

At minimum, set real values for:
- `FORMCYCLE_WEBHOOK_SECRET`
- `INTERNAL_API_TOKEN`
- `CLARIFICATION_TOKEN_SECRET`
- `NEXTCLOUD_BASE_URL`
- `NEXTCLOUD_USERNAME`
- `NEXTCLOUD_PASSWORD`
- `ZOTERO_LIBRARY_ID`
- `ZOTERO_API_KEY`
- `SMTP_HOST`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`

If you use the clarification flow, also set:
- `CLARIFICATION_FORM_URL_TEMPLATE`

5. If you want Streamlit authentication, create a secrets file:

```bash
mkdir -p .streamlit
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
```

The Streamlit auth model used here follows `/Users/jmittelbach/Gitlab/ai-service-chatbot`: it is Streamlit's built-in OIDC login flow, not direct SAML. If your institution exposes only SAML, place an OIDC-capable broker such as Authentik or Keycloak in front of it.

If you do not want auth for local development, skip this step. Without a configured `/Users/jmittelbach/Github/Document delivery/.streamlit/secrets.toml`, the Streamlit UI starts without login.

6. Start services:

```bash
docker compose up --build
```

The orchestrator image installs the OCR system dependencies itself:
- `poppler-utils`
- `tesseract-ocr`
- Tesseract language packs from `OCR_TESSERACT_LANG_PACKS`

So if you run the app with Docker Compose, you do not need to install Poppler or Tesseract on the host machine.

7. Open the operator UI:

```text
http://localhost:18501
```

8. Health check:

```bash
curl http://localhost:18000/health
```

9. Common update workflow

- normal config changes:

```bash
docker compose up -d --build
```

- after changing `OCR_TESSERACT_LANG_PACKS`, rebuild is required because the language packs are installed into the image at build time:

```bash
docker compose up -d --build
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
- `AWAITING_USER`
- `PENDING_ZOTERO`
- `WAITING_FOR_ATTACHMENT`
- `PROCESSING_PDF`
- `READY_TO_NOTIFY`
- `DELIVERED`
- `FAILED`

## Configuration notes

- `DATABASE_URL` defaults to `sqlite:////app/data/delivery.sqlite3`.
- `SCAN_INPUT_DIR` is the bind-mounted input directory for manually provided scans. The preferred operator flow is now upload through Streamlit, not dropping files here.
- `WORK_DIR` stores temporary OCR, delivery, and upload working files.
- `OPENALEX_EMAIL` enables OpenAlex as a normalization source.
- `CROSSREF_MAILTO` is optional but recommended for polite Crossref API usage.
- `GBV_SRU_URL` configures the K10plus/GBV SRU endpoint used for book and book-section lookups.
- `RESOLUTION_PRIORITY_LOBID`, `RESOLUTION_PRIORITY_GBV`, `RESOLUTION_PRIORITY_CROSSREF`, and `RESOLUTION_PRIORITY_OPENALEX` control which validated source wins when multiple sources match. For `bookSection`, Lobid and GBV are evaluated ahead of Crossref/OpenAlex by default.
- `NORMALIZATION_AUTO_ACCEPT_THRESHOLD` controls when a metadata match can bypass human review.
- `ZOTERO_COLLECTION_KEY` is optional. Leave it empty to work in the Zotero library root.
- Items in `WAITING_FOR_ATTACHMENT` can now take a PDF upload in the Streamlit UI. The app stores the uploaded scan locally, OCRs it if needed, pushes the processed PDF back to Zotero, and then continues delivery.
- Uploaded scans are visible in the item table as `App upload: <filename>`. Operators can replace or remove an uploaded scan before delivery if needed.
- Delivered PDFs include a generated front page with current delivery metadata. The canonical Zotero attachment remains unchanged apart from optional OCR applied during initial app-upload processing.
- `CITATION_STYLE` controls the CSL style Zotero uses when the app renders bibliography entries for the delivery mail.
- `CITATION_LOCALE_DE`, `CITATION_LOCALE_EN`, and `CITATION_LOCALE_PL` map the FormCycle form language to the Zotero citation locale.
- `SMTP_HOST` enables direct email delivery from the app.
- `SMTP_FROM_EMAIL` is required when SMTP is enabled.
- `CLARIFICATION_FORM_URL_TEMPLATE` configures the user-facing clarification form link the app sends when an operator requests clarification.
- `CLARIFICATION_TOKEN_SECRET` signs clarification links so users can only answer for the intended request item.
- `CLARIFICATION_TOKEN_TTL_HOURS` controls how long a clarification link remains valid.
- `SMTP_USE_TLS=true` with port `587` is the normal setup for authenticated submission.
- If you use Exchange or a university SMTP submission server, `SMTP_USE_TLS=true` with `SMTP_PORT=587` is usually the correct setup. Do not enable `SMTP_USE_TLS` and `SMTP_USE_SSL` at the same time.
- Streamlit authentication is configured through `/Users/jmittelbach/Github/Document delivery/.streamlit/secrets.toml` using Streamlit's OIDC settings (`redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, `server_metadata_url`). Named providers are supported via `[auth.<provider>]`; this repo reads an optional `provider` key from `[auth]` and passes it to `st.login(provider)`.
- Streamlit light/dark colors are defined in `/Users/jmittelbach/Github/Document delivery/.streamlit/config.toml`.
- Delivery and clarification templates for German, English, and Polish are stored in SQLite and editable in the Streamlit `Email templates` page.
- The Streamlit `Statistics` page aggregates request cohorts by month or year and shows request volume, fulfillment rate, average fulfillment time, metadata validation outcomes, clarification requests, and Zotero item reuse.
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

## Clarification flow

- Operators can request clarification from the Streamlit review screen for items in `NEEDS_REVIEW`.
- The app sends the clarification email itself via SMTP and marks the item as `AWAITING_USER`.
- The clarification link should point to a separate FormCycle form that posts back to `POST /webhooks/formcycle/clarifications`.
- The clarification link template can prefill the form with the current item metadata. A working minimal template is:

```env
CLARIFICATION_FORM_URL_TEMPLATE=https://forms.example.edu/form/provide/3104/?request_id={request_id_q}&item_id={item_id_q}&token={token_q}&operator_message={operator_message_q}&item_type={item_type_q}&author={author_q}&workTitle={title_q}&container_title={container_title_q}&issued={issued_q}&volume={volume_q}&issue={issue_q}&page={page_q}
```

- Supported placeholders are:
  - `{request_id}`, `{request_id_q}`
  - `{item_id}`, `{item_id_q}`
  - `{token}`, `{token_q}`
  - `{operator_message}`, `{operator_message_q}`
  - `{item_type}`, `{item_type_q}`
  - `{author}`, `{author_q}`
  - `{title}`, `{title_q}`
  - `{container_title}`, `{container_title_q}`
  - `{issued}`, `{issued_q}`
  - `{volume}`, `{volume_q}`
  - `{issue}`, `{issue_q}`
  - `{page}`, `{page_q}`
  - `{DOI}`, `{DOI_q}`
  - `{publisher}`, `{publisher_q}`
  - `{place}`, `{place_q}`
  - `{series}`, `{series_q}`
  - `{edition}`, `{edition_q}`
  - `{isbn}`, `{isbn_q}`
- The clarification payload must include:
  - `request_id`
  - `item_id`
  - `token`
  - corrected bibliographic fields
  - optional `user_note`
  - optional `operator_message`
- After clarification is received, the corrected fields are written back to the item and the item is returned to `PENDING_METADATA` so the resolver pipeline validates the clarified data again.
