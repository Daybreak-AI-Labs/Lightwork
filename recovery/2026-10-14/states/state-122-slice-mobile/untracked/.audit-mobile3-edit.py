from pathlib import Path
p=Path('apps/mobile-companion/src/screens/RunsListScreen.tsx');s=p.read_text().replace('useCallback }','useCallback, useEffect, useRef, useState }').replace('Text, View','Text, TextInput, View').replace('Settings, listGoals','Settings, listGoals, searchGoals').replace('Banner, EmptyState','Banner, Button, EmptyState');s=s.replace('  const fetcher = useCallback((signal: AbortSignal) => listGoals(settings, 50, signal), [settings]);','''  const [query, setQuery] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState('');
  const [older, setOlder] = useState<Goal[]>([]), [loadingMore, setLoadingMore] = useState(false), [exhausted, setExhausted] = useState(false);
  const [pageError, setPageError] = useState('');
  const requestOwner = useRef(0), pageRequest = useRef<AbortController | null>(null);
  const fetcher = useCallback((signal: AbortSignal) => search ? searchGoals(settings, search, signal) : listGoals(settings, 50, signal, 0, status), [settings, search, status]);
  useEffect(() => {
    requestOwner.current += 1; pageRequest.current?.abort(); setOlder([]); setExhausted(false); setLoadingMore(false); setPageError('');
    return () => { requestOwner.current += 1; pageRequest.current?.abort(); };
  }, [fetcher]);
  async function loadMore(): Promise<void> {
    if (loadingMore || search || !data) return;
    const owner = requestOwner.current, controller = new AbortController(); pageRequest.current = controller;
    setLoadingMore(true); setPageError('');
    try {
      const page = await listGoals(settings, 50, controller.signal, data.length + older.length, status);
      if (owner !== requestOwner.current) return;
      setOlder(previous => Array.from(new Map([...previous, ...page].map(goal => [goal.id, goal])).values()));
      setExhausted(page.length < 50);
    } catch { if (owner === requestOwner.current) setPageError('Could not load older tasks. Try Load older tasks again.'); }
    finally { if (owner === requestOwner.current) setLoadingMore(false); }
  }''');s=s.replace('const { data, error, accessDenied, unsupported, refresh }','const { data, error, accessDenied, unsupported, refresh, updatedAt }');s=s.replace('    ? data\n', '    ? Array.from(new Map([...older, ...data].map(goal => [goal.id, goal])).values()).sort((a, b) => b.id - a.id)\n');s=s.replace('title="No tasks yet"','title={search || status ? "No matching tasks" : "No tasks yet"}');s=s.replace('body="Tasks started from the Lightwork dashboard will appear here. This list refreshes on its own."','body={search || status ? "Try another search or status. Matching tasks may be outside the saved offline copy." : "Tasks started from the Lightwork dashboard will appear here. This list refreshes on its own."}');s=s.replace('      {offline && cached ?', '''      <TextInput style={styles.search} accessibilityLabel="Search tasks" placeholder="Search all task titles and results" value={query} onChangeText={setQuery} onSubmitEditing={() => setSearch(query.trim())} />
      <View style={styles.filters}>
        <Button label="Search" onPress={() => setSearch(query.trim())} />
        <Button label="Clear search" kind="secondary" onPress={() => {setQuery(''); setSearch('');}} />
        {['', 'active', 'blocked', 'done'].map(value => <Button key={value} label={(status === value ? 'Selected: ' : '') + (value || 'All statuses')} kind="secondary" onPress={() => setStatus(value)} />)}
      </View>
      {updatedAt ? <Text style={styles.sub}>Updated {new Date(updatedAt).toLocaleTimeString()}</Text> : null}
      {search ? <Banner text="Search shows up to 200 matches. Refine your search to find a specific task." /> : null}
      {pageError ? <Banner text={pageError} /> : null}
      {offline && cached ?''');s=s.replace('        data={rows}','        data={status ? rows.filter(row => row.status === status) : rows}');s=s.replace('        ListEmptyComponent={empty}','''        ListEmptyComponent={empty}
        ListFooterComponent={!search && data && data.length === 50 && !exhausted ? <Button label={loadingMore ? 'Loading older tasks…' : 'Load older tasks'} disabled={loadingMore} onPress={loadMore} /> : null}''');s=s.replace('  fill: { flex: 1 },','  fill: { flex: 1 },\n  filters: {flexDirection: "row", flexWrap: "wrap", gap: 8, paddingHorizontal: 16},\n  search: {margin: 12, padding: 12, borderWidth: 1, borderColor: theme.border, color: theme.text, minHeight: 48},');p.write_text(s)
p=Path('apps/mobile-companion/src/screens/RunDetailScreen.tsx');s=p.read_text().replace('useCallback, useMemo','useCallback, useMemo, useState').replace('FlatList, Pressable','FlatList, Linking, Pressable').replace('Banner, EmptyState','Banner, Button, EmptyState').replace('import { usePolling }', 'import { offlineBanner, useCachedBundle } from "../offlineCache";\nimport { usePolling }');s=s.replace('  return (\n', '''  const cached = useCachedBundle(settings, Boolean(error && !data && !accessDenied && !unsupported));
  const savedGoal = cached?.bundle.goals.find(goal => goal.id === runId);
  const display = data || (savedGoal && cached ? {
    status: savedGoal.status, result: savedGoal.result, events: cached.bundle.recent_events.filter(event => event.goal_id === runId),
    has_more: false, truncated: true,
  } : null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  function openDashboard(): void {
    // Credentials are never put in a URL; the browser must authenticate itself.
    const url = settings.baseUrl.replace(/\\/+$/, '') + '/chat/goal/' + encodeURIComponent(runId);
    void Linking.openURL(url).catch(() => undefined);
  }

  const header = <>
''',1)
a=s.index('    <View style={styles.fill}>\n',s.index('  const header'));s=s[:a]+s[a:].replace('    <View style={styles.fill}>\n','',1)
a=s.index('      <FlatList');head=s[:a].replace('{data ?', '{display ?').replace('status={data.status}', 'status={display.status}').replace('data?.result','display?.result').replace('{data.result}','{display.result}').replace('data?.has_more','display?.has_more').replace('data?.truncated','display?.truncated');s=head+'''      {cached && !data ? <Banner text={offlineBanner(cached)} /> : null}
      <Button label="Open full task on dashboard" kind="secondary" onPress={openDashboard} />
    </>;
  return (
    <View style={styles.fill}>
'''+s[a:];s=s.replace('        data={data ? [...data.events].reverse() : []}','        ListHeaderComponent={header}\n        data={display ? [...display.events].reverse() : []}');s=s.replace('<Text style={styles.resultText}>','<Text selectable style={styles.resultText}>');s=s.replace('<Text style={styles.content} numberOfLines={6}>','<Text selectable style={styles.content} numberOfLines={expanded[item.id] ? undefined : 6}>');s=s.replace('                {item.content}\n              </Text>','''                {item.content}
              </Text>
              <Button label={expanded[item.id] ? 'Collapse activity' : 'Read full activity'} kind="secondary" onPress={() => setExpanded(previous => ({...previous, [item.id]: !previous[item.id]}))} />''');p.write_text(s)
