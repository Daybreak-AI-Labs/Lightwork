from pathlib import Path
p=Path('apps/visionos-plan-tree/MaverickPlanTree/PlanTreeVolume.swift');s=p.read_text(encoding='utf-8').replace('    @State private var selectedId:', '    @AccessibilityFocusState private var selectedCardFocused: Bool\n    @State private var selectedId:').replace('max(maxX - minX, maxY - minY, 1)', 'max(max(maxX - minX, maxY - minY), 1)').replace('.accessibilityElement(children: .contain)', '.accessibilityElement(children: .contain)\n                    .accessibilityLabel((node.title.isEmpty ? "Untitled task" : node.title) + ", " + statusLabel(node.status))\n                    .accessibilityFocused($selectedCardFocused)').replace('        .onChange(of: search)', '        .onChange(of: selectedId) { value in selectedCardFocused = value != nil }\n        .onChange(of: search)');p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/watch-glance/README.md');s=p.read_text(encoding='utf-8').replace('A minimal SwiftUI watchOS app + complication rendering', '**Preview source; no signed customer installer or delivered complication.**\n\nA minimal SwiftUI watchOS app rendering');s+='''
The view labels the source timestamp and the UTC reporting day explicitly.
Transient failures can show a marked saved snapshot; authentication/permission
failures remove it. Refreshes have one owner, cancellation on leaving/background,
and a 64 KB streamed-response cap. These changes are not device-qualified.

`Tests/GlanceModelTests.swift` is authored for a future `MaverickGlanceTests`
Xcode test target; it has not run. Shared transport cap and error-classification
tests live in `apps/apple-companion-core`. No signing identity, provisioning
profile, store upload or production service was changed.
''';p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/visionos-plan-tree/README.md');s=p.read_text(encoding='utf-8').replace('A minimal SwiftUI', '**Preview source; no signed customer installer.**\n\nA minimal SwiftUI',1);s+='''
Task list provides a searchable, paginated text hierarchy with named statuses,
parent relationships and task selection without precision gaze. It pages 50
nodes at a time; the spatial view bounds entity construction to 200 nodes and
fits received coordinates, with zoom and reset controls. Selection stores a task
ID and reads its card from the newest forest. Errors remain visible while a
same-identity transient snapshot is retained; denied access clears the forest.
The dashboard handoff contains only the task path and requires browser sign-in.

`Tests/PlanTreeModelTests.swift` is authored for a future `MaverickPlanTreeTests`
Xcode test target and remains unrun. The source uses a 2 MB streamed-response
cap and cancels active refreshes on disposal/background. Signed packaging,
RealityKit comfort/layout/accessibility and 1/100/1000-node installed acceptance
are not qualified. No signing or platform account was used.
''';p.write_text(s,encoding='utf-8',newline='\n')
