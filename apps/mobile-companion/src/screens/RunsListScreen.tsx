import {msg, messageLanguage, useLanguage, formattedTime} from "../language";
/* Tasks list — GET /api/v1/goals (newest first), polled every 10s.
 * Falls back to the cached offline bundle when the dashboard is unreachable. */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { BundleGoal, Goal, Settings, listGoals, searchGoals } from "../api";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { Banner, Button, EmptyState, Loading, StatusPill } from "../components/ui";
import { statusLabel, taskTitle } from "../format";
import { offlineBanner, useCachedBundle } from "../offlineCache";
import { usePolling } from "../poll";
import { useTheme, type Palette } from "../theme";

export function RunsListScreen(props: {
  settings: Settings;
  onOpenRun: (id: number, title?: string) => void;
  onOpenSettings?: () => void;
}): React.JSX.Element {
  useLanguage();
  const theme = useTheme(), styles = stylesFor(theme);
  const { settings, onOpenRun, onOpenSettings } = props;
  const [query, setQuery] = useState(''), [search, setSearch] = useState(''), [status, setStatus] = useState('');
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
  }
  const { data, error, accessDenied, unsupported, refresh, updatedAt } = usePolling<Goal[]>(fetcher, 10_000);
  useEffect(() => {
    if (accessDenied || unsupported) { requestOwner.current += 1; pageRequest.current?.abort(); setOlder([]); }
  }, [accessDenied, unsupported]);
  const cached = useCachedBundle(settings, Boolean(error && !data && !accessDenied && !unsupported));

  const offline = !data && cached;
  const rows: Goal[] = data
    ? Array.from(new Map([...older, ...data].map(goal => [goal.id, goal])).values()).sort((a, b) => b.id - a.id)
    : cached
      ? cached.bundle.goals.map((g: BundleGoal) => ({
          id: g.id, status: g.status, title: g.title, result: g.result,
        }))
      : [];

  let empty: React.JSX.Element | null = null;
  if (!data && !error) empty = <Loading text="Loading tasks…" />;
  else if (data || offline) {
    empty = (
      <EmptyState
        title={search || status ? "No matching tasks" : "No tasks yet"}
        body={search || status ? "Try another search or status. Matching tasks may be outside the saved offline copy." : "Tasks started from the Lightwork dashboard will appear here. This list refreshes on its own."}
        actionLabel={msg("Refresh now")}
        onAction={refresh}
      />
    );
  }

  const header = <>
      <Text style={styles.heading} accessibilityRole="header" accessibilityLanguage={messageLanguage("Tasks")}>{msg("Tasks")}</Text>
      <TextInput style={styles.search} accessibilityLabel={msg("Search tasks")} placeholder="Search all task titles and results" value={query} onChangeText={setQuery} onSubmitEditing={() => setSearch(query.trim())} />
      <View style={styles.filters}>
        <Button label={msg("Search")} language={messageLanguage("Search")} onPress={() => setSearch(query.trim())} />
        <Button label={msg("Clear search")} language={messageLanguage("Clear search")} kind="secondary" onPress={() => {setQuery(''); setSearch('');}} />
        {['', 'active', 'blocked', 'done'].map(value => <Button key={value} label={(status === value ? 'Selected: ' : '') + (value ? statusLabel(value) : 'All statuses')} kind="secondary" onPress={() => setStatus(value)} />)}
      </View>
      {updatedAt ? <Text style={styles.sub}>Updated {formattedTime(updatedAt)}</Text> : null}
      {search ? <Banner text="Search shows up to 200 matches. Refine your search to find a specific task." /> : null}
      {pageError ? <Banner text={pageError} /> : null}
      {offline && cached ? <Banner text={offlineBanner(cached)} /> : null}
      {error ? (
        <ConnectionNotice message={error} accessDenied={accessDenied} unsupported={unsupported}
          onRetry={refresh} onOpenSettings={accessDenied ? onOpenSettings : undefined} />
      ) : null}
      {error && data ? <Banner text="The list below may be out of date." /> : null}
    </>;
  return (
    <View style={styles.fill}>
      <FlatList
        ListHeaderComponent={header}
        data={rows.filter(row => (!status || row.status === status) && (!offline || !search || `${row.title} ${row.result || ''}`.toLowerCase().includes(search.toLowerCase())))}
        keyExtractor={(g: Goal) => String(g.id)}
        ListEmptyComponent={empty}
        ListFooterComponent={!search && data && data.length === 50 && !exhausted ? <Button label={loadingMore ? 'Loading older tasks…' : 'Load older tasks'} disabled={loadingMore} onPress={loadMore} /> : null}
        renderItem={({ item }: { item: Goal }) => (
          <Pressable
            style={styles.row}
            accessibilityRole="button"
            accessibilityLabel={`${taskTitle(item.title)}, ${statusLabel(item.status)}`}
            accessibilityHint="Opens this task's activity"
            onPress={() => onOpenRun(item.id, item.title)}
          >
            <View style={styles.fill}>
              <Text style={styles.title} numberOfLines={2}>{taskTitle(item.title)}</Text>
              <StatusPill status={item.status} />
              {item.result ? <Text style={styles.sub} numberOfLines={1}>{item.result}</Text> : null}
            </View>
            <Text style={styles.chevron} accessibilityElementsHidden importantForAccessibility="no">›</Text>
          </Pressable>
        )}
      />
    </View>
  );
}

const stylesFor = (theme: Palette) => StyleSheet.create({
  fill: { flex: 1, backgroundColor: theme.background },
  filters: {flexDirection: "row", flexWrap: "wrap", gap: 8, paddingHorizontal: 16},
  search: {margin: 12, padding: 12, borderWidth: 1, borderColor: theme.border, color: theme.text, minHeight: 48},
  heading: { fontSize: 22, fontWeight: "700", color: theme.text, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    paddingHorizontal: 16, paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: theme.border,
  },
  title: { fontSize: 15, fontWeight: "600", color: theme.text },
  sub: { fontSize: 13, color: theme.muted, marginTop: 4 },
  chevron: { fontSize: 22, color: theme.muted },
});
