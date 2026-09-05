# Lightwork AR plan tree (visionOS scaffold)

A minimal SwiftUI + RealityKit visionOS app rendering a Lightwork goal
forest as a 3D plan tree in a volumetric window: each goal is a small
sphere colored by status, parent links are thin connecting bars, and
gazing at a node + pinching shows its title/status card.

It consumes the same endpoint as the dashboard's WebGL `/plan-tree-3d`
page:

```
GET https://<your-host>/api/v1/goal-tree
-> {nodes: [{id, parent_id, title, status, depth, x, y}], edges: [[parent, child]], count}
```

The server already computes a layered layout; this app maps `x`/`y` into
metres on the volume's X/Y plane and uses `depth` for Z so sibling layers
sit in front of one another.

**App build requirements:** Xcode 16+ on
macOS with the visionOS SDK; an Apple Vision Pro or the visionOS
simulator. Create a visionOS App target named `MaverickPlanTree`, choose
the *Volume* window style, and add the two Swift files and the local
`apps/apple-companion-core` package, linking `LightworkConnectionKit`.
Use **Connection** in the app to save the dashboard address and token in one
device-only Keychain record. Remote dashboards require HTTPS; redirects are
rejected. Legacy scheme settings are read until the first successful save.
CI compiles/tests the shared connection package on macOS with isolated storage;
the complete volume app and its on-device settings layout remain unverified.

**Honest scope:** this is the scaffold — model + fetch + RealityKit
entity assembly are written; it has not been run on Vision Pro hardware
(none is available to this repository). The geometry mapping and gesture
targets are the parts most likely to need on-device tuning, and they are
marked in the source.

Security model: the app talks only to YOUR dashboard host; include the
`Authorization: Bearer <MAVERICK_DASHBOARD_TOKEN>` header when the
dashboard sets one. Read-only — the app calls no mutating endpoint.
