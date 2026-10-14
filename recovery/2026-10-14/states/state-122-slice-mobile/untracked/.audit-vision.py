from pathlib import Path
p=Path('apps/visionos-plan-tree/MaverickPlanTree/PlanTreeVolume.swift');s=p.read_text(encoding='utf-8');s=s.replace('    @State private var selected: GoalNode?', '''    @State private var selectedId: Int?
    @State private var listShown = false
    @State private var search = ""
    @State private var page = 0
    @State private var zoom: Float = 1
    @Environment(\\.scenePhase) private var scenePhase
    @Environment(\\.openURL) private var openURL
    private var selected: GoalNode? { model.forest?.nodes.first { $0.id == selectedId } }
    private var matching: [GoalNode] {
        (model.forest?.nodes ?? []).filter { search.isEmpty || $0.title.localizedCaseInsensitiveContains(search)
            || statusLabel($0.status).localizedCaseInsensitiveContains(search) }
    }
    private var pageCount: Int { max(1, (matching.count + 49) / 50) }''')
s=s.replace('            var byId: [Int: SIMD3<Float>] = [:]\n            for node in forest.nodes {', '''            // Large plans remain fully reachable in the paginated text list.
            // Bound spatial entities separately to keep the volume responsive.
            let nodes = Array(forest.nodes.prefix(200))
            let xs = nodes.map(\\.y), ys = nodes.map(\\.x)
            let minX = xs.min() ?? 0, maxX = xs.max() ?? 0
            let minY = ys.min() ?? 0, maxY = ys.max() ?? 0
            let extent = max(maxX - minX, maxY - minY, 1)
            let scale = min(SCALE, Float(0.5 / extent))
            let depth = max(1, nodes.map(\\.depth).max() ?? 1)
            root.scale = SIMD3<Float>(repeating: zoom)
            var byId: [Int: SIMD3<Float>] = [:]
            for node in nodes {''')
s=s.replace('Float(node.y) * SCALE - 0.25,   // layout rows -> X\n                    0.25 - Float(node.x) * SCALE,   // layout depth cols -> Y\n                    Float(node.depth) * -0.05       // tree depth -> Z layers','Float(node.y - (minX + maxX) / 2) * scale,\n                    -Float(node.x - (minY + maxY) / 2) * scale,\n                    Float(node.depth) / Float(depth) * -0.3')
s=s.replace('selected = model.forest?.nodes.first { $0.id == id }', 'selectedId = id')
s=s.replace('                    Button(model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }','''                    Button(model.isRefreshing ? "Updating…" : model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }
                        .disabled(model.isRefreshing)
                    Button("Task list") { listShown = true }
                    Button("Fit plan") { zoom = 1 }
                    Slider(value: $zoom, in: 0.25...2).frame(width: 130).accessibilityLabel("Plan zoom")''')
s=s.replace('                        selected = nil', '                        selectedId = nil\n                        search = ""')
a=s.index('        .overlay(alignment: .bottom)');b=s.index('\n        .task { await model.refresh() }',a)
s=s[:a]+'''        .overlay(alignment: .bottom) {
            VStack(spacing: 6) {
                // Errors stay visible even when a previously selected task is
                // retained after an eligible transient failure.
                if let err = model.error {
                    Text("Could not update your plan").font(.headline)
                    Text(err).accessibilityLabel("Plan update failed. " + err)
                    if model.forest != nil { Text("Showing a saved snapshot, not current state.") }
                }
                if let received = model.receivedAt {
                    Text("Last received: " + received.formatted(date: .abbreviated, time: .shortened)).font(.caption)
                }
                if let node = selected {
                    ScrollView {
                        VStack(spacing: 4) {
                            Text(node.title.isEmpty ? "Untitled task" : node.title).font(.headline)
                            Text(statusLabel(node.status)).font(.caption)
                            Button("Open task on dashboard") { handoff(node.id) }
                        }
                    }.frame(maxHeight: 180)
                    .accessibilityElement(children: .contain)
                } else if let forest = model.forest, forest.nodes.isEmpty {
                    Text("No tasks to show yet").font(.headline)
                    Text("Start a task from the Lightwork dashboard, then tap Refresh.")
                } else if model.isRefreshing && model.forest == nil {
                    ProgressView("Loading your plan…")
                } else if let forest = model.forest {
                    Text("Choose Task list to search every task, or select a sphere.")
                    if forest.nodes.count > 200 { Text("The volume shows the first 200 tasks. All received tasks are available in Task list.") }
                }
            }
            .font(.caption).padding(12).glassBackgroundEffect().padding(.bottom, 24)
        }
        .sheet(isPresented: $listShown) {
            NavigationStack {
                VStack(alignment: .leading) {
                    TextField("Search tasks or status", text: $search).textFieldStyle(.roundedBorder)
                        .accessibilityLabel("Search tasks or status")
                    if let error = model.error { Text(error).foregroundStyle(.red) }
                    Text("\\(matching.count) matching tasks; page \\(min(page + 1, pageCount)) of \\(pageCount)")
                    List(Array(matching.dropFirst(min(page, pageCount - 1) * 50).prefix(50))) { node in
                        Button {
                            selectedId = node.id; listShown = false
                        } label: {
                            VStack(alignment: .leading) {
                                Text(node.title.isEmpty ? "Untitled task" : node.title)
                                Text(statusLabel(node.status)).font(.caption)
                                if let parentId = node.parentId,
                                   let parent = model.forest?.nodes.first(where: { $0.id == parentId }) {
                                    Text("Part of: " + (parent.title.isEmpty ? "Untitled task" : parent.title)).font(.caption)
                                } else { Text("Top-level task").font(.caption) }
                            }
                        }
                        .accessibilityLabel((node.title.isEmpty ? "Untitled task" : node.title) + ", " + statusLabel(node.status))
                    }
                    HStack {
                        Button("Previous page") { page = max(0, page - 1) }.disabled(page == 0)
                        Button("Next page") { page += 1 }.disabled(page + 1 >= pageCount)
                        Button("Close task list") { listShown = false }
                    }
                }.padding().navigationTitle("Task hierarchy")
            }
        }
        .onChange(of: search) { _ in page = 0 }
        .onChange(of: model.forest?.nodes.map(\\.id)) { ids in
            if let selectedId, !(ids ?? []).contains(selectedId) { self.selectedId = nil }
            if ids == nil { search = ""; page = 0 }
            else { page = min(page, pageCount - 1) }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active { Task { await model.refresh() } } else { model.cancelRefresh() }
        }
        .onDisappear { model.cancelRefresh() }
'''+s[b:]
s=s.replace('    }\n}\n\n/// A thin box', '''    }

    private func handoff(_ id: Int) {
        do {
            let connection = try model.connectionStore.load()
            let url = try connection.validatedURL().appendingPathComponent("chat/goal/\\(id)")
            openURL(url) // The browser authenticates independently; no bearer in the URL.
        } catch { model.error = plainMessage(for: error) }
    }
}

/// A thin box''')
p.write_text(s,encoding='utf-8',newline='\n')
