from pathlib import Path
p=Path('apps/mobile-companion/src/offlineCache.ts');s=p.read_text().replace('import { useEffect, useState }','import { useCallback, useEffect, useState }').replace('import type { OfflineBundle, Settings } from "./api";','import { getOfflineBundle, type OfflineBundle, type Settings } from "./api";\nimport { usePolling } from "./poll";');s+='''
// Owned by the connection, so opening Tasks first also prepares offline data.
export function useConnectionCache(settings: Settings | null) {
  const fetcher = useCallback((signal: AbortSignal) => settings && !settings.needsSetup
    ? getOfflineBundle(settings, signal) : Promise.resolve(null), [settings]);
  const state = usePolling<OfflineBundle | null>(fetcher, 60_000);
  const [saved, setSaved] = useState<{scope?: string; at: number} | null>(null);
  useEffect(() => {
    let current = true;
    if (!settings) return;
    if (state.accessDenied || state.unsupported || settings.offlineEnabled === false) {
      setSaved(null); void purgeBundle(settings.cacheScope);
    } else if (state.data) {
      void saveBundle(state.data, settings.baseUrl, settings.cacheScope).then(() => {
        if (current && settings.cacheScope) setSaved({scope: settings.cacheScope, at: Date.now()});
      }, () => { if (current) setSaved(null); });
    }
    return () => { current = false; };
  }, [state.data, state.accessDenied, state.unsupported, settings]);
  return {...state, savedAt: saved?.scope === settings?.cacheScope ? saved?.at : null};
}
''';p.write_text(s)
p=Path('apps/mobile-companion/App.tsx');s=p.read_text().replace('{ Pressable, SafeAreaView','{ Alert, BackHandler, Pressable, SafeAreaView').replace('import { loadSettings }','import { InvalidSavedConnection, loadSettings, resetSettings }');s=s.replace('import { GlanceScreen }', 'import { useConnectionCache } from "./src/offlineCache";\nimport { GlanceScreen }');s=s.replace('  const [settingsError,', '  const [corrupt, setCorrupt] = useState(false);\n  const [settingsError,');s=s.replace('  const mounted =', '  const cache = useConnectionCache(settings);\n  const mounted =');s=s.replace('    setSettingsError("");','    setSettingsError(""); setCorrupt(false);');s=s.replace('    }, () => {\n      if (!mounted.current', '    }, (error: unknown) => {\n      if (!mounted.current');s=s.replace('      setSettingsError("Could not open', '      if (error instanceof InvalidSavedConnection) { setCorrupt(true); setSettingsError(error.message); return; }\n      setSettingsError("Could not open');s=s.replace('  const openSettings =', '''  useEffect(() => {
    const handler = BackHandler.addEventListener('hardwareBackPress', () => {
      if (run) { setRun(null); setTab('runs'); return true; }
      if (tab !== 'runs') { setTab('runs'); return true; }
      return false;
    });
    return () => handler.remove();
  }, [run, tab]);
  function resetCorrupt(): void {
    Alert.alert('Reset saved connection?', 'Only this app’s saved connection and offline tasks will be removed.', [
      {text: 'Cancel', style: 'cancel'}, {text: 'Reset connection', style: 'destructive', onPress: () => {
        void resetSettings().then(reloadSettings, () => setSettingsError('Could not reset secure storage. Unlock the device and retry.'));
      }},
    ]);
  }
  const openSettings =''');s=s.replace('        <Pressable style={styles.retry}', '        {corrupt ? <Pressable style={styles.retry} accessibilityRole="button" onPress={resetCorrupt}><Text style={styles.retryText}>Reset saved connection</Text></Pressable> : null}\n        <Pressable style={styles.retry}',1);s=s.replace('<GlanceScreen settings={settings} onOpenSettings={openSettings} />','<GlanceScreen settings={settings} liveBundle={cache.data} onOpenRun={(id, title) => { setRun({id, title}); setTab("runs"); }} onOpenSettings={openSettings} />');s=s.replace('      <View style={styles.fill}>{body}</View>', '''      <View style={styles.fill}>{body}</View>
      {settings && !settings.needsSetup && tab !== 'settings' ? <Text style={styles.loading}>
        {settings.offlineEnabled === false ? 'Offline saving is off' : cache.savedAt ? `Offline copy saved ${new Date(cache.savedAt).toLocaleTimeString()}` : 'Offline copy not ready'}
      </Text> : null}''');s=s.replace('tab: { flex: 1,','tab: { minHeight: 48, flex: 1,');p.write_text(s)
