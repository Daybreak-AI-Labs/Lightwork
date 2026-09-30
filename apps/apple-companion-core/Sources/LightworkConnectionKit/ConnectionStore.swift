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
        if address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { throw ConnectionError.missingAddress }
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
    case missingAddress, invalidAddress, invalidToken, secureStorage(Int32), response(Int), unreadable

    // Shown to people as written: plain sentences, no status numbers or codes.
    public var errorDescription: String? {
        switch self {
        case .missingAddress:
            return "Enter your dashboard address. It is the web address you use to open Lightwork on a computer."
        case .invalidAddress:
            return "Enter only the dashboard address, starting with https:// (for example https://lightwork.yourcompany.com). Addresses starting with http:// work only for a dashboard on this same device."
        case .invalidToken:
            return "The access token doesn't look right: it is too long or has a line break in it. Copy it again from your administrator's message."
        case .secureStorage:
            return "Could not open this device's secure storage. Unlock the device, then try again."
        case .response(let status):
            switch status {
            case 401: return "The dashboard did not accept your access token. Check it in Settings or ask your administrator for a new one."
            case 403: return "Your access token does not allow viewing this information. Ask your administrator to check your access."
            case 404: return "This dashboard does not offer this information yet. Ask your administrator to update the dashboard."
            case 429: return "The dashboard is busy. Wait a minute, then try again."
            case 500...599: return "The dashboard had a problem answering. Try again in a few minutes."
            default: return "The dashboard could not answer. Check the address and access token in Settings, then try again."
            }
        case .unreadable:
            return "The dashboard sent a reply this app could not read. Update the app, or ask your administrator to check the dashboard."
        }
    }
}

/// One plain sentence for any failure while talking to the dashboard, so no
/// system error text or code reaches the screen.
public func plainMessage(for error: Error) -> String {
    if let known = error as? ConnectionError, let text = known.errorDescription { return text }
    if error is DecodingError { return ConnectionError.unreadable.errorDescription ?? "" }
    if let urlError = error as? URLError, urlError.code == .timedOut {
        return "The dashboard took too long to answer. Check your network, then try again."
    }
    return "Could not reach the dashboard. Check that you are on your organization's network or VPN, then try again."
}

/// People often type "lightwork.example.com"; assume the secure scheme.
public func addressWithScheme(_ address: String) -> String {
    let trimmed = address.trimmingCharacters(in: .whitespacesAndNewlines)
    if trimmed.isEmpty || trimmed.contains("://") { return trimmed }
    return "https://" + trimmed
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
