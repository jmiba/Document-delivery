# FormCycle form sketch for SMTP delivery + FormCycle follow-up workflow

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
  "language": "[%lang%]",
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

## Form 2: Follow-up interaction (`digitization_followup`)

Purpose: handle user conversation after the app has already sent the delivery email.

Recommended hidden or prefilled fields:
- `request_id`
- `formcycle_submission_id`
- `user_email`

Visible user fields:
- `interaction_type` (selection)
  - `clarification`
  - `confirmation`
  - `redelivery`
- `message` (textarea)
- `corrected_citation` (textarea, optional)
- `preferred_email` (email, optional)

Recommended prefill via URL parameters:
- `request_id`
- `formcycle_submission_id`
- `user_email`

Example link template for the app:

```text
https://forms.europa-uni.de/form/provide/<followup-form-id>/?request_id={request_id_q}&formcycle_submission_id={formcycle_submission_id_q}&user_email={user_email_q}
```

Set this in `.env` as:

```env
FORMCYCLE_FOLLOWUP_URL_TEMPLATE=https://forms.europa-uni.de/form/provide/<followup-form-id>/?request_id={request_id_q}&formcycle_submission_id={formcycle_submission_id_q}&user_email={user_email_q}
```

The app will insert this personalized link into the delivery email.

The public intake form should pass the active FormCycle language through `[%lang%]` so:
- the delivery email is sent in the same language as the form
- Zotero renders bibliography entries with the matching citation locale

## Recommended FormCycle states

For the intake form:
- `REQUESTED`
- `IN_PROGRESS`
- `WAITING_FOR_ATTACHMENT`
- `DELIVERED`
- `ERROR`

For the follow-up form:
- `OPEN`
- `CLARIFICATION_REQUESTED`
- `USER_CONFIRMED`
- `REDELIVERY_REQUESTED`
- `CLOSED`

## Recommended follow-up workflow

1. User receives delivery email from the app.
2. Email contains personalized FormCycle follow-up link.
3. User opens follow-up form.
4. FormCycle workflow routes by `interaction_type`.

Suggested routing:
- `clarification`
  - state: `CLARIFICATION_REQUESTED`
  - notify staff mailbox
- `confirmation`
  - state: `USER_CONFIRMED`
  - optional thank-you message
- `redelivery`
  - state: `REDELIVERY_REQUESTED`
  - notify staff mailbox and optionally ask for reason

The preferred architecture is:
- app sends delivery email
- FormCycle handles user follow-up interaction
