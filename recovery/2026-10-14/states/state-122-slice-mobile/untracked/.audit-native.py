from pathlib import Path
p=Path('apps/apple-companion-core/Sources/LightworkConnectionKit/ConnectionStore.swift');s=p.read_text(encoding='utf-8');s=s.replace('response(Int), unreadable','response(Int), unreadable, responseTooLarge');s=s.replace('        case .unreadable:', '        case .responseTooLarge:\n            return "The dashboard reply was too large for this device. Narrow the view on the dashboard and try again."\n        case .unreadable:');s=s.replace('public func dashboardData(path: String, connection: DashboardConnection) async throws -> Data {','public func dashboardData(path: String, connection: DashboardConnection, maxBytes: Int = 2_097_152) async throws -> Data {');s=s.replace('    configuration.timeoutIntervalForRequest = 20','    configuration.timeoutIntervalForRequest = 20\n    configuration.timeoutIntervalForResource = 25');s=s.replace('    defer { session.finishTasksAndInvalidate() }\n    let (data, response) = try await session.data(for: request)','    defer { session.invalidateAndCancel() }\n    let (bytes, response) = try await session.bytes(for: request)');s=s.replace('    return data\n}', '''    let limit = min(max(1, maxBytes), 2_097_152)
    guard response.expectedContentLength <= Int64(limit) else { throw ConnectionError.responseTooLarge }
    return try await boundedResponse(bytes, maxBytes: limit)
}

/// A byte cap applies even when Content-Length is absent or misleading.
public func boundedResponse<Bytes: AsyncSequence>(_ bytes: Bytes, maxBytes: Int) async throws -> Data where Bytes.Element == UInt8 {
    let limit = min(max(1, maxBytes), 2_097_152)
    var data = Data()
    data.reserveCapacity(min(limit, 65_536))
    for try await byte in bytes {
        if data.count % 4096 == 0 { try Task.checkCancellation() }
        guard data.count < limit else { throw ConnectionError.responseTooLarge }
        data.append(byte)
    }
    try Task.checkCancellation()
    return data
}

/// Only explicitly transient failures may retain same-connection snapshots.
public func mayRetainSnapshot(after error: Error) -> Bool {
    if let known = error as? ConnectionError, case .response(let status) = known {
        return status == 429 || (500...599).contains(status)
    }
    if let network = error as? URLError {
        return [.timedOut, .notConnectedToInternet, .networkConnectionLost,
                .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed].contains(network.code)
    }
    return false
}''');p.write_text(s,encoding='utf-8',newline='\n')
for name, model, payload, prop, env, defaults, endpoint, cap in [
 ('apps/watch-glance/MaverickGlance/GlanceModel.swift','GlanceModel','Glance','glance','MAVERICK_GLANCE_URL','glance_url','api/v1/glance',65536),
 ('apps/visionos-plan-tree/MaverickPlanTree/PlanTreeModel.swift','PlanTreeModel','GoalForest','forest','MAVERICK_DASHBOARD_URL','dashboard_url','api/v1/goal-tree',2097152),
]:
 p=Path(name);s=p.read_text(encoding='utf-8');a=s.index('@MainActor\nfinal class');b=s.find('\n/// Status',a);b=len(s) if b<0 else b
 new=f'''@MainActor
final class {model}: ObservableObject {{
    @Published var {prop}: {payload}?
    @Published var error: String?
    @Published private(set) var isRefreshing = false
    @Published private(set) var receivedAt: Date?

    let connectionStore: ConnectionStore
    private let fetch: (DashboardConnection) async throws -> Data
    private var activeRequest: Task<Data, Error>?
    private var generation = 0
    private var lastConnection: DashboardConnection?

    init(connectionStore: ConnectionStore = ConnectionStore(environmentURLKey: "{env}", defaultsURLKey: "{defaults}"),
         fetch: @escaping (DashboardConnection) async throws -> Data = {{ connection in
             try await dashboardData(path: "{endpoint}", connection: connection, maxBytes: {cap})
         }}) {{
        self.connectionStore = connectionStore
        self.fetch = fetch
    }}

    func cancelRefresh() {{
        generation += 1
        activeRequest?.cancel()
        activeRequest = nil
        isRefreshing = false
    }}

    func connectionChanged() {{
        cancelRefresh()
        {prop} = nil
        receivedAt = nil
        error = nil
        lastConnection = nil
    }}

    func refresh() async {{
        guard !isRefreshing else {{ return }}
        generation += 1
        let current = generation
        isRefreshing = true
        defer {{ if current == generation {{ isRefreshing = false; activeRequest = nil }} }}
        do {{
            let connection = try connectionStore.load()
            if connection != lastConnection {{ {prop} = nil; receivedAt = nil; lastConnection = connection }}
            let request = Task {{ try await fetch(connection) }}
            activeRequest = request
            let data = try await withTaskCancellationHandler(operation: {{ try await request.value }}, onCancel: {{ request.cancel() }})
            guard current == generation && !Task.isCancelled else {{ return }}
            {prop} = try JSONDecoder().decode({payload}.self, from: data)
            receivedAt = Date()
            error = nil
        }} catch {{
            guard current == generation && !Task.isCancelled else {{ return }}
            if !mayRetainSnapshot(after: error) {{ {prop} = nil; receivedAt = nil }}
            self.error = plainMessage(for: error)
        }}
    }}
}}
'''
 s=s[:a]+new+s[b:];p.write_text(s,encoding='utf-8',newline='\n')
p=Path('apps/watch-glance/MaverickGlance/GlanceView.swift');s=p.read_text(encoding='utf-8').replace('/// One-screen glance: counts, today\'s spend, last result. Complication-sized.','/// Read-only preview with an explicit source date and freshness.');s=s.replace('    @State private var settingsShown = false','    @State private var settingsShown = false\n    @Environment(\\.scenePhase) private var scenePhase');s=s.replace('                Text("Today").font(.headline)','''                Text("UTC day: " + sourceDay(g.as_of)).font(.headline)
                Text("Source: " + Date(timeIntervalSince1970: Double(g.as_of)).formatted(date: .abbreviated, time: .shortened)).font(.caption2)
                if model.error != nil { Text("Saved snapshot — not current").font(.caption).bold() }''');s=s.replace('Spent $%.2f today','Spent $%.2f for this UTC day').replace('Nothing has run today.', 'Nothing had run for this UTC day.');s=s.replace('Button(model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }','Button(model.isRefreshing ? "Updating…" : model.error == nil ? "Refresh" : "Try again") { Task { await model.refresh() } }\n                .disabled(model.isRefreshing)');s=s.replace('        .refreshable { await model.refresh() }','''        .refreshable { await model.refresh() }
        .onDisappear { model.cancelRefresh() }
        .onChange(of: scenePhase) { phase in
            if phase == .active { Task { await model.refresh() } } else { model.cancelRefresh() }
        }''');s=s.replace('    private func countRow', '''    private func sourceDay(_ timestamp: Int) -> String {
        let formatter = DateFormatter()
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateStyle = .medium
        return formatter.string(from: Date(timeIntervalSince1970: Double(timestamp)))
    }

    private func countRow''');p.write_text(s,encoding='utf-8',newline='\n')
