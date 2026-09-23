import React, { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

export function ConnectionNotice({ message, accessDenied = false }: { message: string; accessDenied?: boolean }): React.JSX.Element {
  const [expanded, setExpanded] = useState(false);
  return (
    <View style={styles.notice}>
      <Text style={styles.error} accessibilityRole="alert">
        {message.startsWith("Unsupported mobile transport:") ? message :
          accessDenied ? "Access denied. Check your sign-in and permissions. Cached information is hidden." :
          "Could not load the latest information. Check your connection and dashboard settings."}
      </Text>
      <Pressable accessibilityRole="button" accessibilityState={{ expanded }}
        onPress={() => setExpanded(!expanded)}>
        <Text style={styles.link}>{expanded ? "Hide connection details" : "Connection details"}</Text>
      </Pressable>
      {expanded ? <Text style={styles.detail} selectable>{message}</Text> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  notice: { padding: 12 },
  error: { color: "#cf222e", fontSize: 13 },
  link: { color: "#0969da", paddingVertical: 8, fontSize: 13 },
  detail: { color: "#57606a", fontSize: 12 },
});
