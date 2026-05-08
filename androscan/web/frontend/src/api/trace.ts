/**
 * Client for the per-app Behavior Trace cache routes
 * (Phase 10 sub-step 10.6, ``androscan/web/trace_routes.py``).
 *
 * The wire-shape contract is locked in 10.6: GET / POST /anchor return
 * the canonical ``BehaviorAnchor`` JSON shape produced by
 * ``androscan.internal.trace_cache.anchor_to_json`` (i.e.
 * ``dataclasses.asdict(anchor)`` with stable key ordering). 10.7's
 * ``BehaviorAnchorCard`` / ``BehaviorTrace`` (legacy: ``DecisionTimeline``) / ``BypassPlanCard``
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

/** Phase 11 v2.1 sub-step v2.1.3 — one fuzzy / LLM suggestion
 *  candidate returned by ``POST /api/trace/{app_id}/suggest-similar-classes``.
 *
 *  Class-level (not method-level) — the operator's typed input
 *  matched on simple class name; the candidate seeds a class-prefix
 *  (``Lcom/example/MainActivity;->``) that activates the existing
 *  v1 MethodPicker for the explicit Trace step.
 *
 *  ``confidence`` is a ``[0.0, 1.0]`` ratio:
 *  * fuzzy source — :func:`difflib.SequenceMatcher.ratio` between
 *    the typed simple-name and the candidate's simple-name.
 *  * llm_fallback source (v2.1.5+) — the LLM's own ranking score,
 *    normalised to the same scale. */
export type SimilarClassCandidate = {
  /** Bare Smali class descriptor (e.g. ``Lcom/example/MainActivity;``).
   *  Frontend builds the seed prefix by appending ``->`` to this
   *  before writing to ``entryDraft`` (matches what
   *  ``javaRelPathToSmaliMethodPrefix(rel_path, null)`` produces
   *  for a class-only seed from the Browse-tree click path). */
  smali_class: string;
  /** Last class-name segment (e.g. ``MainActivity``); rendered as
   *  the bold lead in the candidate pill so the operator sees the
   *  typo-corrected name at a glance without parsing the full
   *  Smali descriptor. */
  simple_name: string;
  /** Dotted package (e.g. ``com.example``); rendered as the muted
   *  trailing context in the candidate pill so the operator can
   *  disambiguate same-named classes across packages
   *  (``com.foo.MainActivity`` vs ``com.bar.MainActivity``). */
  package: string;
  /** One-line operator-readable explanation of why this candidate
   *  was suggested. v2.1.3: ``"fuzzy match on simple class name
   *  (similarity 0.92)"``; v2.1.5: an LLM-emitted phrase. */
  rationale: string;
  /** ``[0.0, 1.0]``; higher is more confident. Used to sort
   *  candidates and to render an opacity / colour cue on the pill. */
  confidence: number;
};

/** Phase 11 v2.1 sub-step v2.1.3 response shape for
 *  ``POST /api/trace/{app_id}/suggest-similar-classes``.
 *
 *  ``source`` is currently always ``"fuzzy"`` (v2.1.3 ships fuzzy-
 *  only); v2.1.5 will add ``"llm_fallback"`` as a second source
 *  value when the ``suggest_trace_entry`` skill backstops a
 *  no-fuzzy-match case. The frontend currently doesn't render the
 *  source badge but exposing it on the type lets future UI surface
 *  the distinction (e.g. "🤖 LLM-suggested" pill modifier). */
export type SimilarClassesResponse = {
  candidates: SimilarClassCandidate[];
  total: number;
  /** ``"fuzzy"`` (v2.1.3) or ``"llm_fallback"`` (v2.1.5+). */
  source: string;
  /** Reserved for future non-blocking warnings; currently always
   *  ``null`` on a 200. */
  error: string | null;
};

/** Fetch fuzzy / LLM suggestion candidates for the operator's typed
 *  input. v2.1.3: fuzzy-only via :func:`difflib.SequenceMatcher`
 *  against the call graph's class list; v2.1.5: LLM fallback added.
 *
 *  Triggered on the explicit "Find similar classes" button click
 *  (the v2.1.2 ⚠ validation pill grew the button when the input
 *  parsed cleanly but didn't match any class in the call graph) —
 *  *not* automatically on the debounce window; this is a deliberate
 *  operator action so the network round-trip is fine. */
