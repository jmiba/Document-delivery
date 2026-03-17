# FormCycle forms sketch

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

## Clarification form (`digitization_clarification`)

Purpose: let a requester correct one problematic bibliographic item after an operator asks for clarification.

Required hidden fields:
- `request_id`
- `item_id`
- `token`

Recommended visible fields:
- `operator_message` (read-only display)
- `item_type`
- `author`
- `workTitle`
- `container_title`
- `issued`
- `volume`
- `issue`
- `page`
- `user_note`

Important naming note:
- The app uses `title` internally.
- If your FormCycle form cannot use `title` as a field name, use `workTitle` and map it back to `title` in the clarification webhook payload.

Example open URL template:

```text
https://forms.example.edu/form/provide/3104/?request_id={request_id_q}&item_id={item_id_q}&token={token_q}&operator_message={operator_message_q}&item_type={item_type_q}&author={author_q}&workTitle={title_q}&container_title={container_title_q}&issued={issued_q}&volume={volume_q}&issue={issue_q}&page={page_q}
```

Recommended workflow on submit:
1. Submit normally in FormCycle.
2. In the workflow, do one HTTP request to `/webhooks/formcycle/clarifications`.
3. Add header `X-Formcycle-Secret: <FORMCYCLE_WEBHOOK_SECRET>`.
4. Post only fields that actually exist in the form.

Lean clarification payload shape:

```json
{
  "request_id": "[%request_id%]",
  "item_id": "[%item_id%]",
  "token": "[%token%]",
  "operator_message": "[%operator_message%]",
  "bibliographic_data": {
    "item_type": "[%item_type%]",
    "title": "[%workTitle%]",
    "creators": ["[%author%]"],
    "publication_title": "[%container_title%]",
    "year": "[%issued%]",
    "volume": "[%volume%]",
    "issue": "[%issue%]",
    "pages": "[%page%]"
  },
  "user_note": "[%user_note%]"
}
```

Why this shape:
- it matches the current working clarification form
- it avoids unresolved placeholders for fields that are not present in the form
- it lets the app re-run metadata validation after the user submits corrected data
