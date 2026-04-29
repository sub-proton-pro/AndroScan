/**
 * Client for the per-app Behavior Trace cache routes
 * (Phase 10 sub-step 10.6, ``androscan/web/trace_routes.py``).
 *
 * The wire-shape contract is locked in 10.6: GET / POST /anchor return
 * the canonical ``BehaviorAnchor`` JSON shape produced by
 * ``androscan.internal.trace_cache.anchor_to_json`` (i.e.
 * ``dataclasses.asdict(anchor)`` with stable key ordering). 10.7's
 * ``BehaviorAnchorCard`` / ``DecisionTimeline`` / ``BypassPlanCard``
 * will treat the response as the structural truth.
 *
 * The four functions below mirror the four route shapes — kept thin
 * on purpose so per-component callers can compose them without re-
 * implementing the URL layout. Errors are normalised to a discriminated
 * union (``{ ok: true, ... } | { ok: false, error }``) so callers don't
 * have to remember whether they're dealing with a network error vs an
 * HTTP 4xx vs an HTTP 5xx.
 */

export type TraceCacheStatus = {
  status: "missing" | "ready" | "failed";
  schema_version: string | null;
  anchor_count: number | null;
  db_path: string | null;
  error: string | null;
};

export type TraceStatusPayload = {
  app_id: string;
  decompile_status: string;
  call_graph: {
    status: string;
    [k: string]: unknown;
  };
  trace_cache: TraceCacheStatus;
};

export type TraceAnchorRow = {
  entry_smali_id: string;
  hops: number;
  created_at: number;
};

/**
 * The canonical ``BehaviorAnchor`` JSON shape (mirrors
 * ``androscan/analysis/trace_types.py::BehaviorAnchor``).
 *
 * Loose typing on inner fields (``decisions`` / ``plans``) for now —
 * 10.7 will tighten these once ``DecisionTimeline`` / ``BypassPlanCard``
 * start consuming them. The shape is locked at the wire level, so
 * tightening here is purely a TypeScript exercise.
 */
export type BehaviorAnchor = {
  entry_method: {
    class_name: string;
    method_name: string;
    param_descriptors: string[];
    return_descriptor: string;
  };
  hops: number;
  truncated: boolean;
  incomplete: boolean;
  decisions: unknown[];
  plans: unknown[];
  advanced_plans: unknown[];
  rationale: string;
  low_confidence_decision_indices: number[];
};

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; status: number; error: string };

async function _request<T>(input: RequestInfo, init?: RequestInit): Promise<ApiResult<T>> {
  try {
    const r = await fetch(input, init);
    if (!r.ok) {
      // FastAPI 4xx / 5xx bodies are typed as ``{ detail: ... }``;
      // surface the detail string when present so callers can render it
      // inline (e.g. the LabTraceMode placeholder shows "404 — entry
      // not cached" rather than a generic "request failed").
      let detail = `${r.status} ${r.statusText}`;
      try {
        const body = await r.json();
        if (body && typeof body.detail === "string") {
          detail = body.detail;
        } else if (body && typeof body.error === "string") {
          detail = body.error;
        }
      } catch {
        // body wasn't JSON — keep the default detail string.
      }
      return { ok: false, status: r.status, error: detail };
    }
    // The 204 handler (DELETE success) has no body; return a synthetic
    // ``{}`` so callers that don't care about the body don't have to
    // special-case the status code. The TS unsafe-cast here is the
    // single point where we admit that 204 is the ``T = void`` case.
    if (r.status === 204) return { ok: true, data: {} as T };
    const data = (await r.json()) as T;
    return { ok: true, data };
  } catch (e) {
    return { ok: false, status: 0, error: e instanceof Error ? e.message : String(e) };
  }
}

export function fetchTraceStatus(appId: string): Promise<ApiResult<TraceStatusPayload>> {
  return _request<TraceStatusPayload>(`/api/trace/${encodeURIComponent(appId)}/status`);
}

export function listTraceAnchors(
  appId: string,
): Promise<ApiResult<{ app_id: string; anchors: TraceAnchorRow[] }>> {
  return _request(`/api/trace/${encodeURIComponent(appId)}/anchors`);
}

export function fetchTraceAnchor(
  appId: string,
  entrySmaliId: string,
  hops: number,
): Promise<ApiResult<BehaviorAnchor>> {
  const qs = new URLSearchParams({ entry: entrySmaliId, hops: String(hops) });
  return _request<BehaviorAnchor>(
    `/api/trace/${encodeURIComponent(appId)}/anchor?${qs.toString()}`,
  );
}

export function buildTraceAnchor(
  appId: string,
  entrySmaliId: string,
  hops: number,
  force: boolean = false,
): Promise<ApiResult<BehaviorAnchor>> {
  const qs = new URLSearchParams({
    entry: entrySmaliId,
    hops: String(hops),
    force: force ? "true" : "false",
  });
  return _request<BehaviorAnchor>(
    `/api/trace/${encodeURIComponent(appId)}/anchor?${qs.toString()}`,
    { method: "POST" },
  );
}

export function deleteTraceAnchor(
  appId: string,
  entrySmaliId: string,
  hops: number,
): Promise<ApiResult<unknown>> {
  const qs = new URLSearchParams({ entry: entrySmaliId, hops: String(hops) });
  return _request(
    `/api/trace/${encodeURIComponent(appId)}/anchor?${qs.toString()}`,
    { method: "DELETE" },
  );
}
