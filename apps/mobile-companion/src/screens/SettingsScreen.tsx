/* Settings — dashboard base URL + bearer token, stored in the device
 * keychain via expo-secure-store. The app stays read-only regardless of the
 * token's privileges: it only ever issues GETs. */
import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput } from "react-native";

import { loadSettings, saveSettings } from "../settings";
import { connectionValidationError } from "../connectionValidation";

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
    const validation = connectionValidationError(baseUrl, token);
    if (validation) {
      setError(validation);
      setNote("");
      return;
    }
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
    <ScrollView contentContainerStyle={styles.fill} keyboardShouldPersistTaps="handled">
      <Text style={styles.help}>Connect to your organization's Lightwork dashboard. Ask your administrator for the address and access token.</Text>
      {error ? <Text style={styles.error} accessibilityRole="alert">{error}</Text> : null}
      {loadFailed ? (
        <Pressable style={styles.button} accessibilityRole="button" onPress={() => setLoadAttempt(loadAttempt + 1)}>
          <Text style={styles.buttonText}>Retry loading settings</Text>
        </Pressable>
      ) : null}
      <Text style={styles.label}>Dashboard address</Text>
      <TextInput
        style={styles.input}
        accessibilityLabel="Dashboard address"
        editable={!disabled}
        value={baseUrl}
        onChangeText={setBaseUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        placeholder="https://lightwork.example.com"
      />
      <Text style={styles.label}>Access token</Text>
      <TextInput
        style={styles.input}
        accessibilityLabel="Access token"
        editable={!disabled}
        value={token}
        onChangeText={setToken}
        autoCapitalize="none"
        autoCorrect={false}
        secureTextEntry
        placeholder="Leave empty only if sign-in is not required"
      />
      <Pressable style={[styles.button, disabled && styles.disabled]} accessibilityRole="button"
        accessibilityState={{ disabled, busy: saving }} disabled={disabled} onPress={save}>
        <Text style={styles.buttonText}>{saving ? "Saving…" : loading ? "Loading…" : "Save"}</Text>
      </Pressable>
      {note ? <Text style={styles.note} accessibilityLiveRegion="polite">{note}</Text> : null}
      <Text style={styles.help}>
        You can view runs here. Start, cancel, or approve work in the dashboard.
        Use your organization's network or VPN to connect. Use a secure HTTPS
        address for a remote dashboard. Your token is
        kept in your device's secure storage.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  fill: { flexGrow: 1, padding: 16 },
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
