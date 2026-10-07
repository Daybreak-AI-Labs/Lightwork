# Lightwork watch glance (watchOS scaffold)

**Preview source; no signed customer installer or delivered complication.**

Settings now offers expiring [device pairing](../../docs/device-pairing.md)
with explicit browser approval and own-work credentials, plus protected renewal.
Advanced manual token setup remains available. Pairing manages the connection;
watch task reads remain GET-only.

A minimal SwiftUI watchOS app rendering the fixed glance
payload from a self-hosted Lightwork dashboard:

```
GET https://<your-host>:8765/api/v1/glance
-> {active, done_today, failed_today, spend_today, last_result, as_of}
```

**Build from the checked-in project:** on macOS with Xcode 16/watchOS SDK and
XcodeGen 2.42.0, run `python ../apple-companions/build.py watch`.
The generated `MaverickGlance` app and `MaverickGlanceTests` target include the
Swift entry point, both view/model sources and local `LightworkConnectionKit`.
No source or target assembly is required. See the
[native companion release matrix](../../docs/native-companion-release.md).
Use Settings to pair and save a protected own-work credential. Remote dashboards
require HTTPS; only loopback development permits plain HTTP.

Security model: the watch talks only to YOUR dashboard host; include the
`Authorization: Bearer <MAVERICK_DASHBOARD_TOKEN>` header when the dashboard
sets one. Because that bearer token grants dashboard API access, never send
it to a remote dashboard over cleartext HTTP. Nothing else leaves the watch.

The view labels the source timestamp and the UTC reporting day explicitly.
Transient failures can show a marked saved snapshot; authentication/permission
failures remove it. Refreshes have one owner, cancellation on leaving/background,
and a 64 KB streamed-response cap. These changes are not device-qualified.

`Tests/GlanceModelTests.swift` is included in the generated `MaverickGlanceTests`
Xcode test target; it has not run on this Windows executor. Shared transport cap and error-classification
tests live in `apps/apple-companion-core`. No signing identity, provisioning
profile, store upload or production service was changed.
