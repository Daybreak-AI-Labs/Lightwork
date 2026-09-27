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
                    Text("This app shows your organization's Lightwork work. Enter two things your administrator can give you. The app only views information; it never changes anything.")
                        .font(.footnote)
                }
                Section {
                    TextField("https://lightwork.yourcompany.com", text: $connection.address)
                        .accessibilityLabel("Dashboard address")
                        .autocorrectionDisabled()
                        .disabled(!loaded)
                } header: {
                    Text("Dashboard address")
                } footer: {
                    Text("The web address you use to open Lightwork on a computer.")
                }
                Section {
                    SecureField("Paste the code from your administrator", text: $connection.token)
                        .accessibilityLabel("Access token")
                        .disabled(!loaded)
                } header: {
                    Text("Access token")
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
                    Button("Save") { save() }
                        .disabled(!loaded)
                    if testPath != nil {
                        Button(testing ? "Testing…" : "Test connection") { Task { await test() } }
                            .disabled(!loaded || testing)
                    }
                    Button("Cancel", role: .cancel) { close() }
                }
            }
            .navigationTitle("Settings")
            .task { load() }
        }
    }

    private func normalized() -> DashboardConnection {
        DashboardConnection(address: addressWithScheme(connection.address),
                            token: connection.token.trimmingCharacters(in: .whitespacesAndNewlines))
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
            note = "Connected. Tap Save to start using this connection."
        } catch {
            self.error = plainMessage(for: error)
        }
        testing = false
    }

    private func load() {
        do {
            connection = try store.load()
            loaded = true
            error = nil
        } catch {
            loaded = false
            self.error = error is ConnectionError ? plainMessage(for: error)
                : "Could not open your saved settings. Unlock this device and try again. If this keeps happening, ask your administrator for help."
        }
    }

    private func close() {
        if let onClose { onClose() } else { dismiss() }
    }
}
