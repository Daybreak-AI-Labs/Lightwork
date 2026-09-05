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
}
