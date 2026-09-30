/* Offline cache for the maverick-offline/1 bundle.
 *
 * The app saves the last successfully fetched bundle to AsyncStorage with a
 * timestamp; when the dashboard is unreachable, screens render the cached
 * bundle behind an "as of N min ago — offline" banner instead of a blank
 * error. The bundle is built server-side by maverick.offline_bundle (bounded,
 * versioned, no credentials). Task content can still be sensitive: people may
 * disable persistence, and saved content expires after 24 hours.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useState } from "react";

import { getOfflineBundle, type OfflineBundle, type Settings } from "./api";
import { usePolling } from "./poll";
import { agoText } from "./format";

const CACHE_KEY_PREFIX = "maverick.offlineBundle.v2.";
let cacheGeneration = 0;
const retiredScopes = new Set<string>();
const MAX_AGE_MS = 24 * 60 * 60 * 1000;
const MAX_BYTES = 2 * 1024 * 1024;
const SCHEMA = "maverick-offline/1";

export type CachedBundle = { savedAt: number; bundle: OfflineBundle };

// This opaque scope is committed with the connection in SecureStore and rotates
// on every save, including token changes on the same host. Neither a bearer
// token nor a token-derived key is written to AsyncStorage. Legacy URL-only
// caches cannot establish credential identity and are deliberately not read.
function cacheKey(scope?: string): string | null {
  return scope && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(scope)
    ? CACHE_KEY_PREFIX + scope : null;
}

export async function saveBundle(bundle: OfflineBundle, baseUrl: string, scope?: string): Promise<boolean> {
  if (bundle.schema !== SCHEMA) return false; // refuse shapes we don't understand
  const key = cacheKey(scope);
  if (!key || retiredScopes.has(key)) return false;
  const generation = cacheGeneration;
  const entry: CachedBundle = { savedAt: Date.now(), bundle };
  const serialized = JSON.stringify({ ...entry, baseUrl });
  if (serialized.length * 2 > MAX_BYTES) return false;
  await AsyncStorage.setItem(key, serialized);
  if (retiredScopes.has(key) || generation !== cacheGeneration) { await AsyncStorage.removeItem(key); return false; }
  return true;
}

export async function loadBundle(baseUrl: string, scope?: string): Promise<CachedBundle | null> {
  const key = cacheKey(scope);
  if (!key) return null;
  try {
    const raw = await AsyncStorage.getItem(key);
    if (!raw) return null;
    if (raw.length * 2 > MAX_BYTES) { await AsyncStorage.removeItem(key); return null; }
    const entry = JSON.parse(raw) as CachedBundle & { baseUrl?: string };
    if (!entry?.bundle || entry.bundle.schema !== SCHEMA || entry.baseUrl !== baseUrl) return null;
    if (!Number.isFinite(entry.savedAt) || Date.now() - entry.savedAt > MAX_AGE_MS || entry.savedAt > Date.now()) { await AsyncStorage.removeItem(key); return null; }
    return entry;
  } catch {
    return null; // corrupt cache reads as "no cache"
  }
}

export function useCachedBundle(settings: Settings, enabled: boolean): CachedBundle | null {
  const [cached, setCached] = useState<{ settings: Settings; entry: CachedBundle | null } | null>(null);
  useEffect(() => {
    let current = true;
    setCached(null);
    if (enabled && settings.offlineEnabled !== false) {
      loadBundle(settings.baseUrl, settings.cacheScope).then(entry => {
        if (current) setCached({ settings, entry });
      });
    }
    return () => { current = false; };
  }, [enabled, settings.baseUrl, settings.token, settings.cacheScope, settings.offlineEnabled]);
  return enabled && cached?.settings.baseUrl === settings.baseUrl
    && cached.settings.token === settings.token && cached.settings.cacheScope === settings.cacheScope
    ? cached.entry : null;
}

export function ageMinutes(entry: CachedBundle, now: number = Date.now()): number {
  return Math.max(0, Math.round((now - entry.savedAt) / 60000));
}

export function offlineBanner(entry: CachedBundle): string {
  return `You're offline. Showing what was saved ${agoText(ageMinutes(entry))}.`;
}

export async function purgeBundle(scope?: string): Promise<void> {
  const key = cacheKey(scope);
  if (key) { retiredScopes.add(key); await AsyncStorage.removeItem(key); }
}

export async function purgeAppBundles(): Promise<void> {
  cacheGeneration += 1;
  const keys = await AsyncStorage.getAllKeys();
  const owned = keys.filter(key => key.startsWith(CACHE_KEY_PREFIX) || key === 'maverick.offlineBundle.v1');
  if (owned.length) await AsyncStorage.multiRemove(owned);
}

// Owned by the connection, so opening Tasks first also prepares offline data.
export function useConnectionCache(settings: Settings | null) {
  const fetcher = useCallback((signal: AbortSignal) => settings && !settings.needsSetup
    ? getOfflineBundle(settings, signal) : Promise.resolve(null), [settings]);
  const state = usePolling<OfflineBundle | null>(fetcher, 60_000);
  const [saved, setSaved] = useState<{scope?: string; at: number} | null>(null);
  useEffect(() => {
    let current = true;
    if (!settings) return;
    if (state.accessDenied || state.unsupported || settings.offlineEnabled === false) {
      setSaved(null); void purgeBundle(settings.cacheScope);
    } else if (state.data) {
      void saveBundle(state.data, settings.baseUrl, settings.cacheScope).then(saved => {
        if (!saved) { if (current) setSaved(null); return; }
        if (current && settings.cacheScope) setSaved({scope: settings.cacheScope, at: Date.now()});
      }, () => { if (current) setSaved(null); });
    }
    return () => { current = false; };
  }, [state.data, state.accessDenied, state.unsupported, settings]);
  return {...state, savedAt: saved?.scope === settings?.cacheScope ? saved?.at : null};
}
