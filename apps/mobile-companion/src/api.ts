/* Read-only client for the Lightwork dashboard REST API.
 *
 * Every function here is a GET against an endpoint that exists today in
 * packages/maverick-dashboard/maverick_dashboard/api.py:
 *
 *   GET /api/v1/goals?limit=&status=          listGoals()
 *   GET /api/v1/goals/{id}                    getGoal()
 *   GET /api/v1/goals/{id}/events?since=&limit=  getGoalEvents()
 *   GET /api/v1/oversight/active              getActive()
 *   GET /api/v1/spend                         getSpend()
 *   GET /api/v1/offline/bundle                getOfflineBundle()  (optional —
 *       served once the integrator adds the maverick.offline_bundle endpoint;
 *       404 from older dashboards is handled by the caller)
 *
 * This app is READ-ONLY by design: no POST/DELETE/PUT anywhere.
 */

import { connectionValidationError } from "./connectionValidation";

export type Settings = { baseUrl: string; token: string; cacheScope?: string; needsSetup?: boolean; offlineEnabled?: boolean };

export type Goal = {
  id: number;
  status: string;
  title: string;
  description?: string | null;
  result?: string | null;
};

export type GoalEvent = {
  id: number;
  agent: string;
  kind: string;
  content: string;
  ts: number;
};

export type GoalEventsResponse = {
  status: string;
  result: string | null;
  next_id: number;
  events: GoalEvent[];
  has_more: boolean;
  terminal: boolean;
  truncated?: boolean;
};

export type ActiveGoal = {
  id: number;
  title: string;
  status: string;
  updated_at: number;
  activity: string;
};

export type SpendTotal = {
  dollars: number;
  input_tokens: number;
  output_tokens: number;
  runs: number;
};

export type Glance = {
  as_of: number;
  active: { id: number; title: string; status: string; updated_at: number }[];
  counts: { active: number; pending_approvals: number; open_questions: number };
  spend: { dollars: number; runs: number };
};

export type BundleGoal = {
  id: number;
  title: string;
  status: string;
  created_at: number;
  updated_at: number;
  result: string | null;
};

export type OfflinePolicy = {allowed: boolean; max_age_seconds: number; max_bytes: number; revision: string; message?: string};

export type OfflineBundle = {
  cache_policy?: OfflinePolicy;
  schema: string; // "maverick-offline/1"
  as_of: number;
  glance: Glance;
  goals: BundleGoal[];
  recent_events: (GoalEvent & { goal_id: number })[];
};

// Use the same HTTPS/HTTP-loopback policy as connection setup.
export function isTokenTransportSafe(baseUrl: string): boolean {
  return connectionValidationError(baseUrl) === null;
}

// Every message below is shown to people as-is, so it stays plain: no
// addresses, status codes or code names. The status and kind stay on the
// error for program logic.
export const UNSUPPORTED_BUILD = "This version of the app cannot connect safely. Install the latest version of the app, then try again.";
export const NETWORK_TROUBLE = "Could not reach the dashboard. Check that you are on your organization's network or VPN, then try again.";

export function statusMessage(status: number): string {
  if (status === 401) return "The dashboard did not accept your access token. Check it in Settings or ask your administrator for a new one.";
  if (status === 403) return "Your access token does not allow viewing this information. Ask your administrator to check your access.";
  if (status === 404) return "This dashboard does not offer this information yet. Ask your administrator to update the dashboard.";
  if (status === 429) return "The dashboard is busy. Wait a minute, then try again.";
  if (status >= 500) return "The dashboard had a problem answering. Try again in a few minutes.";
  return "The dashboard could not answer this request. Try again, or check your settings.";
}

export class ApiError extends Error {
  constructor(message: string, public status?: number, public kind = "transient") { super(message); }
}

export function isAccessDenied(error: unknown): boolean {
  const status = (error as {status?: number})?.status;
  return status === 401 || status === 403;
}

