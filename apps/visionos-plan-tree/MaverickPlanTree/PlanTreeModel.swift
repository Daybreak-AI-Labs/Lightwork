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

    let connectionStore = ConnectionStore(environmentURLKey: "MAVERICK_DASHBOARD_URL", defaultsURLKey: "dashboard_url")
    private var generation = 0
    private var lastConnection: DashboardConnection?

    func connectionChanged() {
        generation += 1
        forest = nil
        error = nil
        lastConnection = nil
    }

    func refresh() async {
        generation += 1
        let current = generation
        do {
            let connection = try connectionStore.load()
            if connection != lastConnection { forest = nil; lastConnection = connection }
            let data = try await dashboardData(path: "api/v1/goal-tree", connection: connection)
            guard current == generation else { return }
            forest = try JSONDecoder().decode(GoalForest.self, from: data)
            error = nil
        } catch {
            guard current == generation else { return }
            self.error = error.localizedDescription
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
