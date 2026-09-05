import SwiftUI

@MainActor
public struct ConnectionSettingsView: View {
    private let store: ConnectionStore
    private let onSaved: () -> Void
    private let onClose: (() -> Void)?
    @Environment(\.dismiss) private var dismiss
    @State private var connection = DashboardConnection()
    @State private var loaded = false
    @State private var error: String?

    public init(store: ConnectionStore, onClose: (() -> Void)? = nil, onSaved: @escaping () -> Void) {
        self.store = store
        self.onSaved = onSaved
        self.onClose = onClose
    }

    public var body: some View {
        NavigationStack {
            Form {
                Text("Enter your organization's dashboard address and access token. This app only views your work.")
                TextField("Dashboard HTTPS address", text: $connection.address)
                    .disabled(!loaded)
                SecureField("Access token", text: $connection.token)
                    .disabled(!loaded)
                Text("Your token stays in this device's secure storage. Leave it empty only if sign-in is not required.")
                    .font(.caption)
                if let error {
                    Text("Connection settings need attention.").foregroundStyle(.red)
                    DisclosureGroup("Connection details") { Text(error).font(.caption) }
                }
                if !loaded { Button("Retry loading settings", action: load) }
                Button("Save connection") {
                    do {
                        try store.save(connection)
                        onSaved()
                        close()
                    } catch { self.error = error.localizedDescription }
                }.disabled(!loaded)
                Button("Cancel") { close() }
            }
            .navigationTitle("Connection")
            .task { load() }
        }
    }

    private func load() {
        do {
            connection = try store.load()
            loaded = true
            error = nil
        } catch {
            loaded = false
            self.error = error.localizedDescription
        }
    }

    private func close() {
        if let onClose { onClose() } else { dismiss() }
    }
}
