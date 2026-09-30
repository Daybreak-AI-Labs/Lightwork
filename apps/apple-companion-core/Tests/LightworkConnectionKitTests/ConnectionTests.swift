import XCTest
@testable import LightworkConnectionKit

final class MemoryStorage: ConnectionStorage {
    var data: Data?
    var failWrites = false
    func read() throws -> Data? { data }
    func delete() throws {
        if failWrites { throw ConnectionError.secureStorage(-1) }
        data = nil
    }
    func write(_ data: Data) throws {
        if failWrites { throw ConnectionError.secureStorage(-1) }
        self.data = data
    }
}

final class ConnectionTests: XCTestCase {
    func testLegacyConnectionStillDecodesWithoutPairingMetadata() throws {
        let value = try JSONDecoder().decode(DashboardConnection.self,
            from: Data("{\"address\":\"https://example.test\",\"token\":\"synthetic\"}".utf8))
        XCTAssertNil(value.pairedDevice)
        XCTAssertEqual(value.token, "synthetic")
    }

    func testPairingMetadataAndCredentialAreOneAtomicSecureWrite() throws {
        let storage = MemoryStorage()
        let store = ConnectionStore(storage: storage, legacy: { DashboardConnection() })
        let device = PairedDevice(id: "device", generation: 1, expires_at: 100,
            enrollment_expires_at: 200, principal: "user:alice", tenant: "workspace", actions: false)
        let before = DashboardConnection(address: "https://example.test", token: "first", pairedDevice: device)
        try store.save(before)
        storage.failWrites = true
        XCTAssertThrowsError(try store.save(DashboardConnection(address: "https://example.test", token: "rotated")))
        XCTAssertEqual(try store.load(), before)
    }

    func testOnlyTransientFailuresMayRetainPrivateSnapshots() {
        for status in [400, 401, 403, 404] {
            XCTAssertFalse(mayRetainSnapshot(after: ConnectionError.response(status)))
        }
        XCTAssertTrue(mayRetainSnapshot(after: ConnectionError.response(503)))
        XCTAssertTrue(mayRetainSnapshot(after: URLError(.timedOut)))
        XCTAssertFalse(mayRetainSnapshot(after: ConnectionError.secureStorage(-1)))
        XCTAssertFalse(mayRetainSnapshot(after: ConnectionError.responseTooLarge))
    }

    func testStreamingCapStopsAtLimitWithoutRequiringContentLength() async throws {
        let stream = AsyncStream<UInt8> { continuation in
            for _ in 0..<17 { continuation.yield(42) }
            continuation.finish()
        }
        do {
            _ = try await boundedResponse(stream, maxBytes: 16)
            XCTFail("An oversized stream must fail")
        } catch ConnectionError.responseTooLarge { }
        let exact = AsyncStream<UInt8> { continuation in
            for _ in 0..<16 { continuation.yield(42) }
            continuation.finish()
        }
        let data = try await boundedResponse(exact, maxBytes: 16)
        XCTAssertEqual(data.count, 16)
    }

    func testExplicitResetRemovesCorruptRecordAndKeepsOtherStorageOnFailure() throws {
        let memory = MemoryStorage()
        let store = ConnectionStore(storage: memory, legacy: { DashboardConnection() })
        memory.data = Data("invalid".utf8)
        memory.failWrites = true
        XCTAssertThrowsError(try store.reset())
        XCTAssertNotNil(memory.data)
        memory.failWrites = false
        try store.reset()
        XCTAssertNil(memory.data)
    }

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
