# FormCycle intake form sketch

## Public request intake (`digitization_request`)

Purpose: collect one or more bibliographic requests and hand them to FastAPI.

Core requester fields:
- `req_request_id` (hidden): generated request ID, for example `DD-[%$PROCESS_ID%]`
- `req_submission_id` (hidden): FormCycle process or record identifier
- `req_name` (text, required)
- `req_email` (email, required)
- `req_affiliation` (text, optional)
- `req_delivery_days` (number, optional)

Repeatable bibliographic fields:
- `item_type`
- `author`
- `title`
- `container_title`
- `issued`
- `volume`
- `issue`
- `page`
- `DOI`

Workflow on submit:
1. Build one JSON payload with `items` as a list of repeated bibliographic objects.
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
  "language": "[%lang%]",
  "delivery_days": "[%req_delivery_days%]",
  "items": [
    {
      "item_index": 0,
      "bibliographic_data": {
        "item_type": "[%item_type%]",
        "title": "[%title%]",
        "creators": ["[%author%]"],
        "publication_title": "[%container_title%]",
        "year": "[%issued%]",
        "volume": "[%volume%]",
        "issue": "[%issue%]",
        "pages": "[%page%]",
        "doi": "[%DOI%]"
      }
    }
  ]
}
```

The public intake form should pass the active FormCycle language through `[%lang%]` so:
- the delivery email is sent in the same language as the form
- Zotero renders bibliography entries with the matching citation locale

## Recommended FormCycle states

- `REQUESTED`
- `IN_PROGRESS`
- `WAITING_FOR_ATTACHMENT`
- `DELIVERED`
- `ERROR`
