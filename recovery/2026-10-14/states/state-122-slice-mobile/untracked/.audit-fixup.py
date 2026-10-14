from pathlib import Path

def edit(name, f):
 p=Path(name);p.write_text(f(p.read_text(encoding='utf-8')),encoding='utf-8',newline='\n')
edit('apps/mobile-companion/src/screens/GlanceScreen.tsx',lambda s:s.replace('        <Stat\n          label="Total spend"', '''        <Stat label="Waiting for approval" value={props.liveBundle ? String(props.liveBundle.glance.counts.pending_approvals) : "Unavailable"} />
        <Stat label="Questions for you" value={props.liveBundle ? String(props.liveBundle.glance.counts.open_questions) : "Unavailable"} />
        <Stat
          label="Total spend"'''))
edit('apps/mobile-companion/src/offlineCache.ts', lambda s:s.replace('const MAX_AGE_MS', 'const retiredScopes = new Set<string>();\nconst MAX_AGE_MS').replace('  if (!key) return;','  if (!key || retiredScopes.has(key)) return;',1).replace('  await AsyncStorage.setItem(key, serialized);', '  await AsyncStorage.setItem(key, serialized);\n  if (retiredScopes.has(key)) await AsyncStorage.removeItem(key);').replace('  if (key) await AsyncStorage.removeItem(key);','  if (key) { retiredScopes.add(key); await AsyncStorage.removeItem(key); }'))
edit('apps/mobile-companion/src/screens/SettingsScreen.tsx',lambda s:s.replace('useEffect(() => () => { mounted.current = false; generation.current += 1; testController.current?.abort(); }, []);','useEffect(() => { mounted.current = true; return () => { mounted.current = false; generation.current += 1; testController.current?.abort(); }; }, []);').replace('if (loading || loadFailed || testing) return;', 'if (loading || loadFailed || saving || testing) return;'))
edit('apps/mobile-companion/src/screens/RunDetailScreen.tsx',lambda s:s.replace('  function openDashboard()', '  const [linkError, setLinkError] = useState("");\n  function openDashboard()').replace('void Linking.openURL(url).catch(() => undefined);','void Linking.openURL(url).catch(() => setLinkError("Could not open the browser. Open your dashboard from a browser to review the full task."));').replace('      <Button label="Open full task', '      {linkError ? <Banner text={linkError} /> : null}\n      <Button label="Open full task'))
edit('apps/apple-companion-core/Sources/LightworkConnectionKit/ConnectionStore.swift',lambda s:s.replace('    func write(_ data: Data) throws\n}', '    func write(_ data: Data) throws\n    func delete() throws\n}\n\npublic extension ConnectionStorage {\n    func delete() throws { throw ConnectionError.secureStorage(errSecUnimplemented) }\n}').replace('    public func write(_ data: Data) throws {','    public func delete() throws {\n        let status = SecItemDelete(query as CFDictionary)\n        guard status == errSecSuccess || status == errSecItemNotFound else { throw ConnectionError.secureStorage(status) }\n    }\n    public func write(_ data: Data) throws {',1).replace('clearLegacy = { UserDefaults.standard.removeObject(forKey: "dashboard_token") }','clearLegacy = {\n            UserDefaults.standard.removeObject(forKey: "dashboard_token")\n            UserDefaults.standard.removeObject(forKey: defaultsURLKey)\n        }').replace('    public func save(_ input:', '    public func reset() throws {\n        _ = try storage.read() // Refuse to erase a locked or inaccessible store.\n        try storage.delete()\n        clearLegacy()\n    }\n\n    public func save(_ input:'))
edit('apps/apple-companion-core/Sources/LightworkConnectionKit/ConnectionSettingsView.swift',lambda s:s.replace('    @State private var testing = false','    @State private var testing = false\n    @State private var active = true\n    @State private var corrupt = false\n    @State private var confirmReset = false').replace('.disabled(!loaded)', '.disabled(!loaded || testing)').replace('                    if !loaded { Button("Retry loading settings", action: load) }','                    if !loaded { Button("Retry loading settings", action: load) }\n                    if corrupt { Button("Reset saved connection", role: .destructive) { confirmReset = true } }').replace('            .task { load() }','''            .task { active = true; load() }
            .onDisappear { active = false }
            .onChange(of: connection) { _ in note = nil }
            .confirmationDialog("Remove this app’s saved connection?", isPresented: $confirmReset) {
                Button("Reset saved connection", role: .destructive) {
                    do { try store.reset(); connection = DashboardConnection(); loaded = true; corrupt = false; error = nil }
                    catch { self.error = plainMessage(for: error) }
                }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Only this app’s connection is removed. Your dashboard is unchanged.") }''').replace('            note = "Connected.', '            guard active && normalized() == candidate else { testing = false; return }\n            note = "Connected.').replace('            self.error = plainMessage(for: error)\n        }\n        testing', '            if active && normalized() == candidate { self.error = plainMessage(for: error) }\n        }\n        testing').replace('            loaded = true\n            error = nil','            loaded = true\n            corrupt = false\n            error = nil').replace('            loaded = false\n            self.error', '            loaded = false\n            corrupt = error is DecodingError\n            self.error'))
edit('apps/apple-companion-core/Tests/LightworkConnectionKitTests/ConnectionTests.swift',lambda s:s.replace('    func read() throws -> Data? { data }','    func read() throws -> Data? { data }\n    func delete() throws {\n        if failWrites { throw ConnectionError.secureStorage(-1) }\n        data = nil\n    }').replace('final class ConnectionTests: XCTestCase {','''final class ConnectionTests: XCTestCase {
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
'''))
