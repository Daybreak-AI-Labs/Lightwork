import Foundation
import SwiftUI

public struct PairedDevice: Codable, Equatable {
    public let id: String
    public let generation: Int
    public let expires_at: Double
    public let enrollment_expires_at: Double
    public let principal: String
    public let tenant: String?
    public let actions: Bool
}

struct PairingRequest: Decodable {
    let pairing_token: String
    let code: String
    let expires_at: Double
}

struct PairingReply: Decodable {
    let status: String?
    let token: String?
    let device: PairedDevice?
}

func beginDevicePairing(address: String) async throws -> PairingRequest {
    let data = try await dashboardRequest(path: "api/v1/devices/pairing/begin",
        connection: DashboardConnection(address: address),
        body: JSONSerialization.data(withJSONObject: ["label": "My Apple companion"]))
    return try JSONDecoder().decode(PairingRequest.self, from: data)
}

func pollDevicePairing(address: String, proof: String) async throws -> PairingReply {
    let data = try await dashboardRequest(path: "api/v1/devices/pairing/poll",
        connection: DashboardConnection(address: address, token: proof), body: Data("{}".utf8))
    return try JSONDecoder().decode(PairingReply.self, from: data)
}

func renewDevicePairing(connection: DashboardConnection) async throws -> DashboardConnection {
    guard let device = connection.pairedDevice else { throw ConnectionError.invalidToken }
    let data = try await dashboardRequest(path: "api/v1/devices/renew", connection: connection,
        body: JSONSerialization.data(withJSONObject: ["generation": device.generation]))
    let reply = try JSONDecoder().decode(PairingReply.self, from: data)
    guard let token = reply.token, let nextDevice = reply.device else { throw ConnectionError.unreadable }
    return DashboardConnection(address: connection.address, token: token, pairedDevice: nextDevice)
}

@MainActor
struct DevicePairingSection: View {
    let address: String
    let disabled: Bool
    let onPaired: (DashboardConnection) throws -> Void
    @Environment(\.scenePhase) private var scenePhase
    @State private var pending: PairingRequest?
    @State private var busy = false
    @State private var note: String?
    @State private var error: String?
    @State private var work: Task<Void, Never>?
    @State private var generation = 0
    @State private var lastPoll = Date.distantPast

    var body: some View {
        Section {
            Text("Approve a short-lived code in your signed-in dashboard. No access-token copying is needed.").font(.footnote)
            if let pending {
                Text(pending.code).font(.title3.monospaced())
                    .accessibilityLabel("Pairing code " + pending.code.map(String.init).joined(separator: " "))
                Text("On a computer, open this dashboard, choose Your account, then Your paired devices, and enter the code.").font(.footnote)
                Button("Check pairing approval") { check() }.disabled(busy || disabled)
                Button("Cancel pairing", role: .cancel) { cancel() }
            } else {
                Button("Start device pairing") { start() }.disabled(busy || disabled)
            }
            if let note { Text(note).font(.footnote) }
            if let error { Text(error).foregroundStyle(.red).font(.footnote) }
        } header: { Text("Pair with your account") }
        .onDisappear { cancel() }
        .onChange(of: address) { _ in cancel() }
        .onChange(of: scenePhase) { phase in if phase != .active { pause() } }
    }

    private func pause() { generation += 1; work?.cancel(); work = nil; busy = false }
    private func cancel() { pause(); pending = nil }

    private func start() {
        guard !busy && !disabled else { return }
        pause(); let version = generation; busy = true; note = nil; error = nil
        let candidate = addressWithScheme(address)
        work = Task {
            do {
                let reply = try await beginDevicePairing(address: candidate)
                guard !Task.isCancelled && version == generation else { return }
                pending = reply
                note = "Compare the full code before approving. It expires in five minutes."
            } catch { if version == generation { self.error = plainMessage(for: error) } }
            if version == generation { busy = false }
        }
    }

    private func check() {
        guard !busy && !disabled, let pending else { return }
        guard Date().timeIntervalSince1970 < pending.expires_at else { cancel(); error = "The pairing code expired. Start again."; return }
        guard Date().timeIntervalSince(lastPoll) >= 5 else { return }
        lastPoll = Date(); pause(); let version = generation; busy = true; error = nil
        let candidate = addressWithScheme(address)
        work = Task {
            do {
                let reply = try await pollDevicePairing(address: candidate, proof: pending.pairing_token)
                guard !Task.isCancelled && version == generation else { return }
                if reply.status == "approved", let token = reply.token, let device = reply.device {
                    try onPaired(DashboardConnection(address: candidate, token: token, pairedDevice: device))
                    self.pending = nil
                    note = "Device paired and saved securely."
                } else { note = "Waiting for explicit approval on your dashboard." }
            } catch { if version == generation { self.error = plainMessage(for: error) } }
            if version == generation { busy = false }
        }
    }
}
