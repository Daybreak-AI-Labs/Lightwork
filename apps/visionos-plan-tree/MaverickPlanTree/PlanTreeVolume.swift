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
    @State private var selected: GoalNode?
    @State private var settingsShown = false

    var body: some View {
        RealityView { content in
            // Entities are (re)built in `update:`; nothing to do at make time.
            _ = content
        } update: { content in
            content.entities.removeAll()
            guard let forest = model.forest else { return }
            let root = Entity()
            var byId: [Int: SIMD3<Float>] = [:]
            for node in forest.nodes {
                let pos = SIMD3<Float>(
                    Float(node.y) * SCALE - 0.25,   // layout rows -> X
                    0.25 - Float(node.x) * SCALE,   // layout depth cols -> Y
                    Float(node.depth) * -0.05       // tree depth -> Z layers
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
                selected = model.forest?.nodes.first { $0.id == id }
            }
        })
        .overlay(alignment: .top) {
            VStack {
                HStack {
                    Button("Settings") { settingsShown.toggle() }
                    Button(model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }
                }
                .padding(12).glassBackgroundEffect()
                if settingsShown {
                    ConnectionSettingsView(store: model.connectionStore, testPath: "api/v1/goal-tree",
                                           onClose: { settingsShown = false }) {
                        selected = nil
                        model.connectionChanged()
                        Task { await model.refresh() }
                    }
                    .frame(width: 420, height: 520)
                    .glassBackgroundEffect()
                }
            }
        }
        .overlay(alignment: .bottom) {
            if let node = selected {
                VStack(spacing: 4) {
                    Text(node.title.isEmpty ? "Untitled task" : node.title).font(.headline).lineLimit(2)
                    Text(statusLabel(node.status)).font(.caption)
                }
                .padding(12)
                .glassBackgroundEffect()
                .padding(.bottom, 24)
            } else if let err = model.error {
                VStack(spacing: 6) {
                    Text("Could not load your plan").font(.headline)
                    Text(err)
                    Button("Try again") { Task { await model.refresh() } }
                }
                .font(.caption).padding(12).glassBackgroundEffect()
            } else if let forest = model.forest, forest.nodes.isEmpty {
                VStack(spacing: 6) {
                    Text("No tasks to show yet").font(.headline)
                    Text("Start a task from the Lightwork dashboard, then tap Refresh.")
                }
                .font(.caption).padding(12).glassBackgroundEffect()
            } else if model.forest == nil {
                ProgressView("Loading your plan…")
                    .padding(12).glassBackgroundEffect()
            } else {
                Text("Look at a sphere and pinch to see the task's name and status.")
                    .font(.caption).padding(12).glassBackgroundEffect()
            }
        }
        .task { await model.refresh() }
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
