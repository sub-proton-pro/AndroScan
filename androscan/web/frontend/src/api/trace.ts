/**
 * Client for the per-app Behavior Trace cache routes
 * (Phase 10 sub-step 10.6, ``androscan/web/trace_routes.py``).
 *
 * The wire-shape contract is locked in 10.6: GET / POST /anchor return
 * the canonical ``BehaviorAnchor`` JSON shape produced by
 * ``androscan.internal.trace_cache.anchor_to_json`` (i.e.
 * ``dataclasses.asdict(anchor)`` with stable key ordering). 10.7's
 * ``BehaviorAnchorCard`` / ``DecisionTimeline`` / ``BypassPlanCard``
 * consume that shape via the typed surface below.
 *
 * The four pure functions mirror the four route shapes — kept thin
 * on purpose so per-component callers can compose them without re-
 * implementing the URL layout. Errors are normalised to a discriminated
 * union (``{ ok: true, ... } | { ok: false, error }``) so callers don't
 * have to remember whether they're dealing with a network error vs an
 * HTTP 4xx vs an HTTP 5xx.
 *
 * 10.7 adds the typed ``DecisionPoint`` / ``BypassPlan`` /
 * ``PredicateOrigin`` shapes plus the ``useTraceAnchor`` React hook
 * that owns the GET-then-fall-back-to-POST lifecycle for the Trace
 * mode UI.
 */
import { useCallback, useEffect, useRef, useState } from "react";

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

/** Phase 11 sub-step 11.3 — one row in ``GET /anchored-methods``'s
 *  response. Each row represents a ``(class_smali, method_name)``
 *  pair that has been touched by at least one cached anchor's
 *  decision closure; the ``(hops, created_at)`` carry the most-recent
 *  trace's metadata for the operator's tooltip. */
export type AnchoredMethod = {
  /** Smali class descriptor, e.g. ``Lcom/example/Foo;``. Joined
   *  against the call-graph ``GraphClass.class_name`` (Java form)
   *  client-side via the same ``hitKey`` helper the Frida hits
   *  overlay uses — see ``CallGraphView``. */
  class_smali: string;
  method_name: string;
  hops: number;
  created_at: number;
};

export type AnchoredMethodsResponse = {
  app_id: string;
  sha: string;
  methods: AnchoredMethod[];
  total: number;
  /** Single-line summary of per-row payload-decode failures
   *  (typically ``null``). When non-null, the overlay still
   *  renders the methods we *could* read; the field exists so the
   *  consumer can show an operator-readable banner. */
  error: string | null;
};

// ---------------------------------------------------------------------------
// Typed mirrors of the Python data model (androscan/analysis/trace_types.py).
// Field names match ``dataclasses.asdict`` output verbatim — the wire format
// is locked by ``trace_cache.anchor_to_json`` (sort_keys=True).

export type MethodRef = {
  class_name: string;
  method_name: string;
  param_descriptors: string[];
  return_descriptor: string;
};

export type FieldRef = {
  class_name: string;
  field_name: string;
  type_descriptor: string;
};

/**
 * Discriminated union for predicate origin (10.2's slicer output). The
 * ``kind`` discriminator is JSON-stable per the Python encoder.
 *
 * Phase 11 sub-step 11.6 / DEC-025 — ``descent_depth`` is a new
 * optional field on the two non-terminal-in-v1 variants
 * (``method_call`` / ``field_read``). ``0`` (the default; equivalent
 * to omitted) means the slicer terminated at this origin without
 * descending — either the v1 path, or v2 with descent disabled / not
 * triggered (deny-list / cycle / external callee / cross-class
 * field). ``>= 1`` means the v2 inter-procedural slicer descended
 * N hops before hitting a cap-stop terminal at this origin. The
 * frontend's depth pill on ``PredicateOriginView`` renders
 * ``"via N helper method(s)"`` / ``"via N field write(s)"`` next
 * to the origin tag when the field is present and ``> 0``.
 * ``Const`` / ``Param`` / ``Composite`` variants stay v1-shaped
 * (no depth field) per Q1 (A) of the 11.6 planning checkpoint.
 */
export type PredicateOrigin =
  | { kind: "method_call"; method: MethodRef; invoke_kind: string; descent_depth?: number }
  | { kind: "field_read"; field: FieldRef; is_static: boolean; descent_depth?: number }
  | { kind: "const"; value: string; smali_op: string }
  | { kind: "param"; register: string }
  | { kind: "composite"; reason: string };

export type Branch = {
  label: string;
  /** ``null`` means fall-through to the next instruction. */
  target_label: string | null;
};

