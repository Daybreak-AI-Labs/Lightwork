import XCTest
import LightworkConnectionKit
@testable import MaverickPlanTree

private final class PlanTestStorage: ConnectionStorage {
    func read() throws -> Data? { nil }
    func write(_ data: Data) throws { }
}

@MainActor
final class PlanTreeModelTests: XCTestCase {
    func testRefreshPublishesLatestNodeAndDenialClearsForest() async {
        let store = ConnectionStore(storage: PlanTestStorage(), legacy: {
            DashboardConnection(address: "https://dashboard.example")
        })
        var status = "active"
        var denied = false
        let model = PlanTreeModel(connectionStore: store, fetch: { _ in
            if denied { throw ConnectionError.response(401) }
            return Data("{\"nodes\":[{\"id\":1,\"parent_id\":null,\"title\":\"PRIVATE TASK\",\"status\":\"\(status)\",\"depth\":0,\"x\":0,\"y\":0}],\"edges\":[],\"count\":1}".utf8)
        })
        await model.refresh()
        XCTAssertEqual(model.forest?.nodes.first?.status, "active")
        status = "done"
        await model.refresh()
        XCTAssertEqual(model.forest?.nodes.first?.status, "done")
        denied = true
        await model.refresh()
        XCTAssertNil(model.forest)
        XCTAssertNil(model.receivedAt)
    }
}
