import Foundation
import Combine
import LightworkConnectionKit

/// One goal node from `GET /api/v1/goal-tree` — the server has already
/// computed the layered layout (`depth`, `x`, `y` in pixels).
struct GoalNode: Decodable, Identifiable {
    let id: Int
    let parentId: Int?
    let title: String
    let status: String
    let depth: Int
    let x: Double
    let y: Double

    enum CodingKeys: String, CodingKey {
        case id, title, status, depth, x, y
        case parentId = "parent_id"
    }
}

struct GoalForest: Decodable {
    let nodes: [GoalNode]
    let edges: [[Int]]
    let count: Int
}

@MainActor
final class PlanTreeModel: ObservableObject {
    @Published var forest: GoalForest?
    @Published var error: String?
    @Published private(set) var isRefreshing = false
    @Published private(set) var receivedAt: Date?

    let connectionStore: ConnectionStore
    private let fetch: (DashboardConnection) async throws -> Data
    private var activeRequest: Task<Data, Error>?
    private var generation = 0
    private var lastConnection: DashboardConnection?

    init(connectionStore: ConnectionStore = ConnectionStore(environmentURLKey: "MAVERICK_DASHBOARD_URL", defaultsURLKey: "dashboard_url"),
         fetch: @escaping (DashboardConnection) async throws -> Data = { connection in
             try await dashboardData(path: "api/v1/goal-tree", connection: connection, maxBytes: 2097152)
         }) {
        self.connectionStore = connectionStore
        self.fetch = fetch
    }

    func cancelRefresh() {
        generation += 1
        activeRequest?.cancel()
        activeRequest = nil
        isRefreshing = false
    }

    func connectionChanged() {
        cancelRefresh()
        forest = nil
        receivedAt = nil
        error = nil
        lastConnection = nil
    }

    func refresh() async {
        guard !isRefreshing else { return }
        generation += 1
        let current = generation
        isRefreshing = true
        defer { if current == generation { isRefreshing = false; activeRequest = nil } }
        do {
            let connection = try connectionStore.load()
            if connection != lastConnection { forest = nil; receivedAt = nil; lastConnection = connection }
            let request = Task { try await fetch(connection) }
            activeRequest = request
            let data = try await withTaskCancellationHandler(operation: { try await request.value }, onCancel: { request.cancel() })
            guard current == generation && !Task.isCancelled else { return }
            forest = try JSONDecoder().decode(GoalForest.self, from: data)
            receivedAt = Date()
            error = nil
        } catch {
            guard current == generation && !Task.isCancelled else { return }
            if !mayRetainSnapshot(after: error) { forest = nil; receivedAt = nil }
            self.error = plainMessage(for: error)
        }
    }
}

/// Status -> display color name, mirroring the dashboard's palette.
func statusColorName(_ status: String) -> String {
    switch status {
    case "active", "running": return "blue"
    case "done", "succeeded", "completed": return "green"
    case "failed", "blocked": return "red"
    case "cancelled": return "gray"
    default: return "yellow"   // pending and anything new
    }
}

/// Status -> the plain words people see (never the stored code word).
func statusLabel(_ status: String) -> String {
    switch status {
    case "active", "running": return "In progress"
    case "pending", "queued": return "Waiting to start"
    case "blocked": return "Needs attention"
    case "done", "succeeded", "completed": return "Finished"
    case "failed", "error": return "Did not finish"
    case "cancelled", "canceled": return "Stopped"
    default:
        let words = status.replacingOccurrences(of: "_", with: " ").lowercased()
        return words.isEmpty ? "Unknown" : words.prefix(1).uppercased() + words.dropFirst()
    }
}
