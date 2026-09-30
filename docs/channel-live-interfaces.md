# Voice and wearable interaction boundaries

Voice and glasses use the durable channel journal when receiving external work.
An interaction response does not itself prove that a caller heard or saw it.
Saved execution results remain distinct from transport delivery evidence.

## Voice

The inbound voice webhook requires its existing bearer credential. A final user
transcript must include `message.call.id` (or the legacy top-level `call.id`) and
an event `message.id` or stable `message.timestamp`. Partial transcripts do not
execute work. Missing stable identity is rejected before execution; a relay must
preserve these fields across retries. The configured provider, assistant, phone
number and credential digest bind the account; request content cannot substitute
another account.

Concurrent callbacks for one event take one durable execution claim. A callback
received while execution is running gets a processing response. After completion,
repeated callbacks return the saved response under current bearer/caller policy,
without running the agent again. An HTTP response object is not a provider or TTS
receipt: delivery remains explicitly unknown. Changed content under an existing
event identity is rejected. Uncertain execution is never repeated automatically.

Outbound calls record the returned provider call ID. A definitive HTTP rejection
is retryable delivery; a timeout, ambiguous response or missing call ID remains
unknown. Unsupported providers and missing assistant configuration fail before
submission. Local fixtures verify request construction for Vapi, Retell and Bland;
no live provider acceptance or inbound compatibility beyond the implemented Vapi
envelope is qualified here.

## Glasses and other wearable bridges

The glasses adapter is a bridge interface. It does not include a qualified native
device integration. The external relay should call
`handle_utterance(user_id, text, utterance_id=stable_bridge_id)` after authenticating
the caller. Current configured user policy is still required. If the relay omits
an ID, each invocation is a new request; identical text does not imply a retry.

Intake is durable before the immediate acknowledgement. One owned worker resumes
unexecuted admitted work after restart and serializes execution. The in-memory
queue and waiting quick responses are bounded. Quick responses wait within the
configured deadline (at most 25 seconds); long work returns an acknowledgement
after admission. The relay can read a saved result with
`get_result(user_id, utterance_id)` under current caller/account policy.

The secondary delivery callback must return a provider acceptance identifier to
confirm delivery. `DeliveryNotSent` declares definitive nonacceptance and permits
retry of the saved answer. An ordinary exception or a callback returning `None`
leaves delivery unknown. Rendering a quick HUD response also leaves delivery
unknown because there is no device receipt. Configuring no secondary delivery
does not claim the result was sent; the saved result remains available to the
authorized relay. Applications must await `stop()` to drain or conservatively
cancel the owned worker. A custom spawn hook must return an owned asyncio Task.

## CLI and restore

CLI input is a local interactive session, with no provider message redelivery or
provider acknowledgement protocol. It continues through the existing interactive
dispatch path; no external retry guarantee is invented for it. Rich rendering and
email threading helpers are wrappers, not additional inbound adapters.

Restored channel execution/delivery journals, recovery cursors, social polling
cursors and iMessage intake cursors are protected authority. This build keeps a
bootstrap restore containing them in recovery mode; generic confirmation cannot
reopen these records. Admitted, ready, retryable and uncertain records all require
a supported no-replay reconciliation before normal work can resume. The shared
intake, execution and provider-send boundaries enforce the recovery block. This
conservative rule is a restore limitation, not automatic migration qualification.

Tests use local ASGI, fake transport callbacks, real local journal state and
local archive restore. They do not establish native hardware, remote delivery,
external identity-provider or customer acceptance.