export type BranchVerdict = {
  branch_label: string;
  /** ``"deny" | "allow" | "neutral"`` per 10.3's classifier contract. */
  verdict: string;
  /** Signed score; negative = deny pressure, positive = allow pressure. */
  score: number;
  reasons: string[];
};

export type BranchOutcome = {
  verdicts: BranchVerdict[];
  /** ``[0.0, 1.0]``; gates with confidence < 0.6 were flagged for LLM
   *  re-classification by the 10.5 ``trace_behavior`` skill. */
  confidence: number;
  reasons: string[];
};

export type DecisionKind =
  | "if_eq" | "if_ne" | "if_lt" | "if_le" | "if_gt" | "if_ge"
  | "if_eqz" | "if_nez" | "if_ltz" | "if_lez" | "if_gtz" | "if_gez"
  | "packed_switch" | "sparse_switch";

export type DecisionPoint = {
  method: MethodRef;
  instruction_index: number;
  source_line: number | null;
  kind: DecisionKind;
  predicate_registers: string[];
  branches: Branch[];
  predicate_origin: PredicateOrigin | null;
  branch_outcome: BranchOutcome | null;
};

export type BypassPlan = {
  template_id: string;
  params: Record<string, string>;
  rationale: string;
  /** ``"low" | "medium" | "high"`` per 10.4's locked taxonomy. */
  risk: string;
  risks: string[];
  target_method: MethodRef | null;
  source_decision_method: MethodRef | null;
  source_decision_instruction_index: number | null;
};

/**
 * The canonical ``BehaviorAnchor`` JSON shape (mirrors
 * ``androscan/analysis/trace_types.py::BehaviorAnchor``). Locked field
 * names per ``dataclasses.asdict``; new optional fields are additive.
 */
export type BehaviorAnchor = {
  entry_method: MethodRef;
  hops: number;
  truncated: boolean;
  incomplete: boolean;
  decisions: DecisionPoint[];
  plans: BypassPlan[];
  advanced_plans: BypassPlan[];
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

/** Phase 11 sub-step 11.3 — fetch the anchored-methods set the
 *  call-graph overlay layer in Manual Hooks mode renders ⚓ glyphs
 *  for. 404 when no ``trace.sqlite`` exists yet (operator hasn't
 *  built any traces); the consumer treats both 404 and 200+empty
 *  as "no overlay, no glyphs". */
export function listAnchoredMethods(
  appId: string,
): Promise<ApiResult<AnchoredMethodsResponse>> {
  return _request<AnchoredMethodsResponse>(
    `/api/trace/${encodeURIComponent(appId)}/anchored-methods`,
  );
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

/** Phase 11 v2.1 sub-step v2.1.2 — response shape for the
 *  ``POST /api/trace/{app_id}/normalise-entry`` coalescer endpoint.
 *
 *  Translates the operator's typed input (dotted Java method, partial
 *  Smali, stack-trace line) into a canonical Smali method-prefix
 *  (``Lcom/example/Foo;->onClick(``) AND validates the underlying
 *  class against the call graph in the same round-trip — the
 *  validation signal is what makes Trace mode's ✓ / ⚠ pill
 *  meaningful.
 *
 *  Wire-shape contract pinned by ``tests/test_trace_routes.py::
 *  test_normalise_entry_*`` (Phase 11 v2.1.2). Any field-level change
 *  ripples through both the route and the consuming
 *  ``LabTraceMode``'s pill renderer.
 *
 *  ``error`` is non-null only on the 200-but-the-coalescer-flagged-
 *  something rare path — un-parseable inputs land as 422 with the
 *  detail string carried via ``ApiResult<>.error`` (the
 *  ``_request`` helper unwraps FastAPI's ``{detail: ...}`` shape).
 *  In v2.1.2's response shape ``error`` is reserved for future
 *  use; currently the field is always ``null`` on a 200. */
export type NormaliseEntryResponse = {
  /** Canonical Smali entry — full signature when the input was
   *  already a full sig, method-prefix (``...->name(``) when only
   *  class+method was given, bare class descriptor when no method
   *  was supplied. ``null`` only on the un-parseable path (which
   *  surfaces as 422; the ``error`` field is the operator-readable
   *  reason then). */
  normalised_entry: string | null;
  /** The bare Smali class descriptor extracted from the input
   *  (``Lcom/example/Foo;``). Used by the v2.1.3 "Find similar
   *  classes" button as the fuzzy-match seed. */
  smali_class: string | null;
  /** ``true`` iff the call-graph store has at least one non-external
   *  method node on this class. ``false`` is the v2.1.3 entry-point
   *  signal — the ⚠ pill renders, and the operator can ask for
   *  fuzzy-match suggestions. */
  class_exists_in_graph: boolean;
  /** Number of non-external method nodes on the class. Matches what
   *  the MethodPicker would surface on the same class. ``0`` when
   *  ``class_exists_in_graph`` is ``false``. */
  method_count: number;
  /** Reserved for future use (currently always ``null`` on a 200).
   *  v2.1.2 422 errors land via ``ApiResult.error`` instead. */
  error: string | null;
};

/** Translate + validate the operator's typed Trace-mode entry input.
 *  Returns the coalescer's structured response on a 200; on 422
 *  (un-parseable input) the error string carries the operator-
 *  readable parse-failure reason; on 404 / 409 / network error the
 *  caller is responsible for surfacing "validation unavailable" copy
 *  to the operator (the validation pill renders ⚠ in those cases
 *  rather than ✓ / ✗). */
export function normaliseTraceEntry(
  appId: string,
  entry: string,
): Promise<ApiResult<NormaliseEntryResponse>> {
  return _request<NormaliseEntryResponse>(
    `/api/trace/${encodeURIComponent(appId)}/normalise-entry`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entry }),
    },
  );
}

