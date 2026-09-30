# Lightwork watch glance (watchOS scaffold)

**Preview source; no signed customer installer or delivered complication.**

A minimal SwiftUI watchOS app rendering the fixed glance
payload from a self-hosted Lightwork dashboard:

```
GET https://<your-host>:8765/api/v1/glance
-> {active, done_today, failed_today, spend_today, last_result, as_of}
```

**App build requirements:** Xcode 16+ on macOS
with the watchOS SDK; create a watchOS App target named `MaverickGlance` and
add the two Swift files and the local `apps/apple-companion-core` package,
linking `LightworkConnectionKit`. Use **Settings** in the app to save the
dashboard address and token to device-only Keychain storage. Legacy scheme
settings are read until the first successful in-app save. The
URL must use HTTPS for any non-localhost dashboard; plain HTTP is accepted
only for loopback development (`localhost`, `127.0.0.1`, or `::1`).

CI compiles/tests the shared connection package on macOS with isolated storage;
the complete watch app and on-device input remain unverified.

Security model: the watch talks only to YOUR dashboard host; include the
`Authorization: Bearer <MAVERICK_DASHBOARD_TOKEN>` header when the dashboard
sets one. Because that bearer token grants dashboard API access, never send
it to a remote dashboard over cleartext HTTP. Nothing else leaves the watch.

The view labels the source timestamp and the UTC reporting day explicitly.
Transient failures can show a marked saved snapshot; authentication/permission
failures remove it. Refreshes have one owner, cancellation on leaving/background,
and a 64 KB streamed-response cap. These changes are not device-qualified.

`Tests/GlanceModelTests.swift` is authored for a future `MaverickGlanceTests`
Xcode test target; it has not run. Shared transport cap and error-classification
tests live in `apps/apple-companion-core`. No signing identity, provisioning
profile, store upload or production service was changed.
