import XCTest
import LightworkConnectionKit
@testable import MaverickGlance

private final class GlanceTestStorage: ConnectionStorage {
    func read() throws -> Data? { nil }
    func write(_ data: Data) throws { }
}

@MainActor
final class GlanceModelTests: XCTestCase {
    func testRapidRefreshUsesOneRequestAndDisposalCancelsIt() async {
        let store = ConnectionStore(storage: GlanceTestStorage(), legacy: {
            DashboardConnection(address: "https://dashboard.example")
        })
        let started = expectation(description: "Request started")
        let cancelled = expectation(description: "Request cancelled")
        var calls = 0
        let model = GlanceModel(connectionStore: store, fetch: { _ in
            calls += 1
            started.fulfill()
            do {
                try await Task.sleep(nanoseconds: 60_000_000_000)
                return Data()
            } catch {
                cancelled.fulfill()
                throw error
            }
        })
        let refresh = Task { await model.refresh() }
        await fulfillment(of: [started], timeout: 2)
        await model.refresh()
        XCTAssertEqual(calls, 1)
        model.cancelRefresh()
        await fulfillment(of: [cancelled], timeout: 2)
        await refresh.value
        XCTAssertFalse(model.isRefreshing)
        XCTAssertNil(model.glance)
    }

    func testDenialClearsPrivateSnapshotAndTransientFailureKeepsItsSourceDate() async {
        let store = ConnectionStore(storage: GlanceTestStorage(), legacy: {
            DashboardConnection(address: "https://dashboard.example")
        })
        var status = 200
        let payload = Data(#"{"active":1,"done_today":2,"failed_today":0,"spend_today":3.0,"last_result":"PRIVATE RESULT","as_of":1000}"#.utf8)
        let model = GlanceModel(connectionStore: store, fetch: { _ in
            if status != 200 { throw ConnectionError.response(status) }
            return payload
        })
        await model.refresh()
        XCTAssertEqual(model.glance?.last_result, "PRIVATE RESULT")
        status = 503
        await model.refresh()
        XCTAssertEqual(model.glance?.as_of, 1000)
        XCTAssertNotNil(model.error)
        status = 403
        await model.refresh()
        XCTAssertNil(model.glance)
        XCTAssertNil(model.receivedAt)
        XCTAssertFalse(model.isRefreshing)
    }
}
