# Document Delivery Pipeline

Code-first starter for a university-library document delivery workflow:

- FormCycle sends document-delivery requests to a FastAPI webhook.
- FastAPI stores requests and requested items in SQLite.
- A worker normalizes metadata, checks Zotero, processes PDF attachments, uploads delivered files to Nextcloud, and sends notification email through SMTP.
- Streamlit provides the operator UI for review, retries, uploads, templates, and statistics.

## Services

`docker compose` starts three containers from the same image:

- `api`: FastAPI on `http://localhost:18000`
- `worker`: background processing worker
- `streamlit`: operator UI on `http://localhost:18501`

Persistent data is stored in bind mounts:

- `./data/app` -> SQLite database and working files
- `./data/scans` -> optional read-only drop folder for incoming PDFs
- `./.streamlit` -> optional Streamlit auth configuration

## Installation

### 1. Prerequisites

Install these on the host:

- `git`
- Docker Desktop or Docker Engine with Compose v2 (`docker compose version`)

You also need network access from the containers to the systems you configure in `.env`, typically:

- Zotero Web API
- Nextcloud WebDAV
- SMTP submission server
- OpenAlex and Crossref
- optional clarification form endpoint

### 2. Clone the repository

```bash
git clone https://github.com/jmiba/Document-delivery.git
cd Document-delivery
```

### 3. Create local bind-mount directories

```bash
mkdir -p data/app data/scans
```

`data/app` is required because it stores the SQLite database and temporary processing files. `data/scans` is optional at runtime, but creating it up front keeps the Docker mounts predictable.

### 4. Create the configuration files

Create the application config:

```bash
cp .env.example .env
```

`.env.example` is a hidden file because its name starts with a dot. If you do not see it in a normal directory listing, check from the repository root with:

```bash
pwd
ls -la
```

You should run the copy command from the directory that contains `docker-compose.yml` and `README.md`.

If you want OIDC login in Streamlit, also create the auth secrets file:

```bash
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
```

If you do not want Streamlit login for local development, leave `.streamlit/secrets.toml` absent or incomplete. The UI will start without authentication.

### 5. Edit `.env`

Before starting the stack, replace every placeholder value in `.env`. The minimum values required for a working end-to-end deployment are:

- `NEXTCLOUD_BASE_URL`
- `NEXTCLOUD_USERNAME`
- `NEXTCLOUD_PASSWORD`
- `ZOTERO_LIBRARY_ID`
- `ZOTERO_API_KEY`
- `FORMCYCLE_WEBHOOK_SECRET`
- `INTERNAL_API_TOKEN`
- `SMTP_HOST`
- `SMTP_FROM_EMAIL`

For the clarification workflow, also set:

- `CLARIFICATION_FORM_URL_TEMPLATE`
- `CLARIFICATION_TOKEN_SECRET`

