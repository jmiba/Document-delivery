# Architecture

This repository uses a code-first document delivery stack.

## Active components

- `FormCycle`
  - public intake form
  - optional follow-up form for clarification, confirmation, or redelivery
- `FastAPI`
  - webhook ingestion from FormCycle
  - internal operator API for Streamlit
- `SQLite`
  - persistent request, item, and event store
- `Worker`
  - metadata resolution
  - Zotero item reuse/creation
  - PDF attachment polling
  - optional OCR
  - Nextcloud upload and share-link generation
  - SMTP delivery notification
- `Streamlit`
  - operator review UI
  - retry controls
  - event inspection

## Metadata resolution

The resolver is source-prioritized and item-type aware.

- For `journalArticle`
  - `Crossref`
  - `OpenAlex`
- For `bookSection` and `book`
  - `Lobid`
  - `GBV/K10plus`
  - `Crossref`
  - `OpenAlex`

Validated candidates are ranked by configured source priority and score.
Lower-confidence cases stay in `NEEDS_REVIEW` until approved in Streamlit.

## Delivery flow

1. FormCycle posts a request to FastAPI.
2. FastAPI stores the request and its items in SQLite.
3. The worker normalizes metadata.
4. If required, an operator approves or edits metadata in Streamlit.
5. The worker reuses or creates the Zotero item.
6. The worker waits until a PDF attachment exists in Zotero.
7. The worker optionally runs OCR.
8. The worker uploads the PDF to Nextcloud and creates an expiring share link.
9. When all items in the request are ready, the app sends the delivery email by SMTP.
10. The email can link back to a FormCycle follow-up form for later conversation with the user.

## Explicit non-components

The repository no longer uses:

- Budibase
- Redis
- Budibase automations or Budibase data tables

Any earlier Budibase setup referenced in prior conversations is obsolete and not part of the current runtime.
