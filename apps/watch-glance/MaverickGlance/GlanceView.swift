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
                Text("Today").font(.headline)
                countRow(g.active, one: "task in progress", many: "tasks in progress", icon: "play.circle")
                countRow(g.done_today, one: "finished", many: "finished", icon: "checkmark.circle")
                countRow(g.failed_today, one: "did not finish", many: "did not finish", icon: "xmark.circle")
                Text(String(format: "Spent $%.2f today", g.spend_today))
                    .font(.caption)
                if !g.last_result.isEmpty {
                    Text("Latest result").font(.caption2).bold()
                    Text(g.last_result)
                        .font(.caption2)
                        .lineLimit(2)
                        .foregroundStyle(.secondary)
                } else if g.active == 0 && g.done_today == 0 && g.failed_today == 0 {
                    Text("Nothing has run today. New tasks from the dashboard show up here.")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            } else if model.error == nil {
                ProgressView("Loading…")
            }
            if let err = model.error {
                Text("Can't update").font(.caption).bold().foregroundStyle(.red)
                Text(err).font(.caption2)
            }
            Button(model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }
            Button("Settings") { settingsShown = true }
          }
        }
        .sheet(isPresented: $settingsShown) {
            ConnectionSettingsView(store: model.connectionStore, testPath: "api/v1/glance") {
                model.connectionChanged()
                Task { await model.refresh() }
            }
        }
        .task { await model.refresh() }
        .refreshable { await model.refresh() }
    }

    private func countRow(_ count: Int, one: String, many: String, icon: String) -> some View {
        Label("\(count) \(count == 1 ? one : many)", systemImage: icon)
            .font(.footnote)
    }
}
