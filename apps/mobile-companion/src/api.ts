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

export type Settings = { baseUrl: string; token: string; cacheScope?: string };

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

export type OfflineBundle = {
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

export class ApiError extends Error {
  constructor(message: string, public status?: number, public kind = "transient") { super(message); }
}

export function isAccessDenied(error: unknown): boolean {
  const status = (error as {status?: number})?.status;
  return status === 401 || status === 403;
}

async function boundedResponseText(res: Response, signal: AbortSignal): Promise<string> {
  const limit = 4 * 1024 * 1024;
  const oversized = () => new ApiError("Dashboard response exceeds the client limit.", undefined, "protocol");
  const declared = Number(res.headers?.get("content-length"));
  if (Number.isFinite(declared) && declared > limit) throw oversized();
  if (!res.body?.getReader) {
    throw new ApiError("Unsupported mobile transport: this build needs a streaming response reader. Update the app build before connecting.", undefined, "unsupported");
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
  if (error) throw new Error(error);
  // Fail before the native bridge fetches/buffers a body when its Fetch API
  // cannot enforce a streaming byte limit. This scaffold must use a supported
  // streaming transport in its eventual packaged native build.
  if (typeof ReadableStream !== "function" || typeof TextDecoder !== "function" ||
      typeof Response !== "function" || !("body" in Response.prototype)) {
    throw new ApiError("Unsupported mobile transport: this build needs a streaming response reader. Update the app build before connecting.", undefined, "unsupported");
  }
  const base = cfg.baseUrl.trim().replace(/\/+$/, "");
  const headers: Record<string, string> = { Accept: "application/json" };
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
    controller.abort(); rejectAbort(new ApiError("Dashboard request timed out."));
  }, 15_000);
  try {
    return await Promise.race([cancelled, (async () => {
      if (signal?.aborted) throw new ApiError("Request cancelled.", undefined, "cancelled");
      const res = await fetch(`${base}${path}`, { method: "GET", headers, signal: controller.signal, redirect: "error" });
      if (!res.ok) throw new ApiError(`GET ${path} -> HTTP ${res.status}`, res.status,
        res.status === 401 || res.status === 403 ? "access" : "transient");
      const raw = await boundedResponseText(res, controller.signal);
      return JSON.parse(raw) as T;
    })()]);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
  }
}

export function listGoals(cfg: Settings, limit = 50, signal?: AbortSignal): Promise<Goal[]> {
  return apiGet<Goal[]>(cfg, `/api/v1/goals?limit=${limit}`, signal);
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
