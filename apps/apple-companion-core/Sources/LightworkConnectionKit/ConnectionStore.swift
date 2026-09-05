import Foundation
import Security

public struct DashboardConnection: Codable, Equatable {
    public var address: String
    public var token: String

    public init(address: String = "", token: String = "") {
        self.address = address
        self.token = token
    }

    public func validatedURL() throws -> URL {
        guard let parts = URLComponents(string: address.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = parts.host?.lowercased(), !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.query == nil, parts.fragment == nil,
              let url = parts.url else { throw ConnectionError.invalidAddress }
        let loopback = ["localhost", "127.0.0.1", "::1", "[::1]"].contains(host)
        guard parts.scheme?.lowercased() == "https" || (parts.scheme?.lowercased() == "http" && loopback),
              address.utf8.count <= 4096 else { throw ConnectionError.invalidAddress }
        guard token.utf8.count <= 8192,
              !token.contains(where: { $0.isNewline }) else { throw ConnectionError.invalidToken }
        return url
    }
}

public enum ConnectionError: LocalizedError {
    case invalidAddress, invalidToken, secureStorage(Int32), response(Int)

    public var errorDescription: String? {
        switch self {
        case .invalidAddress: return "Enter a dashboard HTTPS address without sign-in details, a query, or a fragment. HTTP is allowed only on this device."
        case .invalidToken: return "The access token is too long or contains a line break. Check it and try again."
        case .secureStorage(let status): return "Secure storage could not be accessed (\(status)). Unlock this device and try again."
        case .response(let status): return "The dashboard request returned status \(status). Check the address and access token."
        }
    }
}

public protocol ConnectionStorage {
    func read() throws -> Data?
    func write(_ data: Data) throws
}

public struct KeychainConnectionStorage: ConnectionStorage {
    private let service: String
    public init(service: String = Bundle.main.bundleIdentifier ?? "com.lightwork.companion") {
        self.service = service
    }
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service, kSecAttrAccount as String: "dashboard-connection-v1"]
    }
    public func read() throws -> Data? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else {
            throw ConnectionError.secureStorage(status)
        }
        return data
    }
    public func write(_ data: Data) throws {
        // One record changes the address and token together. Never delete the
        // old record first: a failed update must leave it usable.
        let update = [kSecValueData as String: data]
        var status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
            status = SecItemAdd(item as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw ConnectionError.secureStorage(status) }
    }
}

public struct ConnectionStore {
    private let storage: any ConnectionStorage
    private let legacy: () -> DashboardConnection
    private let clearLegacy: () -> Void

    public init(environmentURLKey: String, defaultsURLKey: String) {
        storage = KeychainConnectionStorage()
        legacy = {
            DashboardConnection(
                address: ProcessInfo.processInfo.environment[environmentURLKey]
                    ?? UserDefaults.standard.string(forKey: defaultsURLKey) ?? "",
                token: ProcessInfo.processInfo.environment["MAVERICK_DASHBOARD_TOKEN"]
                    ?? UserDefaults.standard.string(forKey: "dashboard_token") ?? ""
            )
        }
        clearLegacy = { UserDefaults.standard.removeObject(forKey: "dashboard_token") }
    }

    public init(storage: any ConnectionStorage, legacy: @escaping () -> DashboardConnection,
                clearLegacy: @escaping () -> Void = {}) {
        self.storage = storage
        self.legacy = legacy
        self.clearLegacy = clearLegacy
    }

    public func load() throws -> DashboardConnection {
        guard let data = try storage.read() else { return legacy() }
        return try JSONDecoder().decode(DashboardConnection.self, from: data)
    }

    public func save(_ input: DashboardConnection) throws {
        let connection = DashboardConnection(
            address: input.address.trimmingCharacters(in: .whitespacesAndNewlines),
            token: input.token.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        _ = try connection.validatedURL()
        try storage.write(JSONEncoder().encode(connection))
        clearLegacy()
    }
}

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        completionHandler(nil)
    }
}

/// Read-only requests, with no redirects that could move a token to another host.
public func dashboardData(path: String, connection: DashboardConnection) async throws -> Data {
    let url = try connection.validatedURL().appendingPathComponent(path)
    var request = URLRequest(url: url)
    request.cachePolicy = .reloadIgnoringLocalCacheData
    if !connection.token.isEmpty {
        request.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
    }
    let configuration = URLSessionConfiguration.ephemeral
    configuration.timeoutIntervalForRequest = 20
    let session = URLSession(configuration: configuration, delegate: NoRedirects(), delegateQueue: nil)
    defer { session.finishTasksAndInvalidate() }
    let (data, response) = try await session.data(for: request)
    guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
        throw ConnectionError.response((response as? HTTPURLResponse)?.statusCode ?? -1)
    }
    return data
}
