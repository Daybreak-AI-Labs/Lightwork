import SwiftUI
import RealityKit
import LightworkConnectionKit

/// The volumetric window: goal spheres + parent links assembled from the
/// server-laid-out forest. Pixel coordinates map to metres via `SCALE`;
/// `depth` becomes Z so each layer sits in front of its parent layer.
/// NEEDS ON-DEVICE TUNING: the scale and sphere radius read well in the
/// simulator but have not been verified on Vision Pro hardware.
private let SCALE: Float = 0.0015
private let RADIUS: Float = 0.012

struct PlanTreeVolume: View {
    @StateObject private var model = PlanTreeModel()
    @AccessibilityFocusState private var selectedCardFocused: Bool
    @State private var selectedId: Int?
    @State private var listShown = false
    @State private var search = ""
    @State private var page = 0
    @State private var zoom: Float = 1
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    private var selected: GoalNode? { model.forest?.nodes.first { $0.id == selectedId } }
    private var matching: [GoalNode] {
        (model.forest?.nodes ?? []).filter { search.isEmpty || $0.title.localizedCaseInsensitiveContains(search)
            || statusLabel($0.status).localizedCaseInsensitiveContains(search) }
    }
    private var pageCount: Int { max(1, (matching.count + 49) / 50) }
    @State private var settingsShown = false

    var body: some View {
        RealityView { content in
            // Entities are (re)built in `update:`; nothing to do at make time.
            _ = content
        } update: { content in
            content.entities.removeAll()
            guard let forest = model.forest else { return }
            let root = Entity()
            // Large plans remain fully reachable in the paginated text list.
            // Bound spatial entities separately to keep the volume responsive.
            let nodes = Array(forest.nodes.prefix(200))
            let xs = nodes.map(\.y), ys = nodes.map(\.x)
            let minX = xs.min() ?? 0, maxX = xs.max() ?? 0
            let minY = ys.min() ?? 0, maxY = ys.max() ?? 0
            let extent = max(max(maxX - minX, maxY - minY), 1)
            let scale = min(SCALE, Float(0.5 / extent))
            let depth = max(1, nodes.map(\.depth).max() ?? 1)
            root.scale = SIMD3<Float>(repeating: zoom)
            var byId: [Int: SIMD3<Float>] = [:]
            for node in nodes {
                let pos = SIMD3<Float>(
                    Float(node.y - (minX + maxX) / 2) * scale,
                    -Float(node.x - (minY + maxY) / 2) * scale,
                    Float(node.depth) / Float(depth) * -0.3
                )
                byId[node.id] = pos
                let sphere = ModelEntity(
                    mesh: .generateSphere(radius: RADIUS),
                    materials: [SimpleMaterial(color: uiColor(node.status),
                                               isMetallic: false)]
                )
                sphere.position = pos
                sphere.components.set(InputTargetComponent())
                sphere.generateCollisionShapes(recursive: false)
                sphere.name = String(node.id)
                root.addChild(sphere)
            }
            for edge in forest.edges where edge.count == 2 {
                if let a = byId[edge[0]], let b = byId[edge[1]] {
                    root.addChild(linkBar(from: a, to: b))
                }
            }
            content.add(root)
        }
        .gesture(TapGesture().targetedToAnyEntity().onEnded { value in
            if let id = Int(value.entity.name) {
                selectedId = id
            }
        })
        .overlay(alignment: .top) {
            VStack {
                HStack {
                    Button("Settings") { settingsShown.toggle() }
                    Button(model.isRefreshing ? "Updating…" : model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }
                        .disabled(model.isRefreshing)
                    Button("Task list") { listShown = true }
                    Button("Fit plan") { zoom = 1 }
                    Slider(value: $zoom, in: 0.25...2).frame(width: 130).accessibilityLabel("Plan zoom")
                }
                .padding(12).glassBackgroundEffect()
                if settingsShown {
                    ConnectionSettingsView(store: model.connectionStore, testPath: "api/v1/goal-tree",
                                           onClose: { settingsShown = false }) {
                        selectedId = nil
                        search = ""
                        model.connectionChanged()
                        Task { await model.refresh() }
                    }
                    .frame(width: 420, height: 520)
                    .glassBackgroundEffect()
                }
            }
        }
        .overlay(alignment: .bottom) {
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
                    .accessibilityLabel((node.title.isEmpty ? "Untitled task" : node.title) + ", " + statusLabel(node.status))
                    .accessibilityFocused($selectedCardFocused)
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
                    Text("\(matching.count) matching tasks; page \(min(page + 1, pageCount)) of \(pageCount)")
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
        .onChange(of: selectedId) { value in selectedCardFocused = value != nil }
        .onChange(of: search) { _ in page = 0 }
        .onChange(of: model.forest?.nodes.map(\.id)) { ids in
            if let selectedId, !(ids ?? []).contains(selectedId) { self.selectedId = nil }
            if ids == nil { search = ""; page = 0 }
            else { page = min(page, pageCount - 1) }
        }
        .onChange(of: scenePhase) { phase in
            if phase == .active { Task { await model.refresh() } } else { model.cancelRefresh() }
        }
        .onDisappear { model.cancelRefresh() }

        .task { await model.refresh() }
    }

    private func handoff(_ id: Int) {
        do {
            let connection = try model.connectionStore.load()
            let url = try connection.validatedURL().appendingPathComponent("chat/goal/\(id)")
            openURL(url) // The browser authenticates independently; no bearer in the URL.
        } catch { model.error = plainMessage(for: error) }
    }
}

/// A thin box stretched between two node positions.
private func linkBar(from a: SIMD3<Float>, to b: SIMD3<Float>) -> ModelEntity {
    let delta = b - a
    let length = max(simd_length(delta), 0.001)
    let bar = ModelEntity(
        mesh: .generateBox(size: [0.002, 0.002, length]),
        materials: [SimpleMaterial(color: .gray, isMetallic: false)]
    )
    bar.position = (a + b) / 2
    bar.look(at: b, from: bar.position, relativeTo: nil)
    return bar
}

private func uiColor(_ status: String) -> UIColor {
    switch statusColorName(status) {
    case "blue": return .systemBlue
    case "green": return .systemGreen
    case "red": return .systemRed
    case "gray": return .systemGray
    default: return .systemYellow
    }
}
