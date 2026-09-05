import Foundation
import Combine
import LightworkConnectionKit

/// The fixed glance payload served by `GET /api/v1/glance`.
struct Glance: Codable {
    let active: Int
    let done_today: Int
    let failed_today: Int
    let spend_today: Double
    let last_result: String
    let as_of: Int
}

@MainActor
final class GlanceModel: ObservableObject {
    @Published var glance: Glance?
    @Published var error: String?

    let connectionStore = ConnectionStore(environmentURLKey: "MAVERICK_GLANCE_URL", defaultsURLKey: "glance_url")
    private var generation = 0
    private var lastConnection: DashboardConnection?

    func connectionChanged() {
        generation += 1
        glance = nil
        error = nil
        lastConnection = nil
    }

    func refresh() async {
        generation += 1
        let current = generation
        do {
            let connection = try connectionStore.load()
            if connection != lastConnection { glance = nil; lastConnection = connection }
            let data = try await dashboardData(path: "api/v1/glance", connection: connection)
            guard current == generation else { return }
            glance = try JSONDecoder().decode(Glance.self, from: data)
            error = nil
        } catch {
            guard current == generation else { return }
            self.error = error.localizedDescription
        }
    }
}
