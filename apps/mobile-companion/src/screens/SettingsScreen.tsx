/* Settings — dashboard base URL + bearer token, stored in the device
 * keychain via expo-secure-store. The app stays read-only regardless of the
 * token's privileges: it only ever issues GETs. */
import React, { useEffect, useRef, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { loadSettings, saveSettings } from "../settings";

export function SettingsScreen(props: {
  onSaved: () => void;
}): React.JSX.Element {
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const saveInFlight = useRef(false);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadFailed(false);
    setError("");
    loadSettings().then((s) => {
      if (!current) return;
      setBaseUrl(s.baseUrl);
      setToken(s.token);
    }, () => {
      if (!current) return;
      setLoadFailed(true);
      setError("Could not load saved settings. Unlock your device and try again.");
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [loadAttempt]);

  async function save(): Promise<void> {
    if (loading || loadFailed || saveInFlight.current) return;
    saveInFlight.current = true;
    setSaving(true);
    setError("");
    setNote("");
    try {
      await saveSettings({ baseUrl, token });
      setNote("Saved.");
      props.onSaved();
    } catch {
      setError("Could not save settings. Check your device storage and try again.");
    } finally {
      saveInFlight.current = false;
      setSaving(false);
    }
  }

  const disabled = loading || loadFailed || saving;

  return (
    <View style={styles.fill}>
      {error ? <Text style={styles.error} accessibilityRole="alert">{error}</Text> : null}
      {loadFailed ? (
        <Pressable style={styles.button} accessibilityRole="button" onPress={() => setLoadAttempt(loadAttempt + 1)}>
          <Text style={styles.buttonText}>Retry loading settings</Text>
        </Pressable>
      ) : null}
      <Text style={styles.label}>Dashboard base URL</Text>
      <TextInput
        style={styles.input}
        accessibilityLabel="Dashboard base URL"
        editable={!disabled}
        value={baseUrl}
        onChangeText={setBaseUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        placeholder="http://192.168.1.20:8400"
      />
      <Text style={styles.label}>API token (Authorization: Bearer …)</Text>
      <TextInput
        style={styles.input}
        accessibilityLabel="API token"
        editable={!disabled}
        value={token}
        onChangeText={setToken}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
        placeholder="leave empty if dashboard auth is off"
      />
      <Pressable style={[styles.button, disabled && styles.disabled]} accessibilityRole="button"
        accessibilityState={{ disabled, busy: saving }} disabled={disabled} onPress={save}>
        <Text style={styles.buttonText}>{saving ? "Saving…" : loading ? "Loading…" : "Save"}</Text>
      </Pressable>
      {note ? <Text style={styles.note} accessibilityLiveRegion="polite">{note}</Text> : null}
      <Text style={styles.help}>
        This app is read-only: it watches runs and never starts, cancels, or
        approves anything. The phone must be able to reach the dashboard
        (same network, VPN, or tunnel). The token is kept in the device
        keychain (expo-secure-store).
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, padding: 16 },
  label: { fontSize: 13, color: "#57606a", marginTop: 14, marginBottom: 4 },
  input: {
    borderWidth: 1, borderColor: "#d0d7de", borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 8, fontSize: 15,
  },
  button: {
    marginTop: 20, backgroundColor: "#1f6feb", borderRadius: 6,
    paddingVertical: 10, alignItems: "center",
  },
  buttonText: { color: "#ffffff", fontSize: 15, fontWeight: "600" },
  note: { marginTop: 10, color: "#2da44e" },
  error: { marginTop: 10, color: "#cf222e" },
  disabled: { opacity: 0.55 },
  help: { marginTop: 24, fontSize: 13, color: "#57606a", lineHeight: 19 },
});
