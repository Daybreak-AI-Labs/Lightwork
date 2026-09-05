/* Dashboard connection settings, stored in the device keychain via
 * expo-secure-store (the token never touches AsyncStorage or logs). */
import * as SecureStore from "expo-secure-store";

import type { Settings } from "./api";
import { connectionValidationError } from "./connectionValidation";

const BASE_URL_KEY = "maverick.baseUrl";
const TOKEN_KEY = "maverick.token";
const CONNECTION_KEY = "maverick.connection.v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function newCacheScope(): string | undefined {
  // Expo SDK 52's core UUID helper uses this native binding. Browser builds
  // can use Web Crypto. Without either, keep connection settings usable but
  // disable offline persistence instead of inventing a weak credential key.
  const runtime = globalThis as typeof globalThis & {
    expo?: { uuidv4?: () => string };
    crypto?: { randomUUID?: () => string };
  };
  try {
    const scope = runtime.expo?.uuidv4?.() ?? runtime.crypto?.randomUUID?.();
    return typeof scope === "string" && UUID.test(scope) ? scope : undefined;
  } catch {
    return undefined;
  }
}

// Serialize this app's reads and writes, including across Settings remounts.
// A rejected keychain operation must not prevent a later retry from running.
let pending: Promise<void> = Promise.resolve();
function inOrder<T>(operation: () => Promise<T>): Promise<T> {
  const result = pending.then(operation);
  pending = result.then(() => undefined, () => undefined);
  return result;
}

export const DEFAULT_BASE_URL = "http://127.0.0.1:8400";

export function loadSettings(): Promise<Settings> {
  return inOrder(async () => {
    const record = await SecureStore.getItemAsync(CONNECTION_KEY);
    if (record !== null) {
      let value: unknown;
      try {
        value = JSON.parse(record);
      } catch {
        throw new Error("Saved connection settings are invalid.");
      }
      if (typeof value !== "object" || value === null
          || !("baseUrl" in value) || typeof value.baseUrl !== "string"
          || !("token" in value) || typeof value.token !== "string") {
        throw new Error("Saved connection settings are invalid.");
      }
      const cacheScope = "cacheScope" in value && typeof value.cacheScope === "string"
        && UUID.test(value.cacheScope) ? value.cacheScope : undefined;
      return { baseUrl: value.baseUrl, token: value.token, cacheScope };
    }
    // Legacy installs are read without a migration write. The next successful
    // save creates the atomic record; failure leaves the old pair untouched.
    const baseUrl = (await SecureStore.getItemAsync(BASE_URL_KEY)) ?? DEFAULT_BASE_URL;
    const token = (await SecureStore.getItemAsync(TOKEN_KEY)) ?? "";
    return { baseUrl, token };
  });
}

export function saveSettings(s: Settings): Promise<void> {
  // One keychain write commits the URL and bearer token together. Never leave
  // a new dashboard URL paired with credentials from the previous dashboard.
  const connection = { baseUrl: s.baseUrl.trim(), token: s.token.trim() };
  return inOrder(() => {
    const error = connectionValidationError(connection.baseUrl, connection.token);
    if (error) throw new Error(error);
    return SecureStore.setItemAsync(CONNECTION_KEY, JSON.stringify({
      ...connection,
      cacheScope: newCacheScope(),
    }));
  });
}
