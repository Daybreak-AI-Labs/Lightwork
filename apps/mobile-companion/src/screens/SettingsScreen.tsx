/* Settings — dashboard address + access token, stored in the device
 * keychain via expo-secure-store. The app stays read-only regardless of the
 * token's privileges: it only ever issues GETs (including "Test connection"). */
import React, { useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";

import { getActive } from "../api";
import { Button } from "../components/ui";
import { loadSettings, saveSettings } from "../settings";
import { connectionValidationError } from "../connectionValidation";
import { theme } from "../theme";

// People often type "lightwork.example.com"; assume the secure scheme.
export function withScheme(address: string): string {
  const trimmed = address.trim();
  return trimmed && !/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? `https://${trimmed}` : trimmed;
}

export function SettingsScreen(props: {
  onSaved: () => void;
  firstRun?: boolean;
}): React.JSX.Element {
  const { firstRun = false } = props;
  const [baseUrl, setBaseUrl] = useState("");
  const [token, setToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const saveInFlight = useRef(false);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setLoadFailed(false);
    setError("");
    loadSettings().then((s) => {
      if (!current) return;
      // A fresh install has nothing saved: leave the address empty so the
      // example shows instead of a placeholder address.
      setBaseUrl(s.needsSetup ? "" : s.baseUrl);
      setToken(s.token);
    }, () => {
      if (!current) return;
      setLoadFailed(true);
      setError("Could not open your saved settings. Unlock your device, then try again.");
    }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [loadAttempt]);

  function checked(): string | null {
    const address = withScheme(baseUrl);
    if (address !== baseUrl) setBaseUrl(address);
    const validation = address ? connectionValidationError(address, token) : "Enter your dashboard address.";
    if (validation) {
      setError(validation);
      setNote("");
      return null;
    }
    return address;
  }

  async function save(): Promise<void> {
    if (loading || loadFailed || saveInFlight.current) return;
    const address = checked();
    if (address === null) return;
    saveInFlight.current = true;
    setSaving(true);
    setError("");
    setNote("");
    try {
      await saveSettings({ baseUrl: address, token });
      setNote("Saved. The app now uses this connection.");
      props.onSaved();
    } catch {
      setError("Could not save your settings. Check that your device has free space, then try again.");
    } finally {
      saveInFlight.current = false;
      setSaving(false);
    }
  }

  async function testConnection(): Promise<void> {
    if (loading || loadFailed || testing) return;
    const address = checked();
    if (address === null) return;
    setTesting(true);
    setError("");
    setNote("");
    try {
      await getActive({ baseUrl: address, token });
      setNote("Connected. Save to start using this connection.");
    } catch (e) {
      const known = e instanceof Error && typeof (e as { kind?: unknown }).kind === "string";
      setError(known ? (e as Error).message
        : "Could not reach the dashboard. Check the address and that you are on your organization's network or VPN.");
    } finally {
      setTesting(false);
    }
  }

  const disabled = loading || loadFailed || saving;

  return (
    <ScrollView contentContainerStyle={styles.fill} keyboardShouldPersistTaps="handled">
      <Text style={styles.heading} accessibilityRole="header">{firstRun ? "Connect to Lightwork" : "Settings"}</Text>
      <Text style={styles.help}>
        {firstRun
          ? "This app shows your organization's Lightwork tasks on your phone. To start, enter two things your administrator can give you."
          : "The dashboard this app shows information from."}
      </Text>
      {error ? <Text style={styles.error} accessibilityRole="alert">{error}</Text> : null}
      {loadFailed ? (
        <Button label="Retry loading settings" onPress={() => setLoadAttempt(loadAttempt + 1)} />
      ) : null}
      <Text style={styles.label} nativeID="address-label">Dashboard address</Text>
      <TextInput
        style={styles.input}
        accessibilityLabel="Dashboard address"
        accessibilityHint="The web address you use to open Lightwork on a computer"
        editable={!disabled}
        value={baseUrl}
        onChangeText={setBaseUrl}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        placeholder="https://lightwork.yourcompany.com"
      />
      <Text style={styles.fieldHelp}>The web address you use to open Lightwork on a computer.</Text>
      <Text style={styles.label}>Access token</Text>
      <View style={styles.tokenRow}>
        <TextInput
          style={[styles.input, styles.tokenInput]}
          accessibilityLabel="Access token"
          accessibilityHint="A private code from your administrator"
          editable={!disabled}
          value={token}
          onChangeText={setToken}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry={!showToken}
          placeholder="Paste the code from your administrator"
        />
        <Pressable style={styles.show} accessibilityRole="button"
          accessibilityLabel={showToken ? "Hide access token" : "Show access token"}
          onPress={() => setShowToken(!showToken)}>
          <Text style={styles.showText}>{showToken ? "Hide" : "Show"}</Text>
        </Pressable>
      </View>
      <Text style={styles.fieldHelp}>A private code from your administrator. Leave it empty only if your administrator says sign-in is not needed.</Text>
      <Button label={saving ? "Saving…" : loading ? "Loading…" : firstRun ? "Save and connect" : "Save"}
        disabled={disabled} busy={saving} onPress={save} />
      <Button label={testing ? "Testing…" : "Test connection"} kind="secondary"
        disabled={disabled || testing} busy={testing} onPress={testConnection}
        hint="Checks the address and token without saving them" />
      {note ? <Text style={styles.note} accessibilityLiveRegion="polite">{note}</Text> : null}
      <Text style={styles.sectionHelp}>Good to know</Text>
      <Text style={styles.help}>
        This app only shows information. To start, stop or approve work, use the
        dashboard on a computer. If you are away from the office, connect to your
        organization's network or VPN first. Your access token is kept in your
        device's secure storage.
      </Text>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  fill: { flexGrow: 1, padding: 16 },
  heading: { fontSize: 22, fontWeight: "700", color: theme.text },
  label: { fontSize: 14, fontWeight: "600", color: theme.text, marginTop: 16, marginBottom: 4 },
  fieldHelp: { fontSize: 12, color: theme.muted, marginTop: 4, lineHeight: 17 },
  input: {
    borderWidth: 1, borderColor: theme.border, borderRadius: 6,
    paddingHorizontal: 10, paddingVertical: 8, fontSize: 15, color: theme.text,
  },
  tokenRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  tokenInput: { flex: 1 },
  show: { paddingHorizontal: 10, paddingVertical: 8 },
  showText: { color: theme.accent, fontSize: 14, fontWeight: "600" },
  note: { marginTop: 10, color: theme.success },
  error: { marginTop: 10, color: theme.danger },
  sectionHelp: { marginTop: 24, fontSize: 14, fontWeight: "600", color: theme.text },
  help: { marginTop: 6, fontSize: 13, color: theme.muted, lineHeight: 19 },
});
