/* Lightwork mobile companion — read-only oversight from a phone.
 *
 * Three tabs (Tasks, Overview, Settings) plus a task-detail drill-down, and
 * a first-run connection setup when nothing is saved yet. Plain
 * state-based navigation: no navigation library, so the dependency surface
 * stays react/react-native/expo only. All data comes from the dashboard's
 * existing read-only REST endpoints (see src/api.ts); the app never mutates.
 */
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";

import type { Settings } from "./src/api";
import { loadSettings } from "./src/settings";
import { GlanceScreen } from "./src/screens/GlanceScreen";
import { RunDetailScreen } from "./src/screens/RunDetailScreen";
import { RunsListScreen } from "./src/screens/RunsListScreen";
import { SettingsScreen } from "./src/screens/SettingsScreen";

type Tab = "runs" | "glance" | "settings";
const TABS: { id: Tab; label: string }[] = [
  { id: "runs", label: "Tasks" },
  { id: "glance", label: "Overview" },
  { id: "settings", label: "Settings" },
];

export default function App(): React.JSX.Element {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsError, setSettingsError] = useState("");
  const [tab, setTab] = useState<Tab>("runs");
  const [run, setRun] = useState<{ id: number; title?: string } | null>(null);
  const mounted = useRef(false);
  const settingsRequest = useRef(0);

  const reloadSettings = useCallback(() => {
    if (!mounted.current) return;
    const request = ++settingsRequest.current;
    setSettingsError("");
    loadSettings().then((loaded) => {
      if (!mounted.current || request !== settingsRequest.current) return;
      setSettings(loaded);
    }, () => {
      if (!mounted.current || request !== settingsRequest.current) return;
      setSettingsError("Could not open your connection settings. Unlock your device, then try again.");
    });
  }, []);
  useEffect(() => {
    mounted.current = true;
    reloadSettings();
    return () => {
      mounted.current = false;
      settingsRequest.current += 1;
    };
  }, [reloadSettings]);

  const openSettings = useCallback(() => { setTab("settings"); setRun(null); }, []);
  const firstRun = Boolean(settings?.needsSetup);

  let body: React.JSX.Element;
  if (!settings || settingsError) {
    body = settingsError ? (
      <View style={styles.center}>
        <Text style={styles.error} accessibilityRole="alert">{settingsError}</Text>
        <Pressable style={styles.retry} accessibilityRole="button" onPress={reloadSettings}>
          <Text style={styles.retryText}>Retry loading settings</Text>
        </Pressable>
      </View>
    ) : <Text style={styles.loading} accessibilityLiveRegion="polite">Loading…</Text>;
  } else if (firstRun) {
    body = <SettingsScreen firstRun onSaved={reloadSettings} />;
  } else if (tab === "runs" && run !== null) {
    body = (
      <RunDetailScreen
        settings={settings}
        runId={run.id}
        title={run.title}
        onBack={() => setRun(null)}
        onOpenSettings={openSettings}
      />
    );
  } else if (tab === "runs") {
    body = <RunsListScreen settings={settings} onOpenRun={(id, title) => setRun({ id, title })} onOpenSettings={openSettings} />;
  } else if (tab === "glance") {
    body = <GlanceScreen settings={settings} onOpenSettings={openSettings} />;
  } else {
    body = <SettingsScreen onSaved={reloadSettings} />;
  }

  return (
    <SafeAreaView style={styles.fill}>
      <StatusBar style="auto" />
      <View style={styles.fill}>{body}</View>
      {settings && !settingsError && !firstRun ? (
        <View style={styles.tabbar} accessibilityRole="tablist">
          {TABS.map((t) => (
            <Pressable
              key={t.id}
              style={styles.tab}
              accessibilityRole="tab"
              accessibilityState={{ selected: tab === t.id }}
              onPress={() => {
                setTab(t.id);
                setRun(null);
              }}
            >
              <Text style={[styles.tabText, tab === t.id && styles.tabActive]}>{t.label}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  center: { padding: 24, alignItems: "center" },
  loading: { padding: 24, textAlign: "center", color: "#57606a" },
  error: { textAlign: "center", color: "#cf222e", fontSize: 15 },
  retry: { marginTop: 16, backgroundColor: "#0969da", borderRadius: 6, paddingVertical: 10, paddingHorizontal: 16 },
  retryText: { color: "#ffffff", fontSize: 15, fontWeight: "600" },
  tabbar: {
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: "#d0d7de",
  },
  tab: { flex: 1, alignItems: "center", paddingVertical: 12 },
  tabText: { fontSize: 14, color: "#57606a" },
  tabActive: { color: "#0969da", fontWeight: "700" },
});
