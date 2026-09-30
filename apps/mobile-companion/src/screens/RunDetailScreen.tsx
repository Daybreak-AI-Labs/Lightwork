/* Task detail — status header + activity timeline.
 * GET /api/v1/goals/{id}/events?since=0&limit=200, polled every 5s.
 * Read-only: there is no cancel/resume/answer here by design. */
import React, { useCallback, useMemo } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import { GoalEvent, GoalEventsResponse, Settings } from "../api";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { Banner, EmptyState, Loading, StatusPill } from "../components/ui";
import { humanize, taskTitle } from "../format";
import { usePolling } from "../poll";
import { runReplay } from "../runReplay";
import { theme } from "../theme";

const continueReplay = (data: GoalEventsResponse) => !data.terminal;

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

export function RunDetailScreen(props: {
  settings: Settings;
  runId: number;
  title?: string;
  onBack: () => void;
  onOpenSettings?: () => void;
}): React.JSX.Element {
  const { settings, runId, title, onBack, onOpenSettings } = props;
  const replay = useMemo(() => runReplay(settings, runId), [settings, runId]);
  const fetcher = useCallback((signal: AbortSignal) => replay.poll(signal), [replay]);
  const { data, error, accessDenied, unsupported, refresh } = usePolling<GoalEventsResponse>(fetcher, 5_000, continueReplay);

  return (
    <View style={styles.fill}>
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12} accessibilityRole="button" accessibilityLabel="Back to tasks">
          <Text style={styles.back}>‹ Tasks</Text>
        </Pressable>
      </View>
      <View style={styles.titleBlock}>
        <Text style={styles.headline} accessibilityRole="header">{title ? taskTitle(title) : "Task details"}</Text>
        {data ? <StatusPill status={data.status} /> : null}
      </View>
      {data?.result ? (
        <View style={styles.result}>
          <Text style={styles.resultLabel}>Outcome</Text>
          <Text style={styles.resultText}>{data.result}</Text>
        </View>
      ) : null}
      {data?.has_more ? <Banner text="Loading earlier activity…" /> : null}
      {data?.truncated ? <Banner text="Showing recent activity only. Open the task on the dashboard to see its full history." /> : null}
      {error ? (
        <ConnectionNotice message={error} accessDenied={accessDenied} unsupported={unsupported}
          onRetry={refresh} onOpenSettings={accessDenied ? onOpenSettings : undefined} />
      ) : null}
      <Text style={styles.section} accessibilityRole="header">Activity</Text>
      <FlatList
        data={data ? [...data.events].reverse() : []}
        keyExtractor={(e: GoalEvent) => String(e.id)}
        ListEmptyComponent={!data && !error ? <Loading text="Loading activity…" /> : (
          <EmptyState title="No activity yet" body="Steps appear here as the work moves along. This page refreshes on its own." />
        )}
        renderItem={({ item }: { item: GoalEvent }) => (
          <View style={styles.event}>
            <View style={styles.spine} />
            <View style={styles.fill}>
              <Text style={styles.meta}>
                {clock(item.ts)} · {humanize(item.agent) || "Lightwork"} · {humanize(item.kind)}
              </Text>
              <Text style={styles.content} numberOfLines={6}>
                {item.content}
              </Text>
            </View>
          </View>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: {
    paddingHorizontal: 16, paddingTop: 12,
  },
  back: { fontSize: 16, color: theme.accent },
  titleBlock: {
    paddingHorizontal: 16, paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: theme.border,
  },
  headline: { fontSize: 20, fontWeight: "700", color: theme.text },
  result: { margin: 12, padding: 12, borderRadius: 8, backgroundColor: "#ddf4ff" },
  resultLabel: { fontSize: 13, fontWeight: "700", color: theme.text },
  resultText: { fontSize: 14, color: theme.text, marginTop: 4 },
  section: { fontSize: 15, fontWeight: "700", color: theme.text, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  event: { flexDirection: "row", paddingHorizontal: 14, paddingVertical: 8 },
  spine: {
    width: 3, borderRadius: 2, backgroundColor: theme.border, marginRight: 10,
  },
  meta: { fontSize: 12, color: theme.muted },
  content: { fontSize: 14, marginTop: 2, color: theme.text },
});
