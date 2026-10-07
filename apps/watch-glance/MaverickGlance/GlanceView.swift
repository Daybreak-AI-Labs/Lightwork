import SwiftUI
import LightworkConnectionKit

/// Read-only preview with an explicit source date and freshness.
struct GlanceView: View {
    @StateObject private var model = GlanceModel()
    @State private var settingsShown = false
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ScrollView {
          VStack(alignment: .leading, spacing: 8) {
            if let g = model.glance {
                Text("UTC day: " + sourceDay(g.as_of)).font(.headline)
                Text("Source: " + Date(timeIntervalSince1970: Double(g.as_of)).formatted(date: .abbreviated, time: .shortened)).font(.caption2)
                if model.error != nil { Text("Saved snapshot — not current").font(.caption).bold() }
                countRow(g.active, one: "task in progress", many: "tasks in progress", icon: "play.circle")
                countRow(g.done_today, one: "finished", many: "finished", icon: "checkmark.circle")
                countRow(g.failed_today, one: "did not finish", many: "did not finish", icon: "xmark.circle")
                Text(String(format: "Spent $%.2f for this UTC day", g.spend_today))
                    .font(.caption)
                if !g.last_result.isEmpty {
                    Text("Latest result").font(.caption2).bold()
                    Text(g.last_result)
                        .font(.caption2)
                        .lineLimit(2)
                        .foregroundStyle(.secondary)
                } else if g.active == 0 && g.done_today == 0 && g.failed_today == 0 {
                    Text("Nothing had run for this UTC day. New tasks from the dashboard show up here.")
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
            Button(model.isRefreshing ? "Updating…" : model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }
                .disabled(model.isRefreshing)
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
        .onDisappear { model.cancelRefresh() }
        .onChange(of: scenePhase) { phase in
            if phase == .active { Task { await model.refresh() } } else { model.cancelRefresh() }
        }
    }

    private func sourceDay(_ timestamp: Int) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateStyle = .medium
        return formatter.string(from: Date(timeIntervalSince1970: Double(timestamp)))
    }

    private func countRow(_ count: Int, one: String, many: String, icon: String) -> some View {
        Label("\(count) \(count == 1 ? one : many)", systemImage: icon)
            .font(.footnote)
    }
}