async function boundedResponseText(res: Response, signal: AbortSignal): Promise<string> {
  const limit = 4 * 1024 * 1024;
  const oversized = () => new ApiError("The dashboard sent more information than this app can show. Open the dashboard on a computer instead.", undefined, "protocol");
  const declared = Number(res.headers?.get("content-length"));
  if (Number.isFinite(declared) && declared > limit) throw oversized();
  if (!res.body?.getReader) {
    throw new ApiError(UNSUPPORTED_BUILD, undefined, "unsupported");
  }
  const reader = res.body.getReader();
  const cancel = () => { void reader.cancel().catch(() => {}); };
  signal.addEventListener("abort", cancel, { once: true });
  const decoder = new TextDecoder();
  let raw = "", bytes = 0;
  try {
    if (signal.aborted) throw new ApiError("Request cancelled.", undefined, "cancelled");
    while (true) {
      const {done, value} = await reader.read();
      if (signal.aborted) throw new ApiError("Request cancelled.", undefined, "cancelled");
      if (done) break;
      bytes += value.byteLength;
      if (bytes > limit) { cancel(); throw oversized(); }
      raw += decoder.decode(value, {stream:true});
    }
    return raw + decoder.decode();
  } finally {
    signal.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
}

async function apiGet<T>(cfg: Settings, path: string, signal?: AbortSignal): Promise<T> {
  const error = connectionValidationError(cfg.baseUrl, cfg.token);
  if (error) throw new ApiError(error, undefined, "settings");
  // Fail before the native bridge fetches/buffers a body when its Fetch API
  // cannot enforce a streaming byte limit. This scaffold must use a supported
  // streaming transport in its eventual packaged native build.
  if (typeof ReadableStream !== "function" || typeof TextDecoder !== "function" ||
      typeof Response !== "function" || !("body" in Response.prototype)) {
    throw new ApiError(UNSUPPORTED_BUILD, undefined, "unsupported");
  }
  const base = cfg.baseUrl.trim().replace(/\/+$/, "");
  const headers: Record<string, string> = { Accept: "application/json" };
  if (path === "/api/v1/offline/bundle") headers["X-Lightwork-Offline-Policy"] = "1";
  if (cfg.token) {
    headers.Authorization = `Bearer ${cfg.token.trim()}`;
  }
  const controller = new AbortController();
  let rejectAbort: (reason?: unknown) => void = () => {};
  const cancelled = new Promise<never>((_, reject) => { rejectAbort = reject; });
  const abort = () => { controller.abort(); rejectAbort(new ApiError("Request cancelled.", undefined, "cancelled")); };
  if (signal?.aborted) abort();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    controller.abort(); rejectAbort(new ApiError("The dashboard took too long to answer. Check your network, then try again."));
  }, 15_000);
  try {
    return await Promise.race([cancelled, (async () => {
      if (signal?.aborted) throw new ApiError("Request cancelled.", undefined, "cancelled");
      const res = await fetch(`${base}${path}`, { method: "GET", headers, signal: controller.signal, redirect: "error" });
      if (!res.ok) throw new ApiError(statusMessage(res.status), res.status,
        res.status === 401 || res.status === 403 ? "access" : "transient");
      const raw = await boundedResponseText(res, controller.signal);
      try {
        return JSON.parse(raw) as T;
      } catch {
        throw new ApiError("The dashboard sent a reply this app could not read. Ask your administrator to check the dashboard address.", undefined, "protocol");
      }
    })()]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function listGoals(cfg: Settings, limit = 50, signal?: AbortSignal, offset = 0, status = ""): Promise<Goal[]> {
  return apiGet<Goal[]>(cfg, `/api/v1/goals?limit=${limit}&offset=${offset}&status=${encodeURIComponent(status)}`, signal);
}

export function searchGoals(cfg: Settings, query: string, signal?: AbortSignal): Promise<Goal[]> {
  return apiGet<Goal[]>(cfg, `/api/v1/goals/search?q=${encodeURIComponent(query)}&limit=200`, signal);
}

export function getGoal(cfg: Settings, id: number, signal?: AbortSignal): Promise<Goal> {
  return apiGet<Goal>(cfg, `/api/v1/goals/${id}`, signal);
}

export function getGoalEvents(
  cfg: Settings, id: number, since = 0, limit = 200, signal?: AbortSignal,
): Promise<GoalEventsResponse> {
  return apiGet<GoalEventsResponse>(
    cfg, `/api/v1/goals/${id}/events?since=${since}&limit=${limit}`, signal,
  );
}

export function getActive(cfg: Settings, signal?: AbortSignal): Promise<{ goals: ActiveGoal[] }> {
  return apiGet<{ goals: ActiveGoal[] }>(cfg, "/api/v1/oversight/active", signal);
}

export function getSpend(cfg: Settings, signal?: AbortSignal): Promise<{ total: SpendTotal }> {
  return apiGet<{ total: SpendTotal }>(cfg, "/api/v1/spend", signal);
}

export function getOfflineBundle(cfg: Settings, signal?: AbortSignal): Promise<OfflineBundle> {
  return apiGet<OfflineBundle>(cfg, "/api/v1/offline/bundle", signal);
}
