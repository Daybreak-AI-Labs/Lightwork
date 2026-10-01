import SwiftUI

@MainActor
public struct ConnectionSettingsView: View {
    private let store: ConnectionStore
    private let testPath: String?
    private let onSaved: () -> Void
    private let onClose: (() -> Void)?
    @Environment(\.dismiss) private var dismiss
    @State private var connection = DashboardConnection()
    @State private var loaded = false
    @State private var error: String?
    @State private var note: String?
    @State private var testing = false
    @State private var active = true
    @State private var corrupt = false
    @State private var confirmReset = false

    /// `testPath` is the read-only dashboard page this app shows; when given,
    /// a "Test connection" button checks the entered details without saving.
    public init(store: ConnectionStore, testPath: String? = nil, onClose: (() -> Void)? = nil,
                onSaved: @escaping () -> Void) {
        self.store = store
        self.testPath = testPath
        self.onSaved = onSaved
        self.onClose = onClose
    }

    public var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Enter your dashboard address, then pair with your signed-in account. This companion reads your own work. Its pairing and renewal controls manage only its connection.")
                        .font(.footnote)
                }
                Section {
                    TextField("https://lightwork.yourcompany.com", text: Binding(get: { connection.address }, set: { connection.address = $0; connection.pairedDevice = nil }))
                        .accessibilityLabel("Dashboard address")
                        .autocorrectionDisabled()
                        .disabled(!loaded || testing)
                } header: {
                    Text("Dashboard address")
                } footer: {
                    Text("The web address you use to open Lightwork on a computer.")
                }
                DevicePairingSection(address: connection.address, disabled: !loaded || testing) { candidate in
                    try store.save(candidate)
                    connection = candidate
                    onSaved()
                    close()
                }
                if let device = connection.pairedDevice {
                    Section {
                        Text("Paired to " + device.principal + " in " + (device.tenant ?? "the default workspace"))
                        Text("Expires " + Date(timeIntervalSince1970: device.expires_at).formatted())
                        Button("Renew paired connection") { Task { await renew() } }.disabled(!loaded || testing)
                    }
                }
                Section {
                    SecureField("Paste the code from your administrator", text: Binding(get: { connection.token }, set: { connection.token = $0; connection.pairedDevice = nil }))
                        .accessibilityLabel("Access token")
                        .disabled(!loaded || testing)
                } header: {
                    Text("Advanced administrative token connection")
                } footer: {
                    Text("A private code kept in this device's secure storage. Leave it empty only if your administrator says sign-in is not needed.")
                }
                if let error {
                    Section {
                        Text(error).foregroundStyle(.red)
                    }
                }
                if let note {
                    Section { Text(note).foregroundStyle(.green) }
                }
                Section {
                    if !loaded { Button("Retry loading settings", action: load) }
                    if corrupt { Button("Reset saved connection", role: .destructive) { confirmReset = true } }
                    Button("Save") { save() }
                        .disabled(!loaded || testing)
                    if testPath != nil {
                        Button(testing ? "Testing…" : "Test connection") { Task { await test() } }
                            .disabled(!loaded || testing)
                    }
                    Button("Cancel", role: .cancel) { close() }
                }
            }
            .navigationTitle("Settings")
            .task { active = true; load() }
            .onDisappear { active = false }
            .onChange(of: connection) { _ in note = nil }
            .confirmationDialog("Remove this app’s saved connection?", isPresented: $confirmReset) {
                Button("Reset saved connection", role: .destructive) {
                    do { try store.reset(); connection = DashboardConnection(); loaded = true; corrupt = false; error = nil }
                    catch { self.error = plainMessage(for: error) }
                }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Only this app’s connection is removed. Your dashboard is unchanged.") }
        }
    }

    private func normalized() -> DashboardConnection {
        DashboardConnection(address: addressWithScheme(connection.address),
                            token: connection.token.trimmingCharacters(in: .whitespacesAndNewlines), pairedDevice: connection.pairedDevice)
    }

    private func renew() async {
        guard !testing else { return }
        let candidate = normalized(); testing = true; error = nil; note = nil
        do {
            let next = try await renewDevicePairing(connection: candidate)
            guard active && normalized() == candidate else { testing = false; return }
            try store.save(next); connection = next; onSaved()
            note = "Device connection renewed and stored securely."
        } catch {
            if active { self.error = "Renewal could not be saved. Retry within one minute; if the connection expired, pair again." }
        }
        testing = false
    }

    private func save() {
        note = nil
        let candidate = normalized()
        connection.address = candidate.address
        do {
            try store.save(candidate)
            error = nil
            onSaved()
            close()
        } catch {
            self.error = plainMessage(for: error)
        }
    }

    private func test() async {
        guard let testPath else { return }
        let candidate = normalized()
        connection.address = candidate.address
        testing = true
        error = nil
        note = nil
        do {
            _ = try await dashboardData(path: testPath, connection: candidate)
            guard active && normalized() == candidate else { testing = false; return }
            note = "Connected. Tap Save to start using this connection."
        } catch {
            if active && normalized() == candidate { self.error = plainMessage(for: error) }
        }
        testing = false
    }

    private func load() {
        do {
            connection = try store.load()
            loaded = true
            corrupt = false
            error = nil
        } catch {
            loaded = false
            corrupt = error is DecodingError
            self.error = error is ConnectionError ? plainMessage(for: error)
                : "Could not open your saved settings. Unlock this device and try again. If this keeps happening, ask your administrator for help."
        }
    }

    private func close() {
        if let onClose { onClose() } else { dismiss() }
    }
}
