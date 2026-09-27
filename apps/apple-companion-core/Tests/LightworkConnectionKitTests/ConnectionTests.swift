import XCTest
@testable import LightworkConnectionKit

final class MemoryStorage: ConnectionStorage {
    var data: Data?
    var failWrites = false
    func read() throws -> Data? { data }
    func write(_ data: Data) throws {
        if failWrites { throw ConnectionError.secureStorage(-1) }
        self.data = data
    }
}

final class ConnectionTests: XCTestCase {
    func testRemoteAddressRequiresHTTPSAndRejectsEmbeddedCredentials() throws {
        for address in ["http://example.com", "https://u:p@example.com", "https://example.com?q=x", "file:///tmp/a", "https:///missing"] {
            XCTAssertThrowsError(try DashboardConnection(address: address).validatedURL())
        }
        for address in ["https://example.com/prefix", "http://127.0.0.1:8765", "http://[::1]:8765"] {
            XCTAssertNoThrow(try DashboardConnection(address: address).validatedURL())
        }
    }

    func testSaveCommitsAddressAndTokenTogetherAndSupersedesLegacy() throws {
        let memory = MemoryStorage()
        let old = DashboardConnection(address: "https://old.example", token: "old-synthetic")
        var cleared = false
        let store = ConnectionStore(storage: memory, legacy: { old }, clearLegacy: { cleared = true })
        XCTAssertEqual(try store.load(), old)
        let current = DashboardConnection(address: "https://new.example", token: "new-synthetic")
        try store.save(current)
        XCTAssertEqual(try store.load(), current)
        XCTAssertTrue(cleared)
    }

    func testFailedSavePreservesOldConnectionAndLegacyToken() throws {
        let memory = MemoryStorage()
        let old = DashboardConnection(address: "https://old.example", token: "old-synthetic")
        var cleared = false
        let store = ConnectionStore(storage: memory, legacy: { old }, clearLegacy: { cleared = true })
        memory.failWrites = true
        XCTAssertThrowsError(try store.save(DashboardConnection(address: "https://new.example", token: "new-synthetic")))
        XCTAssertEqual(try store.load(), old)
        XCTAssertFalse(cleared)
        XCTAssertNil(memory.data)
    }

    func testInvalidAddressNeverChangesSavedRecord() throws {
        let memory = MemoryStorage()
        let store = ConnectionStore(storage: memory, legacy: { DashboardConnection() })
        XCTAssertThrowsError(try store.save(DashboardConnection(address: "http://remote.example", token: "synthetic")))
        XCTAssertNil(memory.data)
    }

    func testCorruptSecureRecordDoesNotFallBackToLegacyCredentials() {
        let memory = MemoryStorage()
        memory.data = Data("invalid".utf8)
        let store = ConnectionStore(storage: memory, legacy: { DashboardConnection(address: "https://old.example", token: "old-synthetic") })
        XCTAssertThrowsError(try store.load())
    }

    func testMessagesArePlainAndCarryNoStatusNumbers() {
        for status in [400, 401, 403, 404, 429, 500, 503, -1] {
            let text = ConnectionError.response(status).errorDescription ?? ""
            XCTAssertFalse(text.isEmpty)
            XCTAssertNil(text.range(of: "[0-9]{3}", options: .regularExpression), text)
            XCTAssertFalse(text.contains("status"), text)
        }
        XCTAssertFalse((ConnectionError.secureStorage(-25300).errorDescription ?? "").contains("25300"))
        XCTAssertEqual(plainMessage(for: URLError(.notConnectedToInternet)).hasPrefix("Could not reach the dashboard"), true)
    }

    func testEmptyAddressAsksForOneAndBareHostGetsSecureScheme() {
        XCTAssertThrowsError(try DashboardConnection(address: "  ").validatedURL()) { error in
            XCTAssertEqual(error.localizedDescription, ConnectionError.missingAddress.errorDescription)
        }
        XCTAssertEqual(addressWithScheme(" lightwork.example.com "), "https://lightwork.example.com")
        XCTAssertEqual(addressWithScheme("http://127.0.0.1:8400"), "http://127.0.0.1:8400")
    }
}
