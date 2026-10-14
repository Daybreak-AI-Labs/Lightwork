# lightwork-client

A small Python client for the Lightwork dashboard REST API (`/api/v1`). It uses
only the standard library (`urllib`, `json`, `ssl`, `ipaddress`), so it has no
dependencies and runs on Python 3.10 to 3.12.

**Not published.** This package is a checkout-only helper. It is not on PyPI,
it is not a member of the uv workspace, and it is not part of the release
cohort. Its version (`0.1.0`) is not tied to the Lightwork packages.

The REST contract itself is documented in [`docs/api.md`](../../docs/api.md).
The live spec is at `/openapi.json` on your dashboard.

## Install from a checkout

From the repository root, in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install ./sdks/python-client
```

To work on the client itself, use `pip install -e ./sdks/python-client`.
You can also copy the `lightwork_client/` folder next to your script and import
it without installing.

## Authenticate

Pass a personal or service-account API key (`lwk_...`, from **Account › API
keys**) or the dashboard's `MAVERICK_DASHBOARD_TOKEN`. Either one is sent as
`Authorization: Bearer`.

```python
import os
from lightwork_client import Lightwork

lw = Lightwork("https://lightwork.example.com", os.environ["LIGHTWORK_API_KEY"])
```

Without a token, a local dashboard on loopback accepts writes only from its own
origin. The client always sends `Origin: <base URL origin>` on `POST`, so that
check passes:

```python
lw = Lightwork("http://127.0.0.1:8765")   # no token, same computer only
```

A bearer token over plain `http://` to a non-loopback host is refused. Use
`https://` for remote dashboards.

## Quick start: submit a goal and follow it

Create the idempotency key yourself and store it before you send the request.
If the request times out or the reply is lost, reuse the same key to recover
the start. Do not send the work again under a new key.

```python
import time
import uuid

from lightwork_client import Lightwork, LightworkError

lw = Lightwork("http://127.0.0.1:8765")
key = str(uuid.uuid4())  # persist this before sending

try:
    goal = lw.submit_goal(
        "Compare pgvector and Qdrant",
        "Write the comparison to report.md.",
        max_dollars=2.0,
        max_wall_seconds=1800,
        idempotency_key=key,
    )
except LightworkError as exc:
    if exc.status is not None and exc.status < 500:
        raise  # a refusal such as 422 or 403: fix the request, do not retry
    goal = lw.goal_receipt(key)["goal"]  # no response or a 5xx: reconcile, never resubmit

goal_id, since = goal["id"], 0
while True:  # real code needs a deadline: a goal waiting on an answer stays non-terminal
    page = lw.goal_events(goal_id, since=since)
    for event in page["events"]:
        print(event)
    since = page["next_id"]
    if page["terminal"] and not page["has_more"]:
        break
    if not page["has_more"]:
        time.sleep(2)

final = lw.get_goal(goal_id)
print(final["status"], final["result"])
```

A goal ID means the start was accepted. It does not mean the work ran or
succeeded. `terminal` on the events page is the signal to stop polling. A
goal that waits for an answer keeps `terminal` false, so answer it with
`answer_question()` and keep polling.

## Methods

| Method | Dashboard route | Returns |
|---|---|---|
| `health()` | `GET /healthz` (no auth) | `Health(ok, http_status, body)`. A 503 "degraded" answer is returned, not raised. |
| `submit_goal(title, description="", *, max_dollars, max_wall_seconds, max_depth, idempotency_key)` | `POST /api/v1/goals` | The goal dict (201). |
| `goal_receipt(idempotency_key, *, kind="api")` | `GET /api/v1/goal-submissions/receipt` | The receipt dict. It never submits work. |
| `get_goal(goal_id)` | `GET /api/v1/goals/{id}` | The goal dict, with `status`, `result`, `reason_code`, `needs_input`. |
| `list_goals(*, status, limit=50, offset=0)` | `GET /api/v1/goals` | A list of goal dicts, newest first. |
| `goal_events(goal_id, *, since=0, limit=200)` | `GET /api/v1/goals/{id}/events` | One page: `events`, `next_id`, `has_more`, `terminal`. |
| `answer_question(goal_id, question_id, answer)` | `POST /api/v1/goals/{id}/answer` | `None` (204). |
| `list_approvals()` | `GET /api/v1/approvals` (needs operate) | The dict from the route, with `approvals` and `private_requests`. |
| `approve_approval(approval_id, *, binding, acknowledged=False, confirmation=None)` | `POST /api/v1/approvals/{id}/approve` | `Decision` (202 or 204). |
| `deny_approval(approval_id, *, reason=None)` | `POST /api/v1/approvals/{id}/deny` | `Decision` (202 or 204). |
| `spend()` | `GET /api/v1/spend` | The dict from the route, with `total` and `episodes`. |

Responses are returned as the API sends them: plain dicts and lists. `Health`
and `Decision` are small frozen dataclasses. `Decision` holds the
`X-Lightwork-*` headers: `decision_id`, `audit` (`"delivered"` or `"pending"`)
and `approval_state`. While `audit` is `"pending"`, the vote is recorded but not
yet effective.

### Approving

`binding` is the `binding_sha256` of the reviewed card. Read it from the row
that `list_approvals()` returns, under `approval_card`. The server rejects an
approval without the current card (409). High and critical risk need
`acknowledged=True`. Critical risk also needs `confirmation="approve"`. Only
approve after a person has reviewed the card.

```python
for row in lw.list_approvals()["approvals"]:
    card = row["approval_card"]
    if card and row["approval_card_status"] == "current":
        print(row["id"], row["action"], row["risk"], row["detail"])
# Approve only after review, for example:
# lw.approve_approval(row["id"], binding=card["binding_sha256"], acknowledged=True)
```

## Errors

Any failed request raises `LightworkError`:

- `exc.status` is the HTTP status. It is `None` when no response arrived, for
  example a connection failure or a timeout.
- `exc.message` is the server's plain `detail` text. It is never the raw body.
- A token that appears in a server reply is replaced with `[redacted]`.

Write calls are never retried automatically. A timeout or a 5xx on a write can
still mean the write happened. Recover it with `goal_receipt()` for goal starts,
or read the object again for other writes.

## Timeouts

`timeout` is in seconds (default `30.0`) and applies to each request. It must
be positive. The built-in transport does not follow redirects, so a bearer
token is never re-sent to another URL. A 3xx reply raises `LightworkError`.

## Testing without a network

Pass `transport=` to replace the network call. A transport receives
`(method, url, headers, body, timeout)` and returns
`(status, response_headers, body_bytes)`.

```python
from lightwork_client import Lightwork

def canned(method, url, headers, body, timeout):
    return 200, {}, b'{"total": 0, "episodes": []}'

assert Lightwork("http://testserver", transport=canned).spend()["total"] == 0
```

The dashboard tests use this seam to run every method against the real app:
`packages/maverick-dashboard/tests/test_python_client.py`.

## Not included

- No async API, no retries, no automatic pagination helpers.
- No server-sent events or WebSocket streams. Use `goal_events()` polling.
- Only the operations listed above. For anything else, use the endpoints in
  `docs/api.md` or the OpenAPI spec.
