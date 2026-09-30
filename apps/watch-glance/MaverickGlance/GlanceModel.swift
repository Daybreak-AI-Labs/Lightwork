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
    @Published private(set) var isRefreshing = false
    @Published private(set) var receivedAt: Date?

    let connectionStore: ConnectionStore
    private let fetch: (DashboardConnection) async throws -> Data
    private var activeRequest: Task<Data, Error>?
    private var generation = 0
    private var lastConnection: DashboardConnection?

    init(connectionStore: ConnectionStore = ConnectionStore(environmentURLKey: "MAVERICK_GLANCE_URL", defaultsURLKey: "glance_url"),
         fetch: @escaping (DashboardConnection) async throws -> Data = { connection in
             try await dashboardData(path: "api/v1/glance", connection: connection, maxBytes: 65536)
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
        glance = nil
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
            if connection != lastConnection { glance = nil; receivedAt = nil; lastConnection = connection }
            let request = Task { try await fetch(connection) }
            activeRequest = request
            let data = try await withTaskCancellationHandler(operation: { try await request.value }, onCancel: { request.cancel() })
            guard current == generation && !Task.isCancelled else { return }
            glance = try JSONDecoder().decode(Glance.self, from: data)
            receivedAt = Date()
            error = nil
        } catch {
            guard current == generation && !Task.isCancelled else { return }
            if !mayRetainSnapshot(after: error) { glance = nil; receivedAt = nil }
            self.error = plainMessage(for: error)
        }
    }
}