Details for every `.env` key are documented in [`.env configuration`](#env-configuration).

### 6. Start the stack with Docker

```bash
docker compose up -d --build
```

This builds `services/orchestrator/Dockerfile` once and starts:

- `api`
- `worker`
- `streamlit`

The image already installs:

- `poppler-utils`
- `tesseract-ocr`
- all Tesseract language packs listed in `OCR_TESSERACT_LANG_PACKS`

If you use Docker Compose, you do not need Poppler or Tesseract on the host.

### 7. Verify the installation

Check container status:

```bash
docker compose ps
```

Check API health:

```bash
curl http://localhost:18000/health
```

Open the operator UI:

```text
http://localhost:18501
```

Tail logs when needed:

```bash
docker compose logs -f api worker streamlit
```

### 8. Stop or rebuild later

Stop the stack:

```bash
docker compose down
```

Restart without forcing a rebuild:

```bash
docker compose up -d
```

Rebuild after code changes or Docker build-arg changes:

```bash
docker compose up -d --build
```

`OCR_TESSERACT_LANG_PACKS` is a Docker build argument, not a normal runtime setting. Any change to that variable requires `--build`.

## Docker setup details

The compose file is `docker-compose.yml`. It defines:

- `api`
  - runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`
  - publishes host port `18000`
  - reads environment from `.env`
  - mounts `./data/scans` at `/scans` read-only
  - mounts `./data/app` at `/app/data`
- `worker`
  - runs `python -m app.worker`
  - reads environment from `.env`
  - mounts the same `scans` and `app` volumes as `api`
- `streamlit`
  - runs `streamlit run app/ui.py --server.address 0.0.0.0 --server.port 8501`
  - publishes host port `18501`
  - reads environment from `.env`
  - injects `API_BASE_URL=http://api:8000`
  - mounts `./.streamlit` at `/app/.streamlit` read-only
  - mounts `./data/app` at `/app/data`

All three services are built from `services/orchestrator/Dockerfile`.

## `.env` configuration

The API and worker load settings from `.env` through Pydantic settings. Compose also passes `.env` into the Streamlit container. Variable names are case-insensitive in the application code.

Important behavior:

- If `FORMCYCLE_WEBHOOK_SECRET` is empty, the webhook endpoints accept requests without header authentication.
- If `INTERNAL_API_TOKEN` is empty, the operator API endpoints accept requests without the `X-Internal-Token` header.
- `NEXTCLOUD_BASE_URL`, `NEXTCLOUD_USERNAME`, `NEXTCLOUD_PASSWORD`, `ZOTERO_LIBRARY_ID`, and `ZOTERO_API_KEY` are required by the application settings model. If they are missing, `api` and `worker` will fail to start.
- `SMTP_*` is optional only if you do not need delivery, clarification, or rejection emails. The workflow is incomplete without it.
- `OCR_TESSERACT_LANG_PACKS` is consumed at Docker build time by Compose and the Dockerfile, not by the runtime settings model.

### Core runtime

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `DATABASE_URL` | No | `sqlite:////app/data/delivery.sqlite3` | SQLAlchemy database URL. In Docker, this should normally stay on `/app/data` so the file persists in `./data/app`. |
| `SCAN_INPUT_DIR` | No | `/scans` | Directory for manually dropped PDFs. In Compose this maps to `./data/scans`. |
| `WORK_DIR` | No | `/app/data/work` | Scratch space for OCR, delivery assembly, and temporary files. |
| `WORKER_POLL_INTERVAL_SECONDS` | No | `15` | How often the worker polls for normal queued work. |
| `ATTACHMENT_POLL_INTERVAL_SECONDS` | No | `300` | How often the worker checks waiting items for external attachments. |
| `NOTIFICATION_RETRY_INTERVAL_SECONDS` | No | `900` | Retry delay for notification work. |
| `DEFAULT_LINK_EXPIRY_DAYS` | No | app default `14`; `.env.example` sets `7` | Lifetime of generated Nextcloud share links. |
| `NORMALIZATION_AUTO_ACCEPT_THRESHOLD` | No | `0.92` | Confidence threshold above which metadata can bypass manual review. |

### Metadata resolution

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `OPENALEX_EMAIL` | Recommended | empty | Contact email sent to OpenAlex. Improves API etiquette and traceability. |
| `CROSSREF_MAILTO` | Recommended | empty | Contact email for Crossref queries. |
| `GBV_SRU_URL` | No | `https://sru.k10plus.de/gvk` | K10plus/GBV SRU endpoint for book-like metadata lookups. |
| `RESOLUTION_PRIORITY_LOBID` | No | `1` | Lower number means higher precedence when Lobid returns a valid match. |
| `RESOLUTION_PRIORITY_GBV` | No | `2` | Precedence for GBV/K10plus results. |
| `RESOLUTION_PRIORITY_CROSSREF` | No | `3` | Precedence for Crossref results. |
| `RESOLUTION_PRIORITY_OPENALEX` | No | `4` | Precedence for OpenAlex results. |

### Nextcloud delivery target

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `NEXTCLOUD_BASE_URL` | Yes | none | Base Nextcloud URL, for example `https://nextcloud.example.edu`. |
| `NEXTCLOUD_USERNAME` | Yes | none | Account used for WebDAV upload and share creation. |
| `NEXTCLOUD_PASSWORD` | Yes | none | Password or app password for the Nextcloud account. |
| `NEXTCLOUD_DAV_BASE_PATH` | No | `/remote.php/dav/files/{username}` | WebDAV base path. `{username}` is expanded with `NEXTCLOUD_USERNAME`. |
| `NEXTCLOUD_ROOT_PATH` | No | `/Digitization` in code; `.env.example` sets `/Document-Delivery` | Root folder inside Nextcloud where delivered files are stored. |

### Zotero integration

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `ZOTERO_LIBRARY_TYPE` | No | `user` in code; `.env.example` sets `group` | Must be `user` or `group`. |
| `ZOTERO_LIBRARY_ID` | Yes | none | Numeric Zotero user or group library ID. |
| `ZOTERO_API_KEY` | Yes | none | Zotero API key with access to the configured library. |
| `ZOTERO_COLLECTION_KEY` | No | empty | Optional collection to work in. Leave empty to use the library root. |
| `ZOTERO_IN_PROCESS_TAG` | No | `in process` | Tag applied to records created by this pipeline before delivery is complete. |
| `CITATION_STYLE` | No | `apa` | CSL style used when rendering citations in delivery mail. |
| `CITATION_LOCALE_DE` | No | `de-DE` | Zotero locale used for German requests. |
| `CITATION_LOCALE_EN` | No | `en-US` | Zotero locale used for English requests. |
| `CITATION_LOCALE_PL` | No | `pl-PL` | Zotero locale used for Polish requests. |

### Webhook and operator security

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `FORMCYCLE_WEBHOOK_SECRET` | Strongly recommended | empty | Expected value of the `X-Formcycle-Secret` header on webhook requests. Empty means no webhook authentication. |
| `INTERNAL_API_TOKEN` | Strongly recommended | empty | Expected value of the `X-Internal-Token` header on operator API endpoints. Streamlit forwards this header when the variable is set. Empty means no operator API authentication. |
| `CLARIFICATION_FORM_URL_TEMPLATE` | Required for clarification flow | empty | URL template used to build links to the external clarification form. |
| `CLARIFICATION_TOKEN_SECRET` | Required for clarification flow | empty | Secret used to sign clarification links. If clarification is requested without it, the app raises a runtime error. |
| `CLARIFICATION_TOKEN_TTL_HOURS` | No | `168` | Validity period for clarification links. |

### SMTP delivery

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `SMTP_HOST` | Required for any email sending | empty | SMTP host used for delivery, clarification, and rejection mail. |
| `SMTP_PORT` | No | `587` | SMTP port. |
| `SMTP_USERNAME` | Usually yes | empty | Username for SMTP authentication. |
| `SMTP_PASSWORD` | Usually yes | empty | Password for SMTP authentication. |
| `SMTP_USE_TLS` | No | `true` | Enables STARTTLS. Typical setting for port `587`. |
| `SMTP_USE_SSL` | No | `false` | Enables implicit TLS. Do not set both `SMTP_USE_TLS` and `SMTP_USE_SSL` to `true`. |
| `SMTP_FROM_EMAIL` | Required when `SMTP_HOST` is set | empty | Sender address. The app raises an error if SMTP is enabled without this value. |
| `SMTP_FROM_NAME` | No | `Bibliothek` in code; `.env.example` sets `Universitaetsbibliothek` | Display name in the From header. |
| `SMTP_REPLY_TO` | No | empty | Optional Reply-To header. |

### OCR and PDF processing

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `OCR_TESSERACT_LANG_PACKS` | No | Docker build arg default `eng deu pol`; `.env.example` sets a longer list | Space-separated language packs installed into the image during `docker compose build`. Rebuild required after changes. |
| `OCR_MODE` | No | `off` in code; `.env.example` sets `tesseract_overlay` | OCR mode. `tesseract_overlay` adds a text layer to scanned PDFs. |
| `OCR_LANGUAGE_MODE` | No | `manual` in code; `.env.example` sets `auto` | `auto` samples early pages and narrows the OCR language set; `manual` always uses `OCR_LANGUAGE`. |
| `OCR_LANGUAGE` | No | `deu+eng+pol` | Runtime fallback Tesseract language bundle. |
| `OCR_LANGUAGE_DETECT_SEED` | No | `eng+deu+pol+fra` | Initial broad language bundle used during automatic detection. |
| `OCR_LANGUAGE_DETECT_PAGES` | No | `2` | Number of leading pages sampled for language detection. |
| `OCR_DPI` | No | `300` | Rasterization DPI before OCR. |
| `OCR_POPPLER_PATH` | No | empty | Override path to Poppler binaries when not running inside Docker. |
| `OCR_TESSERACT_CMD` | No | empty | Override path to the `tesseract` executable when not running inside Docker. |
| `OCR_SKIP_IF_TEXT_LAYER` | No | `true` | Skip OCR if the existing text layer appears usable. |
| `OCR_TEXT_LAYER_MIN_CHARS_PER_PAGE` | No | `80` | Minimum average characters per page for the existing text layer heuristic. |
| `OCR_TEXT_LAYER_MIN_PAGE_RATIO` | No | `0.5` | Minimum fraction of pages that must contain usable text. |
| `OCR_TEXT_LAYER_MIN_ALPHA_RATIO` | No | `0.6` | Minimum alphabetic-character ratio used by the text-layer heuristic. |

### Example `.env` workflow

The intended workflow is:

```bash
cp .env.example .env
```

Then edit `.env` and replace all placeholders:

- every `change-me`
- every example hostname such as `nextcloud.example.edu`
- every example email address such as `library@example.edu`
- every placeholder library ID

Do not commit the filled `.env` file.

## `.streamlit/secrets.toml` configuration

The repository does not use a root-level `secrets.toml`. The only supported file is:

- `.streamlit/secrets.toml`

This file is mounted only into the `streamlit` container. The API and worker do not read it.

### When you need it

Use `.streamlit/secrets.toml` only if you want OIDC login in the Streamlit operator UI.

If the file is missing, or if `[auth]` does not contain both `redirect_uri` and `cookie_secret`, the UI runs without login.

### File structure

Start from the example:

```bash
cp .streamlit/secrets.example.toml .streamlit/secrets.toml
```

Example:

```toml
[auth]
redirect_uri = "http://localhost:18501/oauth2callback"
cookie_secret = "replace-with-a-long-random-secret"
provider = "authentik"

[auth.authentik]
client_id = "replace-with-client-id"
client_secret = "replace-with-client-secret"
server_metadata_url = "https://auth.example.edu/application/o/document-delivery/.well-known/openid-configuration"
client_kwargs = { prompt = "login" }
```

### Meaning of each key

| Key | Required | Meaning |
| --- | --- | --- |
| `[auth]` | Yes for auth | Top-level Streamlit auth section. |
| `redirect_uri` | Yes for auth | OIDC callback URL registered with the identity provider. For local Compose, this is normally `http://localhost:18501/oauth2callback`. |
| `cookie_secret` | Yes for auth | Long random secret used by Streamlit to sign auth cookies. |
| `provider` | Optional | Provider name passed by this app to `st.login(provider)`. If omitted, the app calls `st.login()` without a named provider. |
| `[auth.<provider>]` | Yes for the named provider | Provider-specific OIDC config section. The section name must match `provider`. |
| `client_id` | Yes for auth | OIDC client ID. |
| `client_secret` | Yes for auth | OIDC client secret. |
| `server_metadata_url` | Yes for auth | OIDC discovery document URL (`.well-known/openid-configuration`). |
| `client_kwargs` | Optional | Extra OIDC client parameters understood by Streamlit or the underlying auth stack. |

If your institution offers only SAML, place an OIDC-capable broker such as Authentik or Keycloak in front of it and configure Streamlit against that broker.

Do not commit the filled `.streamlit/secrets.toml` file.

## Request webhook example

The preferred FormCycle request payload is:

```bash
curl -X POST http://localhost:18000/webhooks/formcycle/requests \
  -H "Content-Type: application/json" \
  -H "X-Formcycle-Secret: <FORMCYCLE_WEBHOOK_SECRET>" \
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

The API also accepts the older single-item shape with top-level `bibliographic_data`. Repeated webhook calls with the same `request_id` append items to the existing request.

## Example FormCycle exports

The repository contains reusable FormCycle template exports in `docs/examples/formcycle/`.

Provided files:

- `docs/examples/formcycle/request-form.json`
  - end-user request form
- `docs/examples/formcycle/request-workflow.json`
  - request workflow that posts submitted items to `POST /webhooks/formcycle/requests`
- `docs/examples/formcycle/clarification-form.json`
  - clarification form used when the operator asks the user to correct or complete metadata
- `docs/examples/formcycle/clarification-workflow.json`
  - clarification workflow that posts corrected data to `POST /webhooks/formcycle/clarifications`

These files are meant as importable templates for a new FormCycle setup. They preserve the field names, workflow parameters, and payload structure expected by this app.

Before importing them into your own FormCycle instance, adjust the environment-specific values:

- webhook URLs
- `X-Formcycle-Secret`
- operator notification recipients
- operator UI base URL

The templates are a working starting point, not a complete production configuration. You should still review:

- language texts and institutional wording
- recipient addresses
- status transitions
- authentication and access rules inside FormCycle

## Runtime notes

- Item statuses include `PENDING_METADATA`, `NEEDS_REVIEW`, `AWAITING_USER`, `PENDING_ZOTERO`, `WAITING_FOR_ATTACHMENT`, `PROCESSING_PDF`, `READY_TO_NOTIFY`, `DELIVERED`, and `FAILED`.
- Uploaded PDFs through Streamlit are stored locally first, then OCR-processed if configured, then pushed back to Zotero as the canonical attachment.
- Delivered PDFs get a generated front page before being uploaded to Nextcloud.
- Delivery, clarification, and rejection templates are stored in SQLite and editable from Streamlit.
- The `Statistics` page aggregates daily, weekly, monthly, or yearly request cohorts.

## Clarification flow

- Operators can request clarification for items in `NEEDS_REVIEW`.
- The app sends the clarification email through SMTP and changes the item status to `AWAITING_USER`.
- The clarification link must point to an external form that posts back to `POST /webhooks/formcycle/clarifications`.

Minimal clarification URL template:

```env
CLARIFICATION_FORM_URL_TEMPLATE=https://forms.example.edu/form/provide/3104/?request_id={request_id_q}&item_id={item_id_q}&token={token_q}&operator_message={operator_message_q}&item_type={item_type_q}&author={author_q}&workTitle={title_q}&container_title={container_title_q}&issued={issued_q}&volume={volume_q}&issue={issue_q}&page={page_q}
```

Supported placeholders:

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

The clarification form payload must include:

- `request_id`
- `item_id`
- `token`
- corrected bibliographic fields
- optional `user_note`
- optional `operator_message`

After clarification is received, the corrected fields are written back to the item and the item returns to `PENDING_METADATA` for validation.
