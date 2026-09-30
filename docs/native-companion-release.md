# Native companion preview and release evidence

All four companion surfaces remain **preview** until the exact packaged build
passes the installed-host checks below. Mock tests and TypeScript checks do not
qualify Keychain, Android Keystore, VoiceOver/TalkBack or native networking.
No store listing, signed customer download, distribution certificate or platform
account is created by these changes. Watch has no delivered complication target.

| Client | Reproducible project input | Build host / minimum deployment target | Current local evidence |
| --- | --- | --- | --- |
| iOS phone/tablet | `apps/mobile-companion/package-lock.json`, Expo 52.0.49, local LightworkTransport module, `build-native.cjs` | macOS + Xcode; iOS 15.1 | 91 mocked UI/protocol tests; installed-dependency TypeScript passed; native installed qualification pending |
| Android phone/tablet | Same locked Expo project and native transport | JDK 17 + Android SDK; SDK versions from pinned Expo generation | Pinned-template Android project generation and native-module autolinking passed; Android SDK absent |
| watchOS | `apps/apple-companions/project.yml`, XcodeGen 2.42.0, Swift entry point and hosted model test target | macOS + Xcode 16/watchOS SDK; watchOS 10 | Auth/response-cap/model tests authored; no Xcode on audit executor |
| visionOS | Same generated project, volume entry point and hosted model test target | macOS + Xcode 16/visionOS SDK; visionOS 1 | Model tests authored; device comfort/accessibility and build qualification pending |
| JetBrains | Gradle8.13 wrapper/checksum, pinned SDK2024.3.6, Kotlin2.1.20, `apps/jetbrains-plugin/build.py` | JDK21; compile target IntelliJ Community243 only | Local SDK compile and synthetic loopback transport checks; installed IDE/PasswordSafe/accessibility qualification pending |
| Zed | Rust1.97.1, exact API0.7.0, Cargo.lock, `apps/zed-extension/build.py` | wasm32-wasip2; selected host must support API0.7.0 | Locked release WASM build and unsigned source/component bundle; installed-host/MCP/accessibility qualification pending |

For editor integrations, use [JetBrains build instructions](../apps/jetbrains-plugin/BUILDING.md)
and [Zed build instructions](../apps/zed-extension/BUILDING.md). The installable
JetBrains ZIP and Zed engineering bundle do not promote either editor to supported
installed status. The Zed development install rebuilds its bundled sources.

## Local builds

In `apps/mobile-companion`, run `npm ci --ignore-scripts`, then
`npm run build:android:local` or `npm run build:ios:local`. These generate native
projects from locked sources and build an Android **debug APK** or an **unsigned
iOS simulator app**, respectively. They do not upload. Inspect generated
manifests before producing a distributable artifact. The native transport is a
local Expo module; Expo Go cannot load it and fails closed before a request.

The bridge applies the 4 MiB cap before data reaches JavaScript, rejects
redirects, has no shared cookie jar or disk cache, owns cancellation/timeouts,
limits concurrent requests, and never logs request bodies or credentials.
Android automatic connection retries are disabled. Native TLS validation is
unchanged. JavaScript also rejects non-loopback cleartext addresses.

For Apple companions, install the exact XcodeGen pin in
`apps/apple-companions/Mintfile`, then run `python build.py watch` or
`python build.py vision` from that folder. `--check` reports missing tools;
`--test-destination 'platform=watchOS Simulator,id=...'` runs the included hosted
model tests on an installed simulator. The same option accepts a visionOS
simulator. The script never enables signing or provisioning updates. Generated
projects include the shared local connection package and require no manual
target/source assembly. The input format follows the
[pinned XcodeGen specification](https://github.com/yonaskolb/XcodeGen/blob/2.42.0/Docs/ProjectSpec.md).

## Release-owner gate

A release owner must select the distribution channel, confirm bundle ownership,
review privacy/export declarations and provide the existing signing configuration
through the platform build system. Archive the reviewed source SHA, dependency
locks, Xcode/SDK/JDK versions, unsigned-build logs, signed artifact SHA-256 and
installed test evidence together. Do not mark a preview supported based solely
on a successful archive. Do not distribute the developer debug APK as a supported
release. Platform signing makes byte-identical signed rebuilds an inappropriate
claim; compare the reviewed source/input manifest and unsigned build evidence.

Before releasing each platform, install the resulting artifact on the minimum
and supported current OS; pair a synthetic account; exercise denial, revocation,
expiry/renewal, TLS redirect refusal, 4 MiB response overflow, network loss,
background cancellation, two-profile/cache isolation, protected-store failure,
largest text, screen-reader navigation and RTL. For mobile actions also test
stale review, duplicate confirmation, lost response and preserved partial result
on Stop. Watch needs the actual UTC-day/freshness view; vision needs both the
spatial and 1/100/1000-node text paths. Record actual results and defects, not
unchecked acceptance claims. These checks use mock accounts/endpoints and never
require a production provider invocation.
