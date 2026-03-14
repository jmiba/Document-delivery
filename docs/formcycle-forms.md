# FormCycle form sketch for SQLite + Streamlit delivery workflow

## Form 1: Public request intake (`digitization_request`)

Purpose: collect one or more article requests and hand them to FastAPI.

Core requester fields:
- `req_request_id` (hidden): generated request ID, for example `DD-[%$PROCESS_ID%]`
- `req_submission_id` (hidden): FormCycle process or record identifier
- `req_name` (text, required)
- `req_email` (email, required)
- `req_affiliation` (text, optional)
- `req_delivery_days` (number, optional)

Repeatable article fields:
- `articleTitle`
- `articleCreators`
- `articleJournal`
- `articleYear`
- `articleVolume`
- `articleIssue`
- `articlePages`
- `articleDoi`

Workflow on submit:
1. Build one JSON payload with `items` as a list of repeated article objects.
2. `POST` the payload to `/webhooks/formcycle/requests`.
3. Add header `X-Formcycle-Secret: <FORMCYCLE_WEBHOOK_SECRET>`.
4. Keep FormCycle process status as `REQUESTED`.

Target JSON shape:

```json
{
  "request_id": "[%req_request_id%]",
  "formcycle_submission_id": "[%req_submission_id%]",
  "user_email": "[%req_email%]",
  "user_name": "[%req_name%]",
  "delivery_days": "[%req_delivery_days%]",
  "items": [
    {
      "item_index": 0,
      "bibliographic_data": {
        "item_type": "journalArticle",
        "title": "Article title",
        "creators": ["Author One", "Author Two"],
        "publication_title": "Journal title",
        "year": "2024",
        "volume": "12",
        "issue": "3",
        "pages": "44-59",
        "doi": "10.1234/example"
      }
    }
  ]
}
```

## Form 2: Delivery callback (`digitization_delivery_callback`)

Purpose: receive delivery results after all items in a request are ready.

Expected callback fields:
- `token`
- `request_id`
- `formcycle_submission_id`
- `recipient_email`
- `recipient_name`
- `status`
- `mail_subject`
- `mail_html`
- `mail_text`

Workflow on callback:
1. Validate `[%token%]` against the shared secret in a workflow condition.
2. Update FormCycle process status to `DELIVERED`.
3. Send requester email with:
- recipient: `[%recipient_email%]`
- subject: `[%mail_subject%]`
- HTML body: `[%mail_html%]`
- text body: `[%mail_text%]`

The app posts callback data as a standard form submission, not nested JSON.

## Suggested FormCycle statuses

- `REQUESTED`
- `IN_PROGRESS`
- `WAITING_FOR_ATTACHMENT`
- `DELIVERED`
- `ERROR`
