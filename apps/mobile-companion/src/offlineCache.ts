/* Offline cache for the maverick-offline/1 bundle.
 *
 * The app saves the last successfully fetched bundle to AsyncStorage with a
 * timestamp; when the dashboard is unreachable, screens render the cached
 * bundle behind an "as of N min ago — offline" banner instead of a blank
 * error. The bundle is built server-side by maverick.offline_bundle (bounded,
 * versioned, no secrets), so caching it on-device is safe by construction.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import { useEffect, useState } from "react";

import type { OfflineBundle, Settings } from "./api";

const CACHE_KEY_PREFIX = "maverick.offlineBundle.v2.";
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

export async function saveBundle(bundle: OfflineBundle, baseUrl: string, scope?: string): Promise<void> {
  if (bundle.schema !== SCHEMA) return; // refuse shapes we don't understand
  const key = cacheKey(scope);
  if (!key) return;
  const entry: CachedBundle = { savedAt: Date.now(), bundle };
  await AsyncStorage.setItem(key, JSON.stringify({ ...entry, baseUrl }));
}

export async function loadBundle(baseUrl: string, scope?: string): Promise<CachedBundle | null> {
  const key = cacheKey(scope);
  if (!key) return null;
  try {
    const raw = await AsyncStorage.getItem(key);
    if (!raw) return null;
    const entry = JSON.parse(raw) as CachedBundle & { baseUrl?: string };
    if (!entry?.bundle || entry.bundle.schema !== SCHEMA || entry.baseUrl !== baseUrl) return null;
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
    if (enabled) {
      loadBundle(settings.baseUrl, settings.cacheScope).then(entry => {
        if (current) setCached({ settings, entry });
      });
    }
    return () => { current = false; };
  }, [enabled, settings.baseUrl, settings.token, settings.cacheScope]);
  return enabled && cached?.settings.baseUrl === settings.baseUrl
    && cached.settings.token === settings.token && cached.settings.cacheScope === settings.cacheScope
    ? cached.entry : null;
}

export function ageMinutes(entry: CachedBundle, now: number = Date.now()): number {
  return Math.max(0, Math.round((now - entry.savedAt) / 60000));
}

export function offlineBanner(entry: CachedBundle): string {
  return `as of ${ageMinutes(entry)} min ago — offline`;
}