// ---------------------------------------------------------------------------
// useTraceAnchor — React hook owning the GET → fall-back-to-POST lifecycle
// for one ``(appId, entry, hops)`` triple. The Trace mode UI is the only
// caller; the cached-anchors picker and the form submit funnel through
// ``setTarget`` and ``build``.
//
// State machine (idle → loading → loaded | error → ...):
//
//   idle       — no entry/appId supplied yet (form empty)
//   loading    — GET in flight (cache hit attempt)
//   loaded     — anchor in state, surfaced for rendering
//   missing    — GET returned 404 ("not cached"); operator clicks
//                Build to fire POST
//   building   — POST in flight (skill invocation)
//   error      — GET / POST returned a non-404 error, surfaced as a
//                retryable inline card
//
// Re-firing rule: changing ``(appId, entry, hops)`` always cancels the
// in-flight request and re-runs the cache GET. The ``ts`` bump on
// ``setTarget`` (similar to ``pendingCodeNav.ts``) lets external callers
// (the cached-anchors picker writing the same triple back) force a re-
// load without changing identity.

export type TraceAnchorState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "loaded"; anchor: BehaviorAnchor; from: "cache" | "build" }
  | { kind: "missing" }
  | { kind: "building" }
  | { kind: "error"; status: number; error: string; phase: "get" | "build" };

export type UseTraceAnchorReturn = {
  state: TraceAnchorState;
  /** Operator-clicked Build; fires POST against the current target. */
  build: (force?: boolean) => Promise<void>;
  /** Operator-clicked Clear; resets to idle without changing target. */
  clear: () => void;
};

export function useTraceAnchor(
  appId: string | null,
  entry: string | null,
  hops: number,
): UseTraceAnchorReturn {
  const [state, setState] = useState<TraceAnchorState>({ kind: "idle" });

  // Track the in-flight request key so a stale response that arrives
  // after the operator changed the target doesn't clobber the new state.
  // Mirrors HookBuilder's renderInflightKeyRef pattern.
  const keyRef = useRef<string>("");

  useEffect(() => {
    if (!appId || !entry) {
      setState({ kind: "idle" });
      keyRef.current = "";
      return;
    }
    const myKey = `${appId}\u0001${entry}\u0001${hops}`;
    keyRef.current = myKey;
    setState({ kind: "loading" });
    let cancelled = false;
    void (async () => {
      const r = await fetchTraceAnchor(appId, entry, hops);
      if (cancelled || keyRef.current !== myKey) return;
      if (r.ok) {
        setState({ kind: "loaded", anchor: r.data, from: "cache" });
      } else if (r.status === 404) {
        setState({ kind: "missing" });
      } else {
        setState({ kind: "error", status: r.status, error: r.error, phase: "get" });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, entry, hops]);

  const build = useCallback(
    async (force: boolean = false) => {
      if (!appId || !entry) return;
      const myKey = `${appId}\u0001${entry}\u0001${hops}`;
      keyRef.current = myKey;
      setState({ kind: "building" });
      const r = await buildTraceAnchor(appId, entry, hops, force);
      if (keyRef.current !== myKey) return;
      if (r.ok) {
        setState({ kind: "loaded", anchor: r.data, from: "build" });
      } else {
        setState({
          kind: "error",
          status: r.status,
          error: r.error,
          phase: "build",
        });
      }
    },
    [appId, entry, hops],
  );

  const clear = useCallback(() => {
    keyRef.current = "";
    setState({ kind: "idle" });
  }, []);

  return { state, build, clear };
}