export function suggestSimilarClasses(
  appId: string,
  entry: string,
): Promise<ApiResult<SimilarClassesResponse>> {
  return _request<SimilarClassesResponse>(
    `/api/trace/${encodeURIComponent(appId)}/suggest-similar-classes`,
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

// ---------------------------------------------------------------------------
// Phase 13 sub-step 13.8 — dynamic-trace surface
// ---------------------------------------------------------------------------
//
// Wraps the Phase 13.2 ``POST /api/trace/{app_id}/dynamic`` start +
// Phase 13.2 ``DELETE /api/trace/{app_id}/dynamic/{session_id}`` stop
// REST endpoints, plus the Phase 13.3 ``WS /ws/trace/{app_id}/{session_id}``
// multiplexed event channel that streams Frida trace events
// (``send`` envelope + ``payload.phase`` of ``"entry" | "exit" |
// "ready" | "hook_failed" | "error"``) alongside LLM summary events
// (``summary_pending`` / ``summary_ready`` / ``summary_failed`` —
// these top-level ``kind``s are the multiplexer's invention; Frida
// itself only emits ``send`` / ``error`` / ``log``).
//
// Wire-shape gotchas the BE explorer report flagged that this hook
// has to handle:
//
//   1. The ``ws_url`` field on the start response still points at
//      ``/ws/frida/{session_id}`` (the legacy pre-13.3 path); we
//      derive the multiplexed URL ``/ws/trace/{app_id}/{session_id}``
//      ourselves from the response's ``session_id`` + the caller-
//      supplied ``app_id``. The 13.10 docs sweep may rewrite the
//      backend's ``ws_url`` to the new shape; until then the FE owns
//      the URL.
//   2. ``kind`` is ``"send"`` (Frida transport) for trace events; the
//      operator-facing ``"entry"`` / ``"exit"`` / etc. lives in
//      ``payload.phase``. The hook normalises this into a discriminated
//      ``DynamicTraceMessage`` union so consumers can ``switch`` on
//      one field instead of remembering the ``kind`` + ``phase``
//      double-discriminator.
//   3. ``summary_ready`` carries its text as ``payload.summary``
//      (NOT ``payload.text`` — easy mis-spell that 13.4's tests pin
//      down on the BE side).
//   4. ``entry`` events use ``payload.thread_depth`` (NOT ``depth``)
//      and ``payload.seq`` (NOT ``entry_seq``); ``entry_seq`` lives
//      on ``exit`` / ``error`` events as the back-reference.
//   5. ``exit`` events use the literal JSON key ``"return"`` for the
//      return value — TS reserved-word, so we read via bracket
//      access.
//   6. ``payload.class`` is Java-dotted (``com.example.Foo``); the
//      ``ExecutionFlow`` node ids are Smali (``Lcom/example/Foo;->...``).
//      :func:`smaliKeyFromEvent` does the conversion.
//
// State machine:
//
//   idle → starting → running → stopping → stopped
//                            ↘ disconnected (WS dropped while running)
//                            ↘ error (POST failed / WS failed to open)
//
// The hook owns its WebSocket lifecycle — caller writes ``start()`` /
// ``stop()`` and reads ``state``. Disconnect is observed via
// ``WebSocket.onclose``; the hook DOESN'T auto-reconnect (the BE's
// ring-buffer replay means a fresh ``start()`` would spin up a new
// session, which isn't what the operator wants — they wanted the
// trace to continue, not restart). 13.9 may revisit if dogfooding
// shows reconnect demand.

export type StartDynamicTraceRequest = {
  /** Smali signature of the entry method (must match a cached anchor
   *  built via 10.6 / 10.7's ``trace_behavior`` skill — the start
   *  endpoint reads the closure from the cache, doesn't re-build). */
  entry: string;
  /** Anchor hops to look up. Defaults to ``3`` server-side; the
   *  ``LabTraceMode`` consumer always passes the active anchor's
   *  hops so the lookup is exact. */
  hops?: number;
  /** Hard cap on methods to hook — server clamps to ``[1, 500]``;
   *  default ``50``. 13.9 will color-code the run-trace button by
   *  ``hop_cap`` thresholds (≤20 green / 21–50 yellow / etc.). */
  hop_cap?: number;
  /** Spawn-on-attach flag — passes through to the Frida client.
   *  Default ``false`` (attach to running process). Spawn is only
   *  useful when the operator wants to see ``onCreate`` / ``onResume``
   *  level events that fire before they could attach manually. */
  spawn?: boolean;
  /** Operator-readable label for logcat / persisted JSONL filenames.
   *  Defaults to ``"behavior-trace-<8 hex>"`` server-side. */
  event_label?: string;
};

export type StartDynamicTraceResponse = {
  session_id: string;
  app_id: string;
  template_id: "behavior_trace_multi";
  package: string;
  pid: number | null;
  /** ISO-8601 string from the Frida session's ``started_at``. */
  started_at: string;
  /** Number of methods actually hooked (after closure extraction +
   *  hop-cap truncation). */
  hook_count: number;
  /** Total methods in the closure before the cap was applied. */
  closure_size: number;
  hop_cap: number;
  event_label: string;
  /** **Legacy.** Currently points at ``/ws/frida/{session_id}``;
   *  consumers should ignore this field and use the ``/ws/trace/...``
   *  shape via :func:`buildTraceWsUrl` instead. The field is kept
   *  for 13.10 backwards-compat; will be rewritten to the new shape
   *  in a future BE pass. */
  ws_url: string;
  persist_path: string | null;
  anchor: {
    /** Smali signature of the active anchor's entry method. */
    entry_method: string;
    hops: number;
  };
};

/** Frida ``send`` envelope (matches :type:`TraceEvent` in
 *  ``api/frida.ts`` byte-for-byte). Renamed here to avoid a
 *  cross-module import cycle. */
type FridaTransportEvent = {
  ts: number;
  session_id: string;
  kind: "send" | "error" | "log";
  payload: unknown;
  raw: Record<string, unknown> | null;
};

/** Multiplexed summary event — the BE's invention; Frida itself
 *  doesn't emit these top-level kinds. Same envelope shape as the
 *  Frida event for code-path symmetry; ``raw`` is always ``null``. */
type SummaryTransportEvent = {
  ts: number;
  session_id: string;
  kind: "summary_pending" | "summary_ready" | "summary_failed";
  payload: {
    /** Java-dotted class name (the ``trace_summary`` helper converts
     *  to Smali for cache keys, but the WS payload stays Java). */
    class: string;
    method: string;
    descriptor: string;
    /** Only on ``summary_ready``. */
    summary?: string;
    /** Only on ``summary_ready``; ``true`` when the summary was
     *  served from ``skill_results_cache`` rather than freshly
     *  generated. */
    cached?: boolean;
    /** Only on ``summary_failed``; one of ``"summary_timeout"`` /
     *  ``"empty_summary"`` / arbitrary exception text. */
    error?: string;
  };
  raw: null;
};

/** Top-level WS message — discriminated by ``kind``. */
export type DynamicTraceWsMessage =
  | FridaTransportEvent
  | SummaryTransportEvent
  /** Drop notice when the session's ring buffer overflows. */
  | { type: "drop"; session_id: string }
  /** Pre-close error (``frida_unavailable`` / ``unknown_session``). */
  | { type: "error"; error: string; message?: string; app_id?: string; session_id?: string };

/** Normalised event shape the consumers see — a flat discriminated
 *  union keyed on ``phase`` (for trace events) or summary kind. The
 *  hook does the ``kind === "send"`` → ``payload.phase`` unwrap so
 *  the consumer doesn't have to.
 *
 *  Each variant carries the operator-facing fields; the raw
 *  envelope is preserved as ``raw`` for the future debug log
 *  surface. */
export type NormalisedTraceEvent =
  | {
      phase: "entry";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
      args: string[];
      seq: number;
      thread_id: number;
      thread_name: string;
      thread_depth: number;
      parent_call_seq: number | null;
    }
  | {
      phase: "exit";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
      ret: string;
      seq: number;
      entry_seq: number | null;
      thread_id: number;
      thread_depth: number;
    }
  | {
      phase: "error";
      ts: number;
      class: string;
      method: string;
      seq: number;
      entry_seq: number | null;
      thread_id: number;
      error: string;
    }
  | {
      phase: "ready";
      ts: number;
      methods_attempted: number;
      methods_hooked: number;
      methods_failed: number;
      error: string | null;
    }
  | {
      phase: "hook_failed";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
      reason: "class_not_found" | "method_not_found" | "impl_set_failed" | string;
      error: string | null;
    }
  | {
      phase: "summary_pending";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
    }
  | {
      phase: "summary_ready";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
      summary: string;
      cached: boolean;
    }
  | {
      phase: "summary_failed";
      ts: number;
      class: string;
      method: string;
      descriptor: string;
      error: string;
    };

/** Java-dotted ``class`` + Smali ``descriptor`` → full Smali
 *  signature key matching :mod:`executionFlowGraph.methodKey`. */
export function smaliSignatureFromEvent(
  classJava: string,
  method: string,
  descriptor: string,
): string {
  return `L${classJava.replace(/\./g, "/")};->${method}${descriptor}`;
}

/** Java-dotted ``class`` + method name → overload-stripped Smali
 *  key matching :mod:`executionFlowGraph.overloadKey`. Multiple
 *  overloads of the same method collapse onto the same node, so
 *  the FE indexes ``firedMethods`` / ``liveValues`` / ``summaries``
 *  by overload key. */
export function overloadKeyFromEvent(
  classJava: string,
  method: string,
): string {
  return `L${classJava.replace(/\./g, "/")};->${method}`;
}

/** Construct the ``/ws/trace/...`` URL for the multiplexed channel.
 *  Honors ``window.location.protocol`` so HTTPS hosts get ``wss://``
 *  for free. */
function buildTraceWsUrl(appId: string, sessionId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/trace/${encodeURIComponent(appId)}/${encodeURIComponent(sessionId)}`;
}

/** Pure helper: map the WS envelope into a flat event for the
 *  consumer reducers. Returns ``null`` for transport-shape messages
 *  the hook handles separately (drop notices / pre-close errors /
 *  Frida ``error`` / ``log`` channels). Exported for test seam +
 *  for any 13.x consumer that wants to replay buffered events. */
export function normaliseTraceMessage(
  msg: DynamicTraceWsMessage,
): NormalisedTraceEvent | null {
  if ("type" in msg) return null;
  if (msg.kind === "summary_pending" || msg.kind === "summary_ready" || msg.kind === "summary_failed") {
    const p = msg.payload;
    if (msg.kind === "summary_ready") {
      return {
        phase: "summary_ready",
        ts: msg.ts,
        class: p.class,
        method: p.method,
        descriptor: p.descriptor,
        summary: p.summary ?? "",
        cached: !!p.cached,
      };
    }
    if (msg.kind === "summary_failed") {
      return {
        phase: "summary_failed",
        ts: msg.ts,
        class: p.class,
        method: p.method,
        descriptor: p.descriptor,
        error: p.error ?? "unknown",
      };
    }
    return {
      phase: "summary_pending",
      ts: msg.ts,
      class: p.class,
      method: p.method,
      descriptor: p.descriptor,
    };
  }
  if (msg.kind !== "send") return null;
  const payload = msg.payload as Record<string, unknown> | null;
  if (!payload || typeof payload !== "object") return null;
  const phase = (payload as { phase?: string }).phase;
  if (!phase) return null;
  if (phase === "entry") {
    return {
      phase: "entry",
      ts: msg.ts,
      class: String((payload as { class?: unknown }).class ?? ""),
      method: String((payload as { method?: unknown }).method ?? ""),
      descriptor: String((payload as { descriptor?: unknown }).descriptor ?? ""),
      args: Array.isArray((payload as { args?: unknown }).args)
        ? ((payload as { args: unknown[] }).args.map((a) => String(a)))
        : [],
      seq: Number((payload as { seq?: unknown }).seq ?? 0),
      thread_id: Number((payload as { thread_id?: unknown }).thread_id ?? 0),
      thread_name: String((payload as { thread_name?: unknown }).thread_name ?? ""),
      thread_depth: Number((payload as { thread_depth?: unknown }).thread_depth ?? 0),
      parent_call_seq:
        (payload as { parent_call_seq?: number | null }).parent_call_seq ?? null,
    };
  }
  if (phase === "exit") {
    // The BE emits ``"return"`` (literal JSON key) for the return
    // value — reserved word in TS, so we read via bracket access.
    const ret = (payload as Record<string, unknown>)["return"];
    return {
      phase: "exit",
      ts: msg.ts,
      class: String((payload as { class?: unknown }).class ?? ""),
      method: String((payload as { method?: unknown }).method ?? ""),
      descriptor: String((payload as { descriptor?: unknown }).descriptor ?? ""),
      ret: ret == null ? "" : String(ret),
      seq: Number((payload as { seq?: unknown }).seq ?? 0),
      entry_seq:
        (payload as { entry_seq?: number | null }).entry_seq ?? null,
      thread_id: Number((payload as { thread_id?: unknown }).thread_id ?? 0),
      thread_depth: Number((payload as { thread_depth?: unknown }).thread_depth ?? 0),
    };
  }
  if (phase === "error") {
    return {
      phase: "error",
      ts: msg.ts,
      class: String((payload as { class?: unknown }).class ?? ""),
      method: String((payload as { method?: unknown }).method ?? ""),
      seq: Number((payload as { seq?: unknown }).seq ?? 0),
      entry_seq:
        (payload as { entry_seq?: number | null }).entry_seq ?? null,
      thread_id: Number((payload as { thread_id?: unknown }).thread_id ?? 0),
      error: String((payload as { error?: unknown }).error ?? ""),
    };
  }
  if (phase === "ready") {
    return {
      phase: "ready",
      ts: msg.ts,
      methods_attempted: Number(
        (payload as { methods_attempted?: unknown }).methods_attempted ?? 0,
      ),
      methods_hooked: Number(
        (payload as { methods_hooked?: unknown }).methods_hooked ?? 0,
      ),
      methods_failed: Number(
        (payload as { methods_failed?: unknown }).methods_failed ?? 0,
      ),
      error: ((payload as { error?: string | null }).error ?? null) || null,
    };
  }
  if (phase === "hook_failed") {
    return {
      phase: "hook_failed",
      ts: msg.ts,
      class: String((payload as { class?: unknown }).class ?? ""),
      method: String((payload as { method?: unknown }).method ?? ""),
      descriptor: String((payload as { descriptor?: unknown }).descriptor ?? ""),
      reason: String((payload as { reason?: unknown }).reason ?? "unknown") as
        | "class_not_found"
        | "method_not_found"
        | "impl_set_failed"
        | string,
      error: ((payload as { error?: string | null }).error ?? null) || null,
    };
  }
  return null;
}

export function startDynamicTrace(
  appId: string,
  body: StartDynamicTraceRequest,
): Promise<ApiResult<StartDynamicTraceResponse>> {
  return _request<StartDynamicTraceResponse>(
    `/api/trace/${encodeURIComponent(appId)}/dynamic`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
}

export function stopDynamicTrace(
  appId: string,
  sessionId: string,
): Promise<ApiResult<{ ok: boolean; session_id: string }>> {
  return _request<{ ok: boolean; session_id: string }>(
    `/api/trace/${encodeURIComponent(appId)}/dynamic/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

// ---------------------------------------------------------------------------
// useDynamicTrace — owns the POST → WS → DELETE lifecycle.
//
// State per running session:
//
//   * ``firedMethods: Set<string>`` of overload-keys (descriptor-
//     stripped Smali) — populated on every ``entry`` event. The
//     ``ExecutionFlow`` consumer uses this to render the fired-
//     edge / fired-node accent (DEC-029 locks accent-blue solid for
//     fired in Dynamic / Both modes).
//   * ``liveValues: Map<overloadKey, LiveValueRecord>`` — per-method
//     latest fire's args / return / thread_depth + a fire count.
//     Multiple overloads of the same method collapse onto the same
//     node so the latest fire across all overloads wins (v1
//     simplification; 13.9 may grow per-overload fan-out).
//   * ``summaries: Map<overloadKey, SummaryState>`` — per-method LLM
//     summary state, fed by the ``summary_pending`` /
//     ``summary_ready`` / ``summary_failed`` events. Cache hits
//     skip ``pending`` and land directly as ``ready`` with
//     ``cached: true`` per the 13.3 contract.
//   * ``hookFailed: Map<overloadKey, HookFailureRecord>`` — populated
//     on ``hook_failed`` events. v1 consumer surface is the
//     ``ExecutionFlow`` "possibly inlined" pill turning red on
//     ``method_not_found`` / ``impl_set_failed``; the full surface
//     lands in 13.9.
//   * ``readyStats`` — final hook counts from the ``ready`` event;
//     the ``LabTraceMode`` consumer renders this as a "N hooked / M
//     attempted" summary in the run-trace button's title.
//
// Reducer-style updaters keep the state immutable per render so
// React Flow's ``useMemo`` dependencies stay simple.

export type LiveValueRecord = {
  args: string[];
  ret: string | null;
  threadId: number;
  threadDepth: number;
  fireCount: number;
  /** ts of the most recent ``entry`` event. */
  lastFireTs: number;
};

export type SummaryState =
  | { state: "pending"; ts: number }
  | { state: "ready"; text: string; cached: boolean; ts: number }
  | { state: "failed"; error: string; ts: number };

export type HookFailureRecord = {
  reason: string;
  error: string | null;
  ts: number;
};

export type DynamicTraceConnection =
  | "idle"
  | "starting"
  | "running"
  | "stopping"
  | "stopped"
  | "disconnected"
  | "error";

export type DynamicTraceState = {
  connection: DynamicTraceConnection;
  sessionId: string | null;
  /** Latest start-response metadata; ``null`` until the first
   *  successful ``start()``. Operator-facing fields (hook count,
   *  package, started_at) live here. */
  meta: StartDynamicTraceResponse | null;
  firedMethods: ReadonlySet<string>;
  liveValues: ReadonlyMap<string, LiveValueRecord>;
  summaries: ReadonlyMap<string, SummaryState>;
  hookFailed: ReadonlyMap<string, HookFailureRecord>;
  readyStats: {
    methods_attempted: number;
    methods_hooked: number;
    methods_failed: number;
  } | null;
  /** Operator-readable error from the most recent failure (POST
   *  rejected / WS dropped with a server-side ``type: "error"``
   *  message / WS construction failed). ``null`` in healthy
   *  states. */
  error: string | null;
  /** Drop-notice counter (matches ``useFridaTrace``'s pattern); the
   *  consumer can render a "N events dropped" badge if non-zero. */
  dropCount: number;
};

export type UseDynamicTraceReturn = {
  state: DynamicTraceState;
  start: (req: StartDynamicTraceRequest) => Promise<void>;
  stop: () => Promise<void>;
  /** Reset the local state without touching the server (useful when
   *  the operator changes anchors and the consumer wants to clear
   *  the carried-over fired-method accents). The ``stop()`` happy
   *  path already calls this internally. */
  reset: () => void;
};

const INITIAL_DYNAMIC_TRACE_STATE: DynamicTraceState = {
  connection: "idle",
  sessionId: null,
  meta: null,
  firedMethods: new Set<string>(),
  liveValues: new Map<string, LiveValueRecord>(),
  summaries: new Map<string, SummaryState>(),
  hookFailed: new Map<string, HookFailureRecord>(),
  readyStats: null,
  error: null,
  dropCount: 0,
};

export function useDynamicTrace(appId: string | null): UseDynamicTraceReturn {
  const [state, setState] = useState<DynamicTraceState>(
    INITIAL_DYNAMIC_TRACE_STATE,
  );

  // WebSocket handle — kept in a ref so ``stop()`` can close it
  // synchronously without racing the open. The ref is the source of
  // truth; the state's ``connection`` field is the consumer-visible
  // mirror.
  const wsRef = useRef<WebSocket | null>(null);
  // Session id ref — kept in a ref so the ``stop()`` callback closes
  // over a stable handle without needing the latest state in its
  // deps (which would re-create the callback on every event).
  const sessionRef = useRef<string | null>(null);
  // Generation counter — incremented on every ``start()`` /
  // ``stop()`` so a stale WS message arriving after the operator
  // reset doesn't clobber the new generation's state.
  const genRef = useRef<number>(0);

  // Reset when ``appId`` flips — a new app always starts a fresh
  // dynamic-trace surface (the previous app's session is the
  // previous session's problem; the BE's per-session ``stop`` GC
  // cleans it up via timeout). Mirrors ``useTraceAnchor``'s
  // appId-clear semantics.
  useEffect(() => {
    genRef.current += 1;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    sessionRef.current = null;
    setState(INITIAL_DYNAMIC_TRACE_STATE);
  }, [appId]);

  const reset = useCallback(() => {
    genRef.current += 1;
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    sessionRef.current = null;
    setState(INITIAL_DYNAMIC_TRACE_STATE);
  }, []);

  const handleMessage = useCallback(
    (raw: MessageEvent, gen: number) => {
      if (genRef.current !== gen) return;
      let parsed: DynamicTraceWsMessage;
      try {
        parsed = JSON.parse(raw.data as string) as DynamicTraceWsMessage;
      } catch {
        return;
      }
      // Drop notice — bump the counter, no normalisation.
      if ("type" in parsed && parsed.type === "drop") {
        setState((s) => ({ ...s, dropCount: s.dropCount + 1 }));
        return;
      }
      // Pre-close error — surface as the connection error, the
      // server will close the socket immediately after.
      if ("type" in parsed && parsed.type === "error") {
        const errStr = parsed.message || parsed.error || "server error";
        setState((s) => ({ ...s, error: errStr }));
        return;
      }
      const ev = normaliseTraceMessage(parsed);
      if (!ev) return;
      setState((s) => updateForEvent(s, ev));
    },
    [],
  );

  const start = useCallback(
    async (req: StartDynamicTraceRequest) => {
      if (!appId) return;
      // Bump generation BEFORE any async work so a stale response
      // from a previous start doesn't clobber the new state.
      const myGen = ++genRef.current;
      // Close any prior WS.
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
        wsRef.current = null;
      }
      sessionRef.current = null;
      setState({
        ...INITIAL_DYNAMIC_TRACE_STATE,
        connection: "starting",
      });
      const r = await startDynamicTrace(appId, req);
      if (genRef.current !== myGen) return;
      if (!r.ok) {
        setState((s) => ({
          ...s,
          connection: "error",
          error: r.status > 0 ? `${r.status}: ${r.error}` : r.error,
        }));
        return;
      }
      const sessionId = r.data.session_id;
      sessionRef.current = sessionId;
      setState((s) => ({
        ...s,
        connection: "running",
        sessionId,
        meta: r.data,
      }));
      // Open the WS. We DON'T await — onmessage will fire as
      // events come in. WS construction can throw on malformed
      // URLs; we surface that as ``connection: "error"``.
      let ws: WebSocket;
      try {
        ws = new WebSocket(buildTraceWsUrl(appId, sessionId));
      } catch (e) {
        if (genRef.current !== myGen) return;
        setState((s) => ({
          ...s,
          connection: "error",
          error: e instanceof Error ? e.message : String(e),
        }));
        return;
      }
      wsRef.current = ws;
      ws.onmessage = (msg) => handleMessage(msg, myGen);
      ws.onclose = () => {
        if (genRef.current !== myGen) return;
        // Distinguish operator-initiated close (handled in stop()
        // which sets ``connection`` first) from a server-side drop.
        setState((s) =>
          s.connection === "stopping" || s.connection === "stopped"
            ? { ...s, connection: "stopped" }
            : { ...s, connection: "disconnected" },
        );
      };
      ws.onerror = () => {
        if (genRef.current !== myGen) return;
        setState((s) => ({
          ...s,
          error: s.error ?? "WebSocket error",
        }));
      };
    },
    [appId, handleMessage],
  );

  const stop = useCallback(async () => {
    if (!appId) return;
    const sid = sessionRef.current;
    if (!sid) return;
    setState((s) => ({ ...s, connection: "stopping" }));
    // Close the WS first so we don't keep processing events while
    // the BE tears down the Frida session.
    if (wsRef.current) {
      try {
        wsRef.current.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    }
    const r = await stopDynamicTrace(appId, sid);
    setState((s) => ({
      ...s,
      connection: "stopped",
      error: r.ok ? s.error : r.error,
    }));
  }, [appId]);

  // Cleanup on unmount — close the WS but DON'T fire ``stop()`` (the
  // BE's session-timeout GC catches the abandoned session; firing
  // ``stop()`` from cleanup races the unmount and leaves the
  // operator with an unhandled-rejection if the DELETE fails).
  useEffect(() => {
    return () => {
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
        wsRef.current = null;
      }
    };
  }, []);

  return { state, start, stop, reset };
}

/** State-update reducer — splits out of the hook so it's testable
 *  and so the ``setState(s => ...)`` callbacks aren't a ~80 LOC
 *  inline lambda. Pure: doesn't mutate ``s``. */
function updateForEvent(
  s: DynamicTraceState,
  ev: NormalisedTraceEvent,
): DynamicTraceState {
  if (ev.phase === "ready") {
    return {
      ...s,
      readyStats: {
        methods_attempted: ev.methods_attempted,
        methods_hooked: ev.methods_hooked,
        methods_failed: ev.methods_failed,
      },
      error: ev.error ?? s.error,
    };
  }
  if (ev.phase === "hook_failed") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const next = new Map(s.hookFailed);
    next.set(key, { reason: ev.reason, error: ev.error, ts: ev.ts });
    return { ...s, hookFailed: next };
  }
  if (ev.phase === "entry") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const fired = new Set(s.firedMethods);
    fired.add(key);
    const live = new Map(s.liveValues);
    const prior = live.get(key);
    live.set(key, {
      args: ev.args,
      ret: prior?.ret ?? null,
      threadId: ev.thread_id,
      threadDepth: ev.thread_depth,
      fireCount: (prior?.fireCount ?? 0) + 1,
      lastFireTs: ev.ts,
    });
    return { ...s, firedMethods: fired, liveValues: live };
  }
  if (ev.phase === "exit") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const live = new Map(s.liveValues);
    const prior = live.get(key);
    if (!prior) {
      // Exit without a matching entry — possible if the operator
      // late-joined and the entry is in the replay-but-already-
      // sent window. v1 ignores; 13.9 may surface.
      return s;
    }
    live.set(key, { ...prior, ret: ev.ret });
    return { ...s, liveValues: live };
  }
  if (ev.phase === "error") {
    // v1: surface as the global error string; per-method runtime
    // errors land in 13.9 with an inline ⚠ pill on the node.
    return { ...s, error: `${ev.class}.${ev.method}: ${ev.error}` };
  }
  if (ev.phase === "summary_pending") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const next = new Map(s.summaries);
    // Don't overwrite an already-ready summary with pending — the
    // BE replays cached summaries as ``ready`` immediately, so a
    // late ``pending`` here would be the operator's perception of
    // a regression.
    if (next.get(key)?.state === "ready") return s;
    next.set(key, { state: "pending", ts: ev.ts });
    return { ...s, summaries: next };
  }
  if (ev.phase === "summary_ready") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const next = new Map(s.summaries);
    next.set(key, {
      state: "ready",
      text: ev.summary,
      cached: ev.cached,
      ts: ev.ts,
    });
    return { ...s, summaries: next };
  }
  if (ev.phase === "summary_failed") {
    const key = overloadKeyFromEvent(ev.class, ev.method);
    const next = new Map(s.summaries);
    next.set(key, { state: "failed", error: ev.error, ts: ev.ts });
    return { ...s, summaries: next };
  }
  return s;
}

// ---------------------------------------------------------------------------
// Phase 11 v2 hook (preserved verbatim below — useTraceAnchor)
// ---------------------------------------------------------------------------

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
