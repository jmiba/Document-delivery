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
- `request_id`
- `formcycle_submission_id`
- `user_email`
- `user_name`
- `status`
- `items[]`

Each callback item contains:
- `item_index`
- `citation_text`
- `download_url`
- `expires_on`
- `zotero_item_key`

Workflow on callback:
1. Validate `Authorization: Bearer <FORMCYCLE_NOTIFY_TOKEN>` if configured.
2. Update FormCycle process status to `DELIVERED`.
3. Send requester email with the normalized citation text and download link for each delivered item.

## Suggested FormCycle statuses

- `REQUESTED`
- `IN_PROGRESS`
- `WAITING_FOR_ATTACHMENT`
- `DELIVERED`
- `ERROR`
