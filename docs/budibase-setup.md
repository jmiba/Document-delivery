# Budibase starter sketch for operator dashboard

## Goal
Provide operators with queue visibility and retry controls while orchestration stays in FastAPI + worker.

## Suggested data tables in Budibase

1. `requests`
- `request_id` (string, primary)
- `submission_id` (string)
- `requester_email` (string)
- `title` (string)
- `journal` (string)
- `year` (string)
- `status` (string)
- `scan_filename` (string)
- `job_id` (string)
- `download_url` (string)
- `expires_on` (date)
- `zotero_item_key` (string)
- `updated_at` (datetime)

2. `job_events`
- `id` (auto)
- `request_id` (string)
- `job_id` (string)
- `event` (string)
- `payload` (long text/json)
- `created_at` (datetime)

## Suggested pages

1. `Queue`
- Filter: `status in (NEW, IN_PROGRESS, DELIVERY_ERROR, NOTIFICATION_ERROR)`
- Action buttons:
- `Open request`
- `Trigger delivery` (calls `POST /deliver/manual`)
- `Retry failed job` (calls `POST /jobs/{job_id}/retry`)

2. `Delivered`
- Filter: `status=DELIVERED`
- Columns: citation fields, link expiry, Zotero item key.

3. `Failures`
- Filter: `status in (DELIVERY_ERROR, NOTIFICATION_ERROR)`
- Show latest worker/API error from `job_events`.

## API datasource in Budibase

Configure REST datasource to orchestrator:
- Base URL: `http://api:8000` (if running in same Docker network) or external host.
- Header: `X-Internal-Token: <INTERNAL_API_TOKEN>`

Actions:
- `POST /deliver/manual`
- `GET /jobs/{{ job_id }}`
- `POST /jobs/{{ job_id }}/retry`

## Security
- Restrict dashboard to operator group.
- Keep the internal token in Budibase secret variables.
- Do not expose manual/retry endpoints without token checks.
