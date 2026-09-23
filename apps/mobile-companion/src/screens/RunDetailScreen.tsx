/* Run detail — status header + event timeline.
 * GET /api/v1/goals/{id}/events?since=0&limit=200, polled every 5s.
 * Read-only: there is no cancel/resume/answer here by design. */
import React, { useCallback, useMemo } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";

import { GoalEvent, GoalEventsResponse, Settings } from "../api";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { usePolling } from "../poll";
import { runReplay } from "../runReplay";

const continueReplay = (data: GoalEventsResponse) => !data.terminal;

function clock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

export function RunDetailScreen(props: {
  settings: Settings;
  runId: number;
  onBack: () => void;
}): React.JSX.Element {
  const { settings, runId, onBack } = props;
  const replay = useMemo(() => runReplay(settings, runId), [settings, runId]);
  const fetcher = useCallback((signal: AbortSignal) => replay.poll(signal), [replay]);
  const { data, error, accessDenied } = usePolling<GoalEventsResponse>(fetcher, 5_000, continueReplay);

  return (
    <View style={styles.fill}>
      <View style={styles.header}>
        <Pressable onPress={onBack} hitSlop={12}>
          <Text style={styles.back}>‹ Runs</Text>
        </Pressable>
        <Text style={styles.headline}>
          Run #{runId} — {data ? data.status : "loading…"}
        </Text>
      </View>
      {data?.result ? <Text style={styles.result}>{data.result}</Text> : null}
      {data?.has_more ? <Text>Loading remaining events…</Text> : null}
      {data?.truncated ? <Text>Showing bounded recent history. Open the dashboard for full history.</Text> : null}
      {error ? <ConnectionNotice message={error} accessDenied={accessDenied} /> : null}
      <FlatList
        data={data ? [...data.events].reverse() : []}
        keyExtractor={(e: GoalEvent) => String(e.id)}
        ListEmptyComponent={<Text style={styles.empty}>No events yet.</Text>}
        renderItem={({ item }: { item: GoalEvent }) => (
          <View style={styles.event}>
            <View style={styles.spine} />
            <View style={styles.fill}>
              <Text style={styles.meta}>
                {clock(item.ts)} · {item.agent} · {item.kind}
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
    flexDirection: "row", alignItems: "center", gap: 12,
    padding: 14, borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#d0d7de",
  },
  back: { fontSize: 16, color: "#0969da" },
  headline: { fontSize: 16, fontWeight: "600", flexShrink: 1 },
  result: { padding: 12, backgroundColor: "#ddf4ff", fontSize: 14 },
  empty: { padding: 24, textAlign: "center", color: "#57606a" },
  event: { flexDirection: "row", paddingHorizontal: 14, paddingVertical: 8 },
  spine: {
    width: 3, borderRadius: 2, backgroundColor: "#d0d7de", marginRight: 10,
  },
  meta: { fontSize: 12, color: "#57606a" },
  content: { fontSize: 14, marginTop: 2 },
});
