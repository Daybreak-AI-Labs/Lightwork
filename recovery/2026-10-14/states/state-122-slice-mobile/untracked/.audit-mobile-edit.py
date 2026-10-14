from pathlib import Path
p=Path('apps/mobile-companion/src/settings.ts');s=p.read_text();s=s.replace('import { connectionValidationError }','import { purgeBundle, purgeAppBundles } from "./offlineCache";\nimport { connectionValidationError }');s=s.replace('http://127.0.0.1:8400','http://127.0.0.1:8765');s=s.replace('export function loadSettings()', '''export class InvalidSavedConnection extends Error {}

export function resetSettings(): Promise<void> {
  return inOrder(async () => {
    // A failed/locked read refuses reset; only an explicit confirmation reaches
    // this path and only this app's named records are removed.
    await SecureStore.getItemAsync(CONNECTION_KEY);
    await SecureStore.deleteItemAsync(CONNECTION_KEY);
    await SecureStore.deleteItemAsync(BASE_URL_KEY);
    await SecureStore.deleteItemAsync(TOKEN_KEY);
    await purgeAppBundles();
  });
}

export function loadSettings()''');s=s.replace('throw new Error("Saved connection settings are invalid.")','throw new InvalidSavedConnection("Saved connection settings are invalid. Reset this app’s saved connection to set it up again.")');s=s.replace('return { baseUrl: value.baseUrl, token: value.token, cacheScope };','return { baseUrl: value.baseUrl, token: value.token, cacheScope, offlineEnabled: !("offlineEnabled" in value) || value.offlineEnabled !== false };');s=s.replace('  return inOrder(() => {\n    const error', '  return inOrder(async () => {\n    const error');s=s.replace('    return SecureStore.setItemAsync(CONNECTION_KEY, JSON.stringify({','''    const previous = await SecureStore.getItemAsync(CONNECTION_KEY);
    let oldScope: string | undefined;
    try { oldScope = previous ? JSON.parse(previous).cacheScope : undefined; } catch { /* invalid record is replaced only by explicit save */ }
    await SecureStore.setItemAsync(CONNECTION_KEY, JSON.stringify({''');s=s.replace('      cacheScope: newCacheScope(),','      cacheScope: newCacheScope(),\n      offlineEnabled: s.offlineEnabled !== false,');s=s.replace('    }));\n  });\n}', '    }));\n    await purgeBundle(oldScope);\n  });\n}');p.write_text(s)
