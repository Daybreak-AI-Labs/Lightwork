import SwiftUI
import LightworkConnectionKit

/// One-screen glance: counts, today's spend, last result. Complication-sized.
struct GlanceView: View {
    @StateObject private var model = GlanceModel()
    @State private var settingsShown = false

    var body: some View {
        ScrollView {
          VStack(alignment: .leading, spacing: 8) {
            if let g = model.glance {
                HStack {
                    Label("\(g.active)", systemImage: "play.circle")
                    Label("\(g.done_today)", systemImage: "checkmark.circle")
                    Label("\(g.failed_today)", systemImage: "xmark.circle")
                }
                .font(.headline)
                Text(String(format: "$%.2f today", g.spend_today))
                    .font(.caption)
                if !g.last_result.isEmpty {
                    Text(g.last_result)
                        .font(.caption2)
                        .lineLimit(2)
                        .foregroundStyle(.secondary)
                }
            } else if model.error == nil {
                ProgressView()
            }
            if let err = model.error {
                Text("Could not update Lightwork. Open Connection to check your settings.")
                    .font(.caption2).foregroundStyle(.red)
                DisclosureGroup("Connection details") {
                    Text(err).font(.caption2)
                }
            }
            Button("Connection") { settingsShown = true }
            Button("Refresh") { Task { await model.refresh() } }
          }
        }
        .sheet(isPresented: $settingsShown) {
            ConnectionSettingsView(store: model.connectionStore) {
                model.connectionChanged()
                Task { await model.refresh() }
            }
        }
        .task { await model.refresh() }
        .refreshable { await model.refresh() }
    }
}
