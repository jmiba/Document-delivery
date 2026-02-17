# FormCycle form sketch for digitization + delivery

## Form 1: Public request intake (`digitization_request`)

Purpose: collect article request and requester contact.

Core fields:
- `req_request_id` (hidden, generated): `DD-${submissionId}`
- `req_name` (text, required)
- `req_email` (email, required)
- `req_affiliation` (text, required)
- `bib_title` (text, required)
- `bib_authors` (text, required, semicolon-separated)
- `bib_journal` (text, required)
- `bib_year` (number, required)
- `bib_volume` (text, optional)
- `bib_issue` (text, optional)
- `bib_pages` (text, optional)
- `bib_doi` (text, optional)
- `req_notes` (textarea, optional)
- `req_copyright_ack` (checkbox, required)
- `proc_status` (hidden): initial value `NEW`

Workflow on submit:
1. Set `proc_status=NEW`.
2. Send confirmation to requester.
3. Assign operator group task.
4. Persist all fields for operator processing.

## Form 2: Operator workbench (`digitization_operator`)

Purpose: let operator manage scan completion and trigger orchestration.

Core fields:
- `op_request_id` (read-only, from Form 1)
- `op_submission_id` (read-only, from Form 1)
- `op_scan_filename` (text, required)
- `op_status` (select): `IN_PROGRESS`, `SCAN_COMPLETE`, `NEEDS_CLARIFICATION`, `REJECTED`
- `op_notes` (textarea)
- `op_quality_check` (checkbox)

Workflow on `op_status=SCAN_COMPLETE`:
1. Validate `op_scan_filename` and `op_quality_check=true`.
2. Execute FormCycle HTTP action `POST /webhooks/formcycle` to orchestrator:

```json
{
  "request_id": "${op_request_id}",
  "formcycle_submission_id": "${op_submission_id}",
  "event_type": "STATUS_CHANGED",
  "status": "SCAN_COMPLETE",
  "user_email": "${req_email}",
  "user_name": "${req_name}",
  "ocr_pdf_filename": "${op_scan_filename}",
  "bibliographic_data": {
    "item_type": "journalArticle",
    "title": "${bib_title}",
    "creators": "${bib_authors}".split(";"),
    "publication_title": "${bib_journal}",
    "year": "${bib_year}",
    "volume": "${bib_volume}",
    "issue": "${bib_issue}",
    "pages": "${bib_pages}",
    "doi": "${bib_doi}"
  }
}
```

3. Add header `X-Formcycle-Secret: <FORMCYCLE_WEBHOOK_SECRET>`.
4. Set `proc_status=QUEUED_FOR_DELIVERY`.
5. On HTTP error, set `proc_status=DELIVERY_ERROR` and notify operator.

## Form 3: Delivery callback (`digitization_delivery_callback`)

Purpose: receive orchestrator result and send final requester message via FormCycle.

Endpoint input fields:
- `cb_request_id`
- `cb_formcycle_submission_id`
- `cb_status`
- `cb_user_email`
- `cb_download_url`
- `cb_expires_on`
- `cb_zotero_item_key`
- `cb_citation_json`

Workflow on callback:
1. Validate token from `Authorization: Bearer <FORMCYCLE_NOTIFY_TOKEN>`.
2. Update original request row with:
- `proc_status=DELIVERED`
- `delivery_download_url`
- `delivery_expires_on`
- `delivery_zotero_key`
3. Send requester email using FormCycle template:
- Subject: `Your digitized article is ready`
- Body includes citation metadata, download URL, and expiration date.
4. If callback fails, set `proc_status=NOTIFICATION_ERROR` for manual retry.

## Status model (shared)

Recommended statuses:
- `NEW`
- `IN_PROGRESS`
- `SCAN_COMPLETE`
- `QUEUED_FOR_DELIVERY`
- `DELIVERED`
- `NEEDS_CLARIFICATION`
- `REJECTED`
- `DELIVERY_ERROR`
- `NOTIFICATION_ERROR`
