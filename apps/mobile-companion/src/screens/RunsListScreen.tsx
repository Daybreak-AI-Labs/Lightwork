/* Tasks list — GET /api/v1/goals (newest first), polled every 10s.
 * Falls back to the cached offline bundle when the dashboard is unreachable. */
import React, { useCallback } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import { BundleGoal, Goal, Settings, listGoals } from "../api";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { Banner, EmptyState, Loading, StatusPill } from "../components/ui";
import { statusLabel, taskTitle } from "../format";
import { offlineBanner, useCachedBundle } from "../offlineCache";
import { usePolling } from "../poll";
import { theme } from "../theme";

export function RunsListScreen(props: {
  settings: Settings;
  onOpenRun: (id: number, title?: string) => void;
  onOpenSettings?: () => void;
}): React.JSX.Element {
  const { settings, onOpenRun, onOpenSettings } = props;
  const fetcher = useCallback((signal: AbortSignal) => listGoals(settings, 50, signal), [settings]);
  const { data, error, accessDenied, unsupported, refresh } = usePolling<Goal[]>(fetcher, 10_000);
  const cached = useCachedBundle(settings, Boolean(error && !data && !accessDenied && !unsupported));

  const offline = !data && cached;
  const rows: Goal[] = data
    ? data
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
        title="No tasks yet"
        body="Tasks started from the Lightwork dashboard will appear here. This list refreshes on its own."
        actionLabel="Refresh now"
        onAction={refresh}
      />
    );
  }

  return (
    <View style={styles.fill}>
      <Text style={styles.heading} accessibilityRole="header">Tasks</Text>
      {offline && cached ? <Banner text={offlineBanner(cached)} /> : null}
      {error ? (
        <ConnectionNotice message={error} accessDenied={accessDenied} unsupported={unsupported}
          onRetry={refresh} onOpenSettings={accessDenied ? onOpenSettings : undefined} />
      ) : null}
      {error && data ? <Banner text="The list below may be out of date." /> : null}
      <FlatList
        data={rows}
        keyExtractor={(g: Goal) => String(g.id)}
        ListEmptyComponent={empty}
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

const styles = StyleSheet.create({
  fill: { flex: 1 },
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