p=Path('apps/mobile-companion/src/api.ts');s=p.read_text().replace('needsSetup?: boolean','needsSetup?: boolean; offlineEnabled?: boolean');s=s.replace('export function listGoals(cfg: Settings, limit = 50, signal?: AbortSignal): Promise<Goal[]> {\n  return apiGet<Goal[]>(cfg, `/api/v1/goals?limit=${limit}`, signal);\n}', '''export function listGoals(cfg: Settings, limit = 50, signal?: AbortSignal, offset = 0, status = ""): Promise<Goal[]> {
  return apiGet<Goal[]>(cfg, `/api/v1/goals?limit=${limit}&offset=${offset}&status=${encodeURIComponent(status)}`, signal);
}

export function searchGoals(cfg: Settings, query: string, signal?: AbortSignal): Promise<Goal[]> {
  return apiGet<Goal[]>(cfg, `/api/v1/goals/search?q=${encodeURIComponent(query)}&limit=200`, signal);
}''');p.write_text(s)
p=Path('apps/mobile-companion/src/offlineCache.ts');s=p.read_text().replace(' * versioned, no secrets), so caching it on-device is safe by construction.',' * versioned, no credentials). Task content can still be sensitive: people may\n * disable persistence, and saved content expires after 24 hours.');s=s.replace('const SCHEMA =', 'const MAX_AGE_MS = 24 * 60 * 60 * 1000;\nconst MAX_BYTES = 2 * 1024 * 1024;\nconst SCHEMA =');s=s.replace('  await AsyncStorage.setItem(key, JSON.stringify({ ...entry, baseUrl }));','''  const serialized = JSON.stringify({ ...entry, baseUrl });
  if (serialized.length * 2 > MAX_BYTES) return;
  await AsyncStorage.setItem(key, serialized);''');s=s.replace('    if (!raw) return null;','    if (!raw) return null;\n    if (raw.length * 2 > MAX_BYTES) { await AsyncStorage.removeItem(key); return null; }');s=s.replace('    return entry;','    if (!Number.isFinite(entry.savedAt) || Date.now() - entry.savedAt > MAX_AGE_MS || entry.savedAt > Date.now()) { await AsyncStorage.removeItem(key); return null; }\n    return entry;');s=s.replace('    if (enabled) {','    if (enabled && settings.offlineEnabled !== false) {');s=s.replace('[enabled, settings.baseUrl, settings.token, settings.cacheScope]','[enabled, settings.baseUrl, settings.token, settings.cacheScope, settings.offlineEnabled]');s+='''
export async function purgeBundle(scope?: string): Promise<void> {
  const key = cacheKey(scope);
  if (key) await AsyncStorage.removeItem(key);
}

export async function purgeAppBundles(): Promise<void> {
  const keys = await AsyncStorage.getAllKeys();
  const owned = keys.filter(key => key.startsWith(CACHE_KEY_PREFIX) || key === 'maverick.offlineBundle.v1');
  if (owned.length) await AsyncStorage.multiRemove(owned);
}
''';p.write_text(s)
p=Path('apps/mobile-companion/src/screens/SettingsScreen.tsx');s=p.read_text().replace('{ Pressable, ScrollView','{ Alert, Switch, Pressable, ScrollView').replace('loadSettings, saveSettings','loadSettings, resetSettings, saveSettings');s=s.replace('  const saveInFlight = useRef(false);','''  const saveInFlight = useRef(false);
  const mounted = useRef(true), generation = useRef(0), testController = useRef<AbortController | null>(null);
  const [offlineEnabled, setOfflineEnabled] = useState(true);
  useEffect(() => () => { mounted.current = false; generation.current += 1; testController.current?.abort(); }, []);
  function edited(update: () => void): void {
    generation.current += 1; testController.current?.abort(); setTesting(false); setNote(""); setError(""); update();
  }
  function disconnect(): void {
    Alert.alert("Remove saved connection?", "This removes this app’s saved access token and offline task data. It does not change the dashboard.", [
      {text: "Cancel", style: "cancel"},
      {text: "Remove connection", style: "destructive", onPress: () => {
        edited(() => {});
        void resetSettings().then(props.onSaved, () => setError("Could not remove saved data. Unlock your device and try again."));
      }},
    ]);
  }''');s=s.replace('      setToken(s.token);','      setToken(s.token);\n      setOfflineEnabled(s.offlineEnabled !== false);');s=s.replace('if (loading || loadFailed || saveInFlight.current)', 'if (loading || loadFailed || testing || saveInFlight.current)');s=s.replace('await saveSettings({ baseUrl: address, token });','await saveSettings({ baseUrl: address, token, offlineEnabled });');s=s.replace('    setTesting(true);','    const request = ++generation.current;\n    const controller = new AbortController(); testController.current = controller;\n    setTesting(true);');s=s.replace('await getActive({ baseUrl: address, token });','await getActive({ baseUrl: address, token }, controller.signal);\n      if (!mounted.current || request !== generation.current) return;');s=s.replace('    } catch (e) {\n      const known', '    } catch (e) {\n      if (!mounted.current || request !== generation.current) return;\n      const known');s=s.replace('      setTesting(false);\n    }','      if (mounted.current && request === generation.current) { setTesting(false); testController.current = null; }\n    }');s=s.replace('onChangeText={setBaseUrl}','onChangeText={value => edited(() => setBaseUrl(value))}').replace('onChangeText={setToken}','onChangeText={value => edited(() => setToken(value))}');s=s.replace('        disabled={disabled} busy={saving}', '        disabled={disabled || testing} busy={saving}');s=s.replace('      <Button label={saving', '''      <Text style={styles.label}>Save offline task data on this device</Text>
      <Switch accessibilityLabel="Save offline task data for up to 24 hours" value={offlineEnabled} disabled={disabled} onValueChange={setOfflineEnabled} />
      <Text style={styles.fieldHelp}>Saved task content can be sensitive. Up to 2 MB is kept for 24 hours; turn this off and save to remove the previous cache.</Text>
      {!firstRun ? <Button label="Disconnect and remove saved data" kind="secondary" disabled={disabled || testing} onPress={disconnect} /> : null}
      <Button label={saving''');p.write_text(s)
p=Path('apps/mobile-companion/README.md');s=p.read_text().replace('8400','8765');s+='''
Offline task data is optional in Settings, is limited to 2 MB and 24 hours, and
is removed on connection replacement or explicit disconnect. A phone's
127.0.0.1 address refers to the phone itself: use the organization's reachable
HTTPS dashboard address (including its configured proxy port), not the desktop's
loopback address. Native streaming and accessibility qualification are still
pending; this source scaffold is not a signed device release.
''';p.write_text(s)
