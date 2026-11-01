# Lightwork AR plan tree (visionOS scaffold)

**Preview source; no signed installer.**

Settings now offers expiring [device pairing](../../docs/device-pairing.md)
with explicit browser approval and own-work credentials, plus protected renewal.
Advanced manual token setup remains available. Pairing manages the connection;
vision task reads remain GET-only.

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

**Build from the checked-in project:** on macOS with Xcode 16/visionOS SDK and
XcodeGen 2.42.0, run `python ../apple-companions/build.py vision`.
The generated `MaverickPlanTree` app includes its volume entry point and hosted
`MaverickPlanTreeTests` target. The local `LightworkConnectionKit` package is
linked automatically. No manual target/source assembly is required. See the
[native companion release matrix](../../docs/native-companion-release.md).
Settings saves the paired connection in device-only Keychain storage; remote
dashboards require HTTPS and redirects are refused. Native build and installed
volume qualification require macOS with Xcode and have not been run.

**Scope:** this is the scaffold — model + fetch + RealityKit
entity assembly are written; it has not been run on Vision Pro hardware.
The geometry mapping and gesture
targets are the parts most likely to need on-device tuning, and they are
marked in the source.

Security model: the app talks only to YOUR dashboard host; include the
`Authorization: Bearer <MAVERICK_DASHBOARD_TOKEN>` header when the
dashboard sets one. Read-only — the app calls no mutating endpoint.

Task list provides a searchable, paginated text hierarchy with named statuses,
parent relationships and task selection without precision gaze. It pages 50
nodes at a time; the spatial view bounds entity construction to 200 nodes and
fits received coordinates, with zoom and reset controls. Selection stores a task
ID and reads its card from the newest forest. Errors remain visible while a
same-identity transient snapshot is retained; denied access clears the forest.
The dashboard handoff contains only the task path and requires browser sign-in.

`Tests/PlanTreeModelTests.swift` is included in the generated `MaverickPlanTreeTests`
Xcode test target and has not been run yet. The source uses a 2 MB streamed-response
cap and cancels active refreshes on disposal/background. Signed packaging,
RealityKit comfort/layout/accessibility and 1/100/1000-node installed acceptance
are not qualified.