p=Path('apps/mobile-companion/src/poll.ts');s=p.read_text().replace('import { NETWORK_TROUBLE', 'import { AppState } from "react-native";\nimport { NETWORK_TROUBLE');s=s.replace('  refresh: () => void;', '  refresh: () => void;\n  updatedAt: number | null;');s=s.replace('    unsupported: boolean;','    unsupported: boolean;\n    updatedAt: number | null;');s=s.replace('unsupported: false }','unsupported: false, updatedAt: null }');s=s.replace('      if (!current) return;', '      if (!current || AppState.currentState === "background" || AppState.currentState === "inactive") return;');s=s.replace('setSnapshot({ fetcher, data, error: null, accessDenied: false, unsupported: false, updatedAt: null });','setSnapshot({ fetcher, data, error: null, accessDenied: false, unsupported: false, updatedAt: Date.now() });');s=s.replace('            unsupported,','            unsupported,\n            updatedAt: !denied && !unsupported ? previous.updatedAt : null,');s=s.replace('    refreshCurrent.current = tick;', '''    const lifecycle = AppState.addEventListener('change', state => {
      if (state === 'active') { tick(); }
      else {
        latestRequest += 1; activeController?.abort(); activeController = null;
        clearTimeout(deadline); pending = false;
      }
    });
    refreshCurrent.current = tick;''');s=s.replace('      current = false;', '      current = false; lifecycle.remove();');s=s.replace('    refresh,\n', '    refresh,\n    updatedAt: matches ? snapshot.updatedAt : null,\n');p.write_text(s)
p=Path('apps/mobile-companion/src/screens/GlanceScreen.tsx');s=p.read_text().replace('useCallback, useEffect','useCallback').replace('{ ScrollView','{ Pressable, ScrollView');s=s.replace('  getOfflineBundle,','  OfflineBundle,').replace('offlineBanner, saveBundle,','offlineBanner,');s=s.replace('props: { settings: Settings; onOpenSettings?: () => void }','props: { settings: Settings; liveBundle?: OfflineBundle | null; onOpenRun?: (id: number, title?: string) => void; onOpenSettings?: () => void }');a=s.index('  // Optional cache refresh');b=s.index('\n  if (!data && cached)',a);s=s[:a]+'''  const cached = useCachedBundle(settings, Boolean(error && !data && !accessDenied && !unsupported));
'''+s[b:];s=s.replace('        <Stat label="Tasks in progress" value={data ? String(data.active.length) : "…"} />','''        <Stat label="Tasks in progress" value={data ? String(data.active.length) : "…"} />
        <Stat label="Waiting for approval" value={props.liveBundle ? String(props.liveBundle.glance.counts.pending_approvals) : "Unavailable"} />
        <Stat label="Questions for you" value={props.liveBundle ? String(props.liveBundle.glance.counts.open_questions) : "Unavailable"} />''');s=s.replace('      <Text style={styles.section} accessibilityRole="header">In progress now</Text>', '''      {props.liveBundle ? <Text style={styles.note}>Attention counts as of {new Date(props.liveBundle.as_of * 1000).toLocaleString()}. Approvals and questions are answered on the dashboard.</Text> : null}
      <Text style={styles.section} accessibilityRole="header">In progress now</Text>''');s=s.replace('<View key={a.id} style={styles.card}>','<Pressable key={a.id} style={styles.card} accessibilityRole="button" accessibilityLabel={`Open ${taskTitle(a.title)}`} onPress={() => props.onOpenRun?.(a.id, a.title)}>');s=s.replace('        </View>\n      ))}', '        </Pressable>\n      ))}');s=s.replace('<Text key={a.id} style={styles.row} numberOfLines={1}>{taskTitle(a.title)}</Text>', '<Pressable key={a.id} style={styles.row} accessibilityRole="button" onPress={() => props.onOpenRun?.(a.id, a.title)}><Text>{taskTitle(a.title)}</Text></Pressable>');p.write_text(s)
p=Path('apps/mobile-companion/src/components/ui.tsx');s=p.read_text().replace('button: { marginTop:', 'button: { minHeight: 48, justifyContent: "center", marginTop:');p.write_text(s)
