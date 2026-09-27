/* Overview (was "Glance") — the at-a-glance summary, polled every 15s.
 *
 * Online path composes two real read-only endpoints:
 *   GET /api/v1/oversight/active  (active runs + latest activity)
 *   GET /api/v1/spend             (total spend)
 * It also opportunistically refreshes the offline cache from
 * GET /api/v1/offline/bundle when the dashboard serves it (404 on older
 * dashboards is fine — the cache just isn't refreshed). When everything is
 * unreachable, renders the cached bundle with an offline banner. */
import React, { useCallback, useEffect } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import {
  ActiveGoal,
  Glance,
  Settings,
  SpendTotal,
  getActive,
  getOfflineBundle,
  getSpend,
  isAccessDenied,
} from "../api";
import { offlineBanner, saveBundle, useCachedBundle } from "../offlineCache";
import { ConnectionNotice } from "../components/ConnectionNotice";
import { Banner, EmptyState } from "../components/ui";
import { activityText, money, plural, taskTitle } from "../format";
import { usePolling } from "../poll";
import { theme } from "../theme";

type GlanceData = { active: ActiveGoal[]; spend: SpendTotal | null };

export function GlanceScreen(props: { settings: Settings; onOpenSettings?: () => void }): React.JSX.Element {
  const { settings, onOpenSettings } = props;

  const fetcher = useCallback(async (signal: AbortSignal): Promise<GlanceData> => {
    const [activeRes, spendRes] = await Promise.all([
      getActive(settings, signal),
      getSpend(settings, signal).catch(error => { if (isAccessDenied(error)) throw error; return null; }),
    ]);
    return { active: activeRes.goals, spend: spendRes ? spendRes.total : null };
  }, [settings]);

  const { data, error, accessDenied, unsupported, refresh } = usePolling<GlanceData>(fetcher, 15_000);
  // Optional cache refresh has its own lifecycle and deadline; it neither
  // holds the glance response nor outlives the connection generation.
  const bundleFetcher = useCallback((signal: AbortSignal) => getOfflineBundle(settings, signal), [settings]);
  const bundle = usePolling(bundleFetcher, 60_000);
  useEffect(() => {
    if (bundle.data) void saveBundle(bundle.data, settings.baseUrl, settings.cacheScope).catch(() => undefined);
  }, [bundle.data, settings]);

  const cached = useCachedBundle(settings, Boolean(error && !data && !accessDenied && !unsupported && !bundle.accessDenied && !bundle.unsupported));

  if (!data && cached) {
    const g = cached.bundle.glance;
    return (
      <ScrollView style={styles.fill}>
        <Text style={styles.heading} accessibilityRole="header">Overview</Text>
        <Banner text={offlineBanner(cached)} />
        <View style={styles.stats}>
          <Stat label="Tasks in progress" value={String(g.counts.active)} />
          <Stat label="Waiting for approval" value={String(g.counts.pending_approvals)} />
          <Stat label="Questions for you" value={String(g.counts.open_questions)} />
          <Stat label="Total spend" value={money(g.spend.dollars)} />
        </View>
        <Text style={styles.section} accessibilityRole="header">In progress (saved)</Text>
        {g.active.length === 0 ? <Text style={styles.row}>Nothing was running.</Text> : null}
        {g.active.map((a: Glance["active"][number]) => (
          <Text key={a.id} style={styles.row} numberOfLines={1}>{taskTitle(a.title)}</Text>
        ))}
        <Text style={styles.note}>Approvals and questions are answered on the dashboard.</Text>
      </ScrollView>
    );
  }

  return (
    <ScrollView style={styles.fill}>
      <Text style={styles.heading} accessibilityRole="header">Overview</Text>
      {error ? (
        <ConnectionNotice message={error} accessDenied={accessDenied} unsupported={unsupported}
          onRetry={refresh} onOpenSettings={accessDenied ? onOpenSettings : undefined} />
      ) : null}
      {error && data ? <Banner text="These numbers may be out of date." /> : null}
      <View style={styles.stats}>
        <Stat label="Tasks in progress" value={data ? String(data.active.length) : "…"} />
        <Stat
          label="Total spend"
          value={data?.spend ? money(data.spend.dollars) : "…"}
          detail={data?.spend ? `Across ${plural(data.spend.runs, "task", "tasks")}` : undefined}
        />
      </View>
      <Text style={styles.section} accessibilityRole="header">In progress now</Text>
      {data && data.active.length === 0 ? (
        <EmptyState
          title="Nothing is running"
          body="When a task starts on the dashboard, you'll see it here with its latest step."
          actionLabel="Refresh now"
          onAction={refresh}
        />
      ) : null}
      {(data ? data.active : []).map((a) => (
        <View key={a.id} style={styles.card}>
          <Text style={styles.cardTitle} numberOfLines={2}>{taskTitle(a.title)}</Text>
          {a.activity ? (
            <Text style={styles.cardSub} numberOfLines={2}>Latest step: {activityText(a.activity)}</Text>
          ) : null}
        </View>
      ))}
    </ScrollView>
  );
}

function Stat(props: { label: string; value: string; detail?: string }): React.JSX.Element {
  return (
    <View style={styles.stat} accessible accessibilityLabel={`${props.label}: ${props.value}${props.detail ? `, ${props.detail}` : ""}`}>
      <Text style={styles.statValue}>{props.value}</Text>
      <Text style={styles.statLabel}>{props.label}</Text>
      {props.detail ? <Text style={styles.statLabel}>{props.detail}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  heading: { fontSize: 22, fontWeight: "700", color: theme.text, paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  stats: { flexDirection: "row", flexWrap: "wrap", paddingHorizontal: 8 },
  stat: {
    width: "46%", margin: "2%", padding: 12, borderRadius: 8,
    backgroundColor: theme.surface, borderWidth: StyleSheet.hairlineWidth, borderColor: theme.border,
  },
  statValue: { fontSize: 22, fontWeight: "700", color: theme.text },
  statLabel: { fontSize: 13, color: theme.muted },
  section: {
    fontSize: 15, fontWeight: "700", color: theme.text,
    paddingHorizontal: 16, paddingTop: 16, paddingBottom: 4,
  },
  row: { paddingHorizontal: 16, paddingVertical: 6, fontSize: 14, color: theme.text },
  note: { paddingHorizontal: 16, paddingVertical: 12, fontSize: 13, color: theme.muted },
  card: {
    marginHorizontal: 14, marginVertical: 6, padding: 12,
    borderRadius: 8, borderWidth: StyleSheet.hairlineWidth,
    borderColor: theme.border,
  },
  cardTitle: { fontSize: 15, fontWeight: "600", color: theme.text },
  cardSub: { fontSize: 13, color: theme.muted, marginTop: 4 },
});
