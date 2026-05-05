/**
 * Trace mode for the Lab tab — the headline UI for Phase 10
 * (sub-steps 10.7 + 10.8 + the post-v1 method-picker follow-up) plus
 * Phase 11 v2.1.1's entry-method discoverability restructure. The
 * 10.6 placeholder shipped a status-only view; this rewrite is the
 * real surface:
 *
 *   1. Seed pill (when present): a small "Seeded from <source>"
 *      banner that surfaces 10.8's cross-tab handoff (Inspect → Trace,
 *      Graph → Trace via 11.2). Sits at the top of the pane so the
 *      operator knows the entry wasn't typed by hand, with a "×"
 *      clear button. Cleared on manual edit / submit / app change.
 *   2. **v2.1.1: Browse classes section** (collapsible, default-
 *      collapsed). Embeds the existing ``ClassMethodTree`` component
 *      that the Inspect tab uses, so operators can pick an entry
 *      method by browsing the call-graph closure rather than typing
 *      a Smali signature blind. Click a method → seeds the entry
 *      field with a Smali class-prefix (``Lcom/example/Foo;->onClick(``)
 *      → activates the MethodPicker (item 4) for overload confirmation
 *      → operator picks an overload → auto-fires the trace. App/Libs
 *      filter toggles reuse ``ClassMethodTree``'s existing defaults.
 *   3. **v2.1.1: Inline controls row** — Hops stepper (1..6 clamped,
 *      always visible as a small inline control) + "Advanced: type
 *      Smali signature directly" toggle (default-collapsed). When
 *      Advanced is expanded, the raw Smali entry input + Trace +
 *      Force re-trace buttons become available — the operator-power-
 *      user path that was the v1 default. Browse + MethodPicker is
 *      the new operator-easy-mode path.
 *   4. Method picker: activates whenever the entry is a class-prefix-
 *      only string (``Lcom/.../Foo;->[partial]``) and reads from
 *      ``GET /api/graph/{app_id}/methods``. Closes the operator-
 *      visible workflow gap when the Inspect → Trace seed couldn't
 *      pin a method (most ``findViewById`` candidates) and is the
 *      committal step for the new v2.1.1 Browse-classes flow —
 *      clicking a row fills the entry with the full Smali signature
 *      and auto-fires the trace. Debounced 150 ms so a fast typist
 *      doesn't fire one request per keystroke.
 *   5. Result region: ``BehaviorAnchorCard`` header + ``DecisionTimeline``
 *      + the per-plan ``BypassPlanCard`` list (default plans visible,
 *      advanced plans behind an ``<details>`` expander per DEC-024).
 *   6. Cached anchors picker: a small list of previously-built
 *      anchors so the operator can flip between them without
 *      re-typing the smali signature.
 *   7. Status row: cache + decompile + call-graph readiness, surfaced
 *      via the same ``GET /status`` shape the 10.6 placeholder hit.
 *
 * Lifecycle owned by the ``useTraceAnchor`` hook in ``api/trace.ts``:
 * GET first (cache), surface "missing" when the entry isn't cached,
 * operator clicks Build to fire POST. ``Force re-trace`` always fires
 * POST with ``force=true``.
 *
 * 10.8 chat plumbing: Trace mode itself still has no chat dock of
 * its own — operators who want to ask the LLM about a trace switch
 * to Manual Hooks mode, which carries the ``ChatDock``. The active
 * ``BehaviorAnchor`` is published to the parent ``LabTab`` via the
 * ``onActiveAnchorChange`` callback so the Manual Hooks chat
 * builder can fold it into the new ``trace`` ``ChatAttachment``.
 *
 * v2.1.1 also bootstraps the decompile status + class-tree fetch
 * lifecycle that ``ClassMethodTree`` needs (mirrors the same pattern
 * ``InspectTab`` uses — ``getDecompileStatus`` on app change,
 * ``fetchTree`` once status is ``ready``, polling while ``pending``).
 * No new backend routes; reuses ``/api/decompile/{app_id}/status``
 * and ``/api/code/{app_id}/tree``.
 *
 * **v2.1.2 — backend coalescer + debounced spinner + validation
 * pill** (Q5 (A) class+method-only translation; Q6 (B) backend
 * coalescer for authoritative call-graph validation):
 *
 *   * Debounced effect (400ms) on ``entryDraft`` calls
 *     ``POST /api/trace/{app_id}/normalise-entry`` — the new
 *     :func:`androscan.web.trace_routes._coalesce_entry` heuristic
 *     dispatcher translates dotted Java / partial Smali / stack-trace
 *     lines into a canonical Smali method-prefix and validates the
 *     class against the call graph in the same round-trip.
 *   * Inline spinner renders during the call (``trace-entry-spinner``).
 *   * Result pill renders ``trace-entry-validation-pill`` in one of
 *     four states:
 *       - **✓ valid** — class exists in the graph; pill copy is
 *         "Lcom/example/Foo; · N method(s)" so the operator sees the
 *         normalised form alongside the count the picker would
 *         surface.
 *       - **⚠ class not found** — input parsed cleanly but no
 *         matching class in the call graph. v2.1.3's "Find similar
 *         classes" button (lands next sub-step) hangs off this state.
 *       - **✗ couldn't parse** — coalescer returned 422; the pill
 *         carries the operator-readable parse-failure reason.
 *       - **— validation unavailable** — 404 / 409 / network error;
 *         pill renders neutrally so the operator can still proceed
 *         via Browse / Advanced without a spurious red ⚠.
 *   * Stale-response guard (``coalescerInflightKeyRef``) — keyed on
 *     ``(appId, entry)`` so a slow request for an old entry doesn't
 *     clobber the pill state for the current input.
 *   * The pill sits below the inline-row (Hops + Advanced toggle)
 *     and above the MethodPicker so it's visible regardless of which
 *     entry-discovery path the operator is using (Browse / Advanced /
 *     cross-tab seed).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { IconChevronDown, IconChevronUp } from "../components/Icons";
import { BehaviorAnchorCard } from "../components/trace/BehaviorAnchorCard";
import { BypassPlanCard } from "../components/trace/BypassPlanCard";
import { DecisionTimeline } from "../components/trace/DecisionTimeline";
import { ClassMethodTree } from "../components/ClassMethodTree";
import {
  fetchTree,
  getDecompileStatus,
  type CodeTree,
  type DecompileStatus,
} from "../api/code";
import { listMethodsOnClass, type GraphNode } from "../api/graph";
import {
  deleteTraceAnchor,
  fetchTraceStatus,
  listTraceAnchors,
  normaliseTraceEntry,
  useTraceAnchor,
  type BehaviorAnchor,
  type NormaliseEntryResponse,
  type TraceAnchorRow,
  type TraceStatusPayload,
} from "../api/trace";
import { useWorkbench } from "../context/WorkbenchContext";
import { javaRelPathToSmaliMethodPrefix } from "../util/smaliClassToFile";

type Props = {
  appId: string | null;
  /** Phase 10 sub-step 10.8: publish the active ``BehaviorAnchor`` (or
   *  ``null`` when nothing is loaded) up to the parent ``LabTab`` so the
   *  Manual Hooks chat builder can fold it into a ``trace``
   *  ``ChatAttachment``. Pure callback; no rendering responsibility. */
  onActiveAnchorChange?: (anchor: BehaviorAnchor | null) => void;
};

/** Heuristic: a Smali method signature is "complete" once it has a
 *  closing paren and a return descriptor. We use this in 10.8 to decide
 *  whether to auto-fire the trace on a cross-tab seed (full sig → fire)
 *  vs. just prefilling the form for the operator to complete (prefix
 *  → wait). False positives are cheap (the trace skill 404s on a bad
 *  signature and we fall back to the "missing" empty-state with a
 *  clear error). */
function looksLikeCompleteSmaliSignature(s: string): boolean {
  const trimmed = s.trim();
  if (!trimmed) return false;
  const closeIdx = trimmed.lastIndexOf(")");
  if (closeIdx <= 0) return false;
  // Return descriptor: V, Z, B, S, C, I, J, F, D, or a class form / array.
  const ret = trimmed.slice(closeIdx + 1);
  return /^\[*([VZBSCIJFD]|L[\w/$]+;)$/.test(ret);
}

/** Split a Smali entry-method prefix or signature into ``{smaliClass,
 *  methodPrefix}``. Used by the method picker to decide whether to fire
 *  the autocomplete query and what to filter by:
 *
 *    ``Lcom/example/Foo;->``           → ``{ smaliClass: "Lcom/example/Foo;",
 *                                            methodPrefix: "" }``
 *    ``Lcom/example/Foo;->onCli``      → ``{ smaliClass: "Lcom/example/Foo;",
 *                                            methodPrefix: "onCli" }``
 *    ``Lcom/example/Foo;->onClick(``   → ``{ smaliClass: "Lcom/example/Foo;",
 *                                            methodPrefix: "onClick" }``
 *                                        (Inspect → Trace seed shape — the
 *                                         resolver knows the method but not
 *                                         the descriptors. Operator wants
 *                                         the picker to surface overloads;
 *                                         we strip the trailing ``(`` for
 *                                         the query so all ``onClick(...)*``
 *                                         appear.)
 *    ``Lcom/example/Foo;->onClick(L``  → null (operator is now typing
 *                                        descriptors deliberately — picker
 *                                        would be misleading)
 *    ``Lcom/example/Foo;``             → null (no ``->`` separator yet)
 *    ``junk``                          → null
 */
function classPrefixContext(
  s: string,
): { smaliClass: string; methodPrefix: string } | null {
  const t = s.trim();
  const sep = t.indexOf(";->");
  if (sep <= 0) return null;
  const klass = t.slice(0, sep + 1); // include the ``;``
  if (!/^L[\w/$]+;$/.test(klass)) return null;
  const tail = t.slice(sep + 3);
  // Picker activates while the operator is in "method-name selection"
  // mode. We accept three tail shapes:
  //   ""          — bare prefix, show all methods on the class.
  //   "onCli"     — partial name, filter by prefix.
  //   "onClick("  — name + lone opening paren (the 10.8 Inspect → Trace
  //                 seed shape). Strip the trailing ``(`` for the query
  //                 so all overloads of the method appear.
  // Anything inside the parens (``onClick(L...``) means the operator
  // is past name-selection; the picker would be misleading there.
  const m = tail.match(/^([\w$]*)\(?$/);
  if (!m) return null;
  return { smaliClass: klass, methodPrefix: m[1] };
}

const DEFAULT_HOPS = 3;
const MIN_HOPS = 1;
const MAX_HOPS = 6;
/** Picker query is debounced this long after the last keystroke so we
 *  don't fire one request per character on a fast typist. */
const PICKER_DEBOUNCE_MS = 150;
/** Hard cap on the number of methods we display in the picker — the
 *  backend route caps at 500, but anything past ~50 isn't a useful
 *  pick list (operator should narrow with more name prefix). */
const PICKER_DISPLAY_LIMIT = 50;
/** v2.1.2 coalescer debounce — longer than the picker because the
 *  coalescer runs a single SQLite COUNT(*) call (~5-10ms) and the
 *  validation pill is the operator's main feedback signal: we'd
 *  rather wait 400ms for a stable value than render a flickering
 *  pill that updates every keystroke. */
const COALESCER_DEBOUNCE_MS = 400;

/** v2.1.2 — discriminated union describing the three pill states the
 *  ``EntryValidationPill`` sub-component renders. Lifted to module
 *  scope so both the parent ``LabTraceMode`` (which produces it via
 *  the debounced effect) and the consumer can share the type without
 *  re-deriving it. */
type CoalescerResult =
  | { kind: "ok"; data: NormaliseEntryResponse }
  | { kind: "parse_error"; detail: string }
  | { kind: "unavailable"; status: number; detail: string };

export function LabTraceMode({ appId, onActiveAnchorChange }: Props) {
  const { pendingTraceEntry, setPendingTraceEntry, dossier } = useWorkbench();

  // ----- form state ------------------------------------------------------
  const [entryDraft, setEntryDraft] = useState("");
  const [hopsDraft, setHopsDraft] = useState<number>(DEFAULT_HOPS);
  const [activeEntry, setActiveEntry] = useState<string | null>(null);
  const [activeHops, setActiveHops] = useState<number>(DEFAULT_HOPS);

  // 10.8: Track the source label written by the cross-tab seed so the
  // operator can see "Seeded from Inspect → MainActivity:42" until they
  // edit the field or fire the trace. Cleared on manual edit / submit /
  // app change so it never lingers stale.
  const [seedLabel, setSeedLabel] = useState<string | null>(null);

  // ----- v2.1.1: Browse classes + Advanced disclosure state -------------
  // Both default-collapsed (Q1: A1, Q4: B). Browse expands the embedded
  // ``ClassMethodTree``; Advanced reveals the raw Smali entry input +
  // Trace + Force re-trace buttons (the v1 power-user surface).
  const [browseExpanded, setBrowseExpanded] = useState(false);
  const [advancedExpanded, setAdvancedExpanded] = useState(false);

  // v2.1.1: Decompile status + class tree for the embedded
  // ``ClassMethodTree``. Mirrors the InspectTab bootstrap pattern
  // (status first, then tree once ``ready``, poll while ``pending``)
  // so the two surfaces share the same readiness contract — if Browse
  // is expanded before jadx finishes, the operator sees the same
  // "Decompiling APK with jadx…" copy + Refresh button as Inspect.
  const [decompile, setDecompile] = useState<DecompileStatus | null>(null);
  const [tree, setTree] = useState<CodeTree | null>(null);
  const [treeFilter, setTreeFilter] = useState("");

  // App package (used by ``ClassMethodTree`` to split App vs. Library
  // packages) read off the workbench-wide dossier (Reports tab keeps
  // it in sync with the active selection).
  const packageName = useMemo<string | null>(() => {
    const apk = (dossier as Record<string, unknown> | null)?.apk_info as
      | { package?: string }
      | undefined;
    return apk?.package || null;
  }, [dossier]);

  // ----- status + cached-anchors list ------------------------------------
  const [status, setStatus] = useState<TraceStatusPayload | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(false);

  const [cached, setCached] = useState<TraceAnchorRow[]>([]);
  const [cachedReloadTick, setCachedReloadTick] = useState(0);

  // ----- the anchor lifecycle --------------------------------------------
  const { state, build, clear } = useTraceAnchor(appId, activeEntry, activeHops);

  // Reset the form + active target whenever the operator changes app.
  useEffect(() => {
    setEntryDraft("");
    setHopsDraft(DEFAULT_HOPS);
    setActiveEntry(null);
    setActiveHops(DEFAULT_HOPS);
    setSeedLabel(null);
    setBrowseExpanded(false);
    setAdvancedExpanded(false);
    setDecompile(null);
    setTree(null);
    setTreeFilter("");
    setCoalescerLoading(false);
    setCoalescerResult(null);
    coalescerInflightKeyRef.current = null;
    clear();
  }, [appId, clear]);

  // v2.1.1: bootstrap decompile status + class tree on app change.
  // Mirrors InspectTab's bootstrap pattern. We fetch eagerly (rather
  // than lazily on Browse-expand) so that when the operator opens
  // Browse the tree is already loaded — avoiding a perceptible
  // "loading…" flash on the most-common click path. The fetch is
  // cheap (one HEAD-style status call + one cached tree fetch).
  useEffect(() => {
    if (!appId) return;
    let cancelled = false;
    void (async () => {
      const s = await getDecompileStatus(appId);
      if (cancelled) return;
      setDecompile(s);
      if (s.status === "ready") {
        const t = await fetchTree(appId);
        if (!cancelled) setTree(t);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [appId]);

  // v2.1.1: poll while jadx is mid-flight so the embedded
  // ``ClassMethodTree`` flips from the "decompiling…" placeholder to
  // the real tree without operator intervention. Identical cadence
  // to InspectTab's poll (2 s).
  useEffect(() => {
    if (!appId || decompile?.status !== "pending") return;
    const id = window.setInterval(async () => {
      const s = await getDecompileStatus(appId);
      setDecompile(s);
      if (s.status === "ready") {
        const t = await fetchTree(appId);
        setTree(t);
      }
    }, 2000);
    return () => window.clearInterval(id);
  }, [appId, decompile?.status]);

  // 10.8 cross-tab seed: pendingTraceEntry is set by the Inspect tab's
  // BestBanner. We prefill the form, surface a "Seeded from ..." pill,
  // and *auto-fire* the trace only when the seeded value already has a
  // complete return descriptor — partial seeds (which are the common
  // case, since the resolver doesn't carry per-overload params) just
  // sit in the input field for the operator to complete.
  useEffect(() => {
    if (!pendingTraceEntry) return;
    if (!appId || pendingTraceEntry.appId !== appId) return;
    const prefix = pendingTraceEntry.entryPrefix;
    const hops = Math.max(
      MIN_HOPS,
      Math.min(MAX_HOPS, pendingTraceEntry.hops || DEFAULT_HOPS),
    );
    setEntryDraft(prefix);
    setHopsDraft(hops);
    setSeedLabel(pendingTraceEntry.sourceLabel ?? "Seeded externally");
    if (looksLikeCompleteSmaliSignature(prefix)) {
      setActiveEntry(prefix.trim());
      setActiveHops(hops);
    } else {
      setActiveEntry(null);
      clear();
    }
    setPendingTraceEntry(null);
    // ``clear`` is a stable callback; intentional partial deps to avoid
    // re-firing the seed every time anchor state changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingTraceEntry?.ts, appId]);

  // 10.8: republish the active anchor (or ``null``) to the parent
  // ``LabTab`` so the Manual Hooks chat builder can fold it into a
  // ``trace`` ``ChatAttachment``. Effect-based instead of inline so a
  // build → loaded transition fires the callback exactly once.
  useEffect(() => {
    if (!onActiveAnchorChange) return;
    if (state.kind === "loaded") {
      onActiveAnchorChange(state.anchor);
    } else {
      onActiveAnchorChange(null);
    }
  }, [state, onActiveAnchorChange]);

  // Status fetch on app change + after Build (so the cache count
  // updates without an explicit reload).
  useEffect(() => {
    setStatus(null);
    setStatusError(null);
    if (!appId) return;
    let cancelled = false;
    setStatusLoading(true);
    void (async () => {
      const r = await fetchTraceStatus(appId);
      if (cancelled) return;
      setStatusLoading(false);
      if (r.ok) setStatus(r.data);
      else setStatusError(`${r.status ? `${r.status} — ` : ""}${r.error}`);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, cachedReloadTick]);

  // Cached anchors list — refresh on app change + after Build / Delete.
  useEffect(() => {
    setCached([]);
    if (!appId) return;
    let cancelled = false;
    void (async () => {
      const r = await listTraceAnchors(appId);
      if (cancelled) return;
      if (r.ok) setCached(r.data.anchors);
    })();
    return () => {
      cancelled = true;
    };
  }, [appId, cachedReloadTick]);

  // After a successful Build, bump the reload tick so the status +
  // cached list pick up the new row.
  useEffect(() => {
    if (state.kind === "loaded" && state.from === "build") {
      setCachedReloadTick((t) => t + 1);
    }
  }, [state]);

  const lowConfidenceSet = useMemo(() => {
    if (state.kind !== "loaded") return new Set<number>();
    return new Set(state.anchor.low_confidence_decision_indices);
  }, [state]);

  // Method picker — fires when the entry is a class-prefix-only string
  // (``Lcom/.../Foo;->[partial]``). Lets the operator discover available
  // methods/overloads without typing descriptors blind. Closes the
  // operator-visible workflow gap from the Inspect → Trace seed when
  // the resolver couldn't pin a method (most ``findViewById`` candidates).
  const pickerCtx = useMemo(() => classPrefixContext(entryDraft), [entryDraft]);
  const [pickerMethods, setPickerMethods] = useState<GraphNode[] | null>(null);
  const [pickerLoading, setPickerLoading] = useState(false);
  const [pickerTotal, setPickerTotal] = useState(0);
  const [pickerError, setPickerError] = useState<string | null>(null);
  // Stale-response guard: keyed on (smaliClass, methodPrefix, appId) so
  // a slow request for an old class doesn't clobber the picker state
  // for the current input.
  const pickerInflightKeyRef = useRef<string | null>(null);

  // v2.1.2 coalescer state. Three flavours of "result":
  //   - kind: "ok"      → validation pill renders ✓ / ⚠ from the response
  //   - kind: "parse_error" → pill renders ✗ with the 422 detail string
  //   - kind: "unavailable" → pill renders neutral (404 / 409 / network)
  // The "result" + "loading" tuple lives next to each other so a
  // render never sees a stale (loading: false, result: <previous>)
  // tuple while a fresh request is in flight — same pattern the
  // picker uses via its own keyed effect.
  const [coalescerLoading, setCoalescerLoading] = useState(false);
  const [coalescerResult, setCoalescerResult] = useState<CoalescerResult | null>(null);
  const coalescerInflightKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!appId || !pickerCtx) {
      setPickerMethods(null);
      setPickerLoading(false);
      setPickerError(null);
      pickerInflightKeyRef.current = null;
      return;
    }
    const key = `${appId}|${pickerCtx.smaliClass}|${pickerCtx.methodPrefix}`;
    pickerInflightKeyRef.current = key;
    setPickerLoading(true);
    setPickerError(null);
    const handle = setTimeout(async () => {
      const r = await listMethodsOnClass(appId, pickerCtx.smaliClass, {
        namePrefix: pickerCtx.methodPrefix || null,
        limit: PICKER_DISPLAY_LIMIT,
      });
      // Only commit if this response is for the latest query.
      if (pickerInflightKeyRef.current !== key) return;
      setPickerLoading(false);
      if (r.ok) {
        setPickerMethods(r.data.methods);
        setPickerTotal(r.data.total);
      } else {
        setPickerMethods([]);
        setPickerTotal(0);
        setPickerError(`${r.status ? `${r.status} — ` : ""}${r.error}`);
      }
    }, PICKER_DEBOUNCE_MS);
    return () => {
      clearTimeout(handle);
    };
  }, [appId, pickerCtx?.smaliClass, pickerCtx?.methodPrefix, pickerCtx]);

  // v2.1.2 debounced coalescer effect — fires
  // ``POST /api/trace/{app_id}/normalise-entry`` 400ms after the last
  // entryDraft change. Powers the inline ✓ / ⚠ / ✗ validation pill.
  // Skipped (cleared) when entryDraft is empty so the pill doesn't
  // render on a blank pane.
  const trimmedEntry = entryDraft.trim();
  useEffect(() => {
    if (!appId || !trimmedEntry) {
      setCoalescerLoading(false);
      setCoalescerResult(null);
      coalescerInflightKeyRef.current = null;
      return;
    }
    const key = `${appId}|${trimmedEntry}`;
    coalescerInflightKeyRef.current = key;
    setCoalescerLoading(true);
    const handle = setTimeout(async () => {
      const r = await normaliseTraceEntry(appId, trimmedEntry);
      // Stale-response guard.
      if (coalescerInflightKeyRef.current !== key) return;
      setCoalescerLoading(false);
      if (r.ok) {
        setCoalescerResult({ kind: "ok", data: r.data });
      } else if (r.status === 422) {
        // Coalescer returned a parse-failure with an operator-readable
        // detail — render as the ✗ pill.
        setCoalescerResult({ kind: "parse_error", detail: r.error });
      } else {
        // 404 / 409 / network / 5xx — validation isn't available;
        // pill renders neutrally rather than misleading the operator
        // with a red ⚠ that suggests their input is bad.
        setCoalescerResult({
          kind: "unavailable",
          status: r.status,
          detail: r.error,
        });
      }
    }, COALESCER_DEBOUNCE_MS);
    return () => {
      clearTimeout(handle);
    };
  }, [appId, trimmedEntry]);

  const onPickMethod = (sig: string) => {
    setEntryDraft(sig);
    setSeedLabel(null);
    const hops = Math.max(MIN_HOPS, Math.min(MAX_HOPS, hopsDraft || DEFAULT_HOPS));
    setActiveEntry(sig);
    setActiveHops(hops);
  };

  // v2.1.1: ``ClassMethodTree`` selection handler. Builds a Smali
  // class-prefix from the rel-path + optional method name and seeds
  // the entry field with it — which immediately activates the
  // MethodPicker because the seeded value matches ``classPrefixContext``
  // (``Lcom/example/Foo;->`` for class clicks, ``Lcom/example/Foo;->onClick(``
  // for method clicks). The operator confirms an overload via the
  // picker, which auto-fires the trace through the existing
  // ``onPickMethod`` path.
  //
  // We *don't* clear ``activeEntry`` here — the MethodPicker is the
  // commit step. We *do* clear the seed-pill (so a subsequent tree
  // click after a cross-tab seed doesn't show a stale "Seeded from
  // Inspect → ..." label).
  const onSelectFromTree = (sel: {
    rel_path: string;
    class_name: string;
    method?: string;
  }) => {
    const prefix = javaRelPathToSmaliMethodPrefix(sel.rel_path, sel.method ?? null);
    if (!prefix) return;
    setEntryDraft(prefix);
    setSeedLabel(null);
  };

  const entryComplete = looksLikeCompleteSmaliSignature(entryDraft);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const entry = entryDraft.trim();
    if (!entry || !entryComplete) return;
    const hops = Math.max(MIN_HOPS, Math.min(MAX_HOPS, hopsDraft || DEFAULT_HOPS));
    setActiveEntry(entry);
    setActiveHops(hops);
    setSeedLabel(null);
  };

  const onForceRebuild = () => {
    if (!activeEntry) return;
    void build(true);
  };

  const onPickCached = (row: TraceAnchorRow) => {
    setEntryDraft(row.entry_smali_id);
    setHopsDraft(row.hops);
    setActiveEntry(row.entry_smali_id);
    setActiveHops(row.hops);
    setSeedLabel(null);
  };

  const onDeleteCached = async (row: TraceAnchorRow) => {
    if (!appId) return;
    const r = await deleteTraceAnchor(appId, row.entry_smali_id, row.hops);
    if (r.ok) {
      // If the deleted row was the active one, clear the active state
      // so the result region returns to its empty state.
      if (activeEntry === row.entry_smali_id && activeHops === row.hops) {
        setActiveEntry(null);
        clear();
      }
      setCachedReloadTick((t) => t + 1);
    }
  };

  return (
    <div className="lab-trace-mode pane-scroll">
      <header className="pane-head">
        <h2>Behavior Trace</h2>
        <span className="muted small">
          UI element ➜ decision points ➜ bypass plans
        </span>
      </header>

      {!appId && (
        <p className="muted small">
          No app selected — pick a project in the Reports tab to start
          tracing behaviour.
        </p>
      )}

      {appId && (
        <>
          {seedLabel && (
            <div className="trace-seed-pill" role="status" aria-live="polite">
              <span className="trace-seed-pill-label">{seedLabel}</span>
              <button
                type="button"
                className="trace-seed-pill-clear"
                onClick={() => {
                  setSeedLabel(null);
                  setEntryDraft("");
                }}
                title="Clear the seeded value"
                aria-label="Clear the seeded value"
              >
                ×
              </button>
            </div>
          )}

          <section className="trace-class-browser">
            <button
              type="button"
              className="trace-class-browser-head"
              onClick={() => setBrowseExpanded((v) => !v)}
              aria-expanded={browseExpanded}
              aria-controls="trace-class-browser-body"
              title={
                browseExpanded
                  ? "Collapse the class browser"
                  : "Browse decompiled classes & methods to pick a trace entry"
              }
            >
              {browseExpanded
                ? <IconChevronDown size={10} />
                : <IconChevronUp size={10} />}
              <span className="trace-class-browser-title">Browse classes</span>
              <span className="muted small trace-class-browser-hint">
                pick a method to trace from the call-graph closure
              </span>
            </button>
            {browseExpanded && (
              <div
                id="trace-class-browser-body"
                className="trace-class-browser-body"
              >
                <ClassMethodTree
                  appId={appId}
                  status={decompile}
                  tree={tree}
                  filter={treeFilter}
                  onFilterChange={setTreeFilter}
                  onTreeLoaded={setTree}
                  onStatus={setDecompile}
                  onSelect={onSelectFromTree}
                  appPackage={packageName}
                />
              </div>
            )}
          </section>

          <form className="trace-form-inline" onSubmit={onSubmit}>
            <label className="trace-form-field trace-form-hops">
              <span>Hops</span>
              <input
                type="number"
                min={MIN_HOPS}
                max={MAX_HOPS}
                step={1}
                value={hopsDraft}
                onChange={(e) => setHopsDraft(parseInt(e.target.value, 10) || DEFAULT_HOPS)}
              />
            </label>
            <button
              type="button"
              className="trace-advanced-toggle"
              onClick={() => setAdvancedExpanded((v) => !v)}
              aria-expanded={advancedExpanded}
              aria-controls="trace-advanced-form-body"
              title={
                advancedExpanded
                  ? "Hide the raw Smali signature input"
                  : "Type a Smali signature directly (advanced)"
              }
            >
              {advancedExpanded
                ? <IconChevronDown size={10} />
                : <IconChevronUp size={10} />}
              <span className="trace-advanced-toggle-title">
                Advanced: type Smali signature directly
              </span>
            </button>
            {advancedExpanded && (
              <div
                id="trace-advanced-form-body"
                className="trace-advanced-form-body"
              >
                <label className="trace-form-field trace-form-entry">
                  <span>Entry method (smali signature)</span>
                  <input
                    type="text"
                    value={entryDraft}
                    onChange={(e) => {
                      setEntryDraft(e.target.value);
                      if (seedLabel) setSeedLabel(null);
                    }}
                    placeholder="Lcom/example/Foo;->onClick(Landroid/view/View;)V"
                    autoComplete="off"
                    spellCheck={false}
                  />
                </label>
                <div className="trace-form-buttons">
                  <button
                    type="submit"
                    disabled={!entryComplete}
                    title={
                      entryComplete
                        ? "Run the trace_behavior skill on this entry method"
                        : "Add the method name + parameter descriptors + return type, e.g. onClick(Landroid/view/View;)V — or pick from the list below"
                    }
                  >
                    Trace
                  </button>
                  <button
                    type="button"
                    onClick={onForceRebuild}
                    disabled={!activeEntry || state.kind === "building"}
                    title="Re-run the trace_behavior skill from scratch (bypass cache)"
                  >
                    Force re-trace
                  </button>
                </div>
              </div>
            )}
          </form>

          <EntryValidationPill
            entry={trimmedEntry}
            loading={coalescerLoading}
            result={coalescerResult}
          />

          {pickerCtx && (
            <MethodPicker
              smaliClass={pickerCtx.smaliClass}
              methodPrefix={pickerCtx.methodPrefix}
              methods={pickerMethods}
              total={pickerTotal}
              loading={pickerLoading}
              error={pickerError}
              onPick={onPickMethod}
            />
          )}

          <TraceResultRegion
            state={state}
            appId={appId}
            lowConfidenceSet={lowConfidenceSet}
            onBuild={() => void build(false)}
          />

          <CachedAnchorsList
            cached={cached}
            activeEntry={activeEntry}
            activeHops={activeHops}
            onPick={onPickCached}
            onDelete={(row) => void onDeleteCached(row)}
          />

          <TraceStatusFooter
            status={status}
            error={statusError}
            loading={statusLoading}
            appId={appId}
          />
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// TraceResultRegion — switches on the ``useTraceAnchor`` state and renders
// the right empty-state / loading / error / loaded UI. Lifted out of the
// main component so the JSX is scannable.
// ---------------------------------------------------------------------------

type ResultProps = {
  state: ReturnType<typeof useTraceAnchor>["state"];
  appId: string;
  lowConfidenceSet: ReadonlySet<number>;
  onBuild: () => void;
};

function TraceResultRegion({ state, appId, lowConfidenceSet, onBuild }: ResultProps) {
  // Decision timeline collapse state. Lives at the parent level (rather
  // than inside ``DecisionTimeline``) so the toggle can sit next to the
  // section header — matches the chevron-before-title pattern the
  // HookBuilder / AdbShell / Chat sections use, and keeps the section
  // chrome (count, future actions) co-located with the toggle.
  const [decisionsCollapsed, setDecisionsCollapsed] = useState(false);

  if (state.kind === "idle") {
    return (
      <p className="trace-empty muted small">
        Expand <strong>Browse classes</strong> above to pick a trace entry from
        the call-graph closure, or open <strong>Advanced</strong> to type a
        Smali signature directly. The cached anchors below show what's already
        been traced for this app.
      </p>
    );
  }
  if (state.kind === "loading") {
    return <p className="trace-empty muted small">Loading cached trace…</p>;
  }
  if (state.kind === "missing") {
    return (
      <div className="trace-empty trace-empty-missing">
        <p>
          This entry hasn't been traced yet. Click <strong>Build trace</strong> to
          run the <code>trace_behavior</code> skill — it walks the call-graph
          closure, classifies every gate, and asks the LLM to refine
          low-confidence verdicts.
        </p>
        <button type="button" onClick={onBuild}>Build trace</button>
      </div>
    );
  }
  if (state.kind === "building") {
    return (
      <p className="trace-empty muted small">
        Building trace — this fires one LLM call per anchor and may take
        a few seconds. The cache is updated atomically once the
        <code> trace_behavior</code> skill returns.
      </p>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="trace-empty trace-error">
        <p>
          <strong>{state.phase === "build" ? "Build failed" : "Cache lookup failed"}</strong>
          {" "}— {state.status ? `${state.status}: ` : ""}{state.error}
        </p>
        <button type="button" onClick={onBuild}>Retry as Build</button>
      </div>
    );
  }

  const { anchor, from } = state;
  return (
    <div className="trace-result">
      <BehaviorAnchorCard anchor={anchor} source={from} />

      <section className="trace-section">
        <header className="trace-section-head">
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={() => setDecisionsCollapsed((c) => !c)}
            aria-expanded={!decisionsCollapsed}
            aria-controls="trace-decision-timeline-body"
            aria-label={
              decisionsCollapsed
                ? "Expand decision timeline"
                : "Collapse decision timeline"
            }
            title={
              decisionsCollapsed
                ? "Expand decision timeline"
                : "Collapse decision timeline"
            }
          >
            {decisionsCollapsed ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
          </button>
          <h3>Decision timeline ({anchor.decisions.length})</h3>
        </header>
        {!decisionsCollapsed && (
          <div id="trace-decision-timeline-body">
            <DecisionTimeline
              decisions={anchor.decisions}
              lowConfidenceIndices={lowConfidenceSet}
              appId={appId}
            />
          </div>
        )}
      </section>

      <section className="trace-section">
        <h3>Bypass plans ({anchor.plans.length})</h3>
        {anchor.plans.length === 0 ? (
          <p className="muted small">
            No deterministic plans synthesised at the configured risk
            threshold. Try the advanced plans below if any, or refine the
            heuristic verdicts manually via Manual Hooks mode.
          </p>
        ) : (
          <div className="trace-bypass-plan-list">
            {anchor.plans.map((p, i) => (
              <BypassPlanCard key={`${p.template_id}-${i}`} plan={p} />
            ))}
          </div>
        )}
        {anchor.advanced_plans.length > 0 && (
          <details className="trace-advanced-plans">
            <summary>
              Advanced ({anchor.advanced_plans.length} higher-risk plan
              {anchor.advanced_plans.length === 1 ? "" : "s"})
            </summary>
            <div className="trace-bypass-plan-list">
              {anchor.advanced_plans.map((p, i) => (
                <BypassPlanCard key={`adv-${p.template_id}-${i}`} plan={p} />
              ))}
            </div>
          </details>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MethodPicker — surfaces method/overload candidates on the class the
// operator is currently typing. Activates whenever the entry input is a
// class-prefix-only string (``Lcom/.../Foo;->[partial]``) and reads from
// ``GET /api/graph/{app_id}/methods``. Closes the operator-visible
// workflow gap when the Inspect → Trace seed couldn't pin a method
// (most ``findViewById`` candidates) — clicking a row fills the entry
// with the full Smali signature and auto-fires the trace.
// ---------------------------------------------------------------------------

type PickerProps = {
  smaliClass: string;
  methodPrefix: string;
  methods: GraphNode[] | null;
  total: number;
  loading: boolean;
  error: string | null;
  onPick: (smaliId: string) => void;
};

function MethodPicker({
  smaliClass,
  methodPrefix,
  methods,
  total,
  loading,
  error,
  onPick,
}: PickerProps) {
  const className = smaliClass.slice(1, -1).replace(/\//g, ".");
  const filterLabel = methodPrefix ? ` matching "${methodPrefix}*"` : "";

  return (
    <section className="trace-method-picker" aria-label="Method picker">
      <header className="trace-method-picker-head">
        <h3>
          Methods on <code>{className}</code>
          {filterLabel}
        </h3>
        {loading && <span className="muted small">loading…</span>}
        {!loading && methods && (
          <span className="muted small">
            {total === 0
              ? "no methods found"
              : total > methods.length
                ? `${methods.length} of ${total} (narrow with name prefix)`
                : `${methods.length} method${methods.length === 1 ? "" : "s"}`}
          </span>
        )}
      </header>
      {error && (
        <p className="muted small" style={{ color: "var(--err)" }}>
          {error}
        </p>
      )}
      {!loading && methods && methods.length === 0 && !error && (
        <p className="muted small">
          {total === 0
            ? `No methods on this class in the call graph. Either the class name is mistyped, or the call graph hasn't indexed this class yet (try Force re-trace's sibling Rebuild on the Graph mode).`
            : "No methods match the prefix; try a shorter prefix or clear it."}
        </p>
      )}
      {methods && methods.length > 0 && (
        <ul className="trace-method-picker-list">
          {methods.map((m) => {
            const params = m.param_types.join(", ");
            return (
              <li key={m.smali_id}>
                <button
                  type="button"
                  className="trace-method-picker-row"
                  onClick={() => onPick(m.smali_id)}
                  title={`Use ${m.smali_id} as the entry method`}
                >
                  <code className="trace-method-picker-name">
                    {m.method_name}
                    <span className="muted">({params})</span>
                    <span className="muted">: {m.return_type}</span>
                  </code>
                  {m.is_static && <span className="trace-method-picker-tag">static</span>}
                  {m.is_abstract && <span className="trace-method-picker-tag">abstract</span>}
                  {m.is_constructor && <span className="trace-method-picker-tag">ctor</span>}
                  {m.may_have_unresolved_reflection && (
                    <span
                      className="trace-method-picker-tag trace-method-picker-tag-warn"
                      title="This method has unresolved reflection — trace may be incomplete"
                    >
                      reflective
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// EntryValidationPill — Phase 11 v2.1 sub-step v2.1.2.
// Renders the inline ✓ / ⚠ / ✗ / "validation unavailable" pill that
// reflects the debounced ``POST /api/trace/{app_id}/normalise-entry``
// call. Sits below the inline-row form (Hops + Advanced toggle) and
// above the MethodPicker so it's visible regardless of which entry-
// discovery path the operator is using (Browse-tree click, Advanced
// raw-Smali typing, or cross-tab seed).
//
// Pill states:
//
//   * Loading        — spinner + "validating…". Renders during the
//                      400ms debounce window + the in-flight backend
//                      call. Operator sees a clear "we're checking"
//                      signal so a slow validation doesn't read as a
//                      stale ✓ from the prior input.
//   * ✓ valid class  — class exists in the call graph; pill shows the
//                      normalised Smali class + method count so the
//                      operator sees both what AndroScan parsed their
//                      input as AND how many methods the picker would
//                      surface.
//   * ⚠ not in graph — input parsed cleanly but no matching class in
//                      the call graph. v2.1.3's "Find similar
//                      classes" button (next sub-step) will hang off
//                      this state via a sibling pill.
//   * ✗ parse error  — coalescer returned 422; pill carries the
//                      operator-readable reason (e.g. "no class name
//                      found — expected an UpperCamelCase segment").
//   * — unavailable  — 404 / 409 / network / 5xx; pill renders
//                      neutrally with the underlying status string in
//                      the title-tooltip. Operator can still proceed
//                      via Browse / Advanced — we don't want a
//                      transient backend hiccup to look like a bad
//                      input.
// ---------------------------------------------------------------------------

type EntryValidationPillProps = {
  /** The trimmed entry input. The pill renders nothing when this is
   *  empty (so a blank Trace pane doesn't show stale validation). */
  entry: string;
  /** ``true`` while a debounce timer is pending OR a fetch is in
   *  flight. Either way the operator sees the spinner. */
  loading: boolean;
  /** ``null`` until the first response arrives for the current
   *  ``entry``; one of the discriminated kinds afterwards. */
  result: CoalescerResult | null;
};

function EntryValidationPill({ entry, loading, result }: EntryValidationPillProps) {
  if (!entry) return null;
  if (loading) {
    return (
      <div
        className="trace-entry-validation-pill trace-entry-validation-pill-loading"
        role="status"
        aria-live="polite"
      >
        <span className="trace-entry-spinner" aria-hidden="true" />
        <span className="trace-entry-validation-pill-text">validating…</span>
      </div>
    );
  }
  if (!result) return null;
  if (result.kind === "ok") {
    const { normalised_entry, smali_class, class_exists_in_graph, method_count } =
      result.data;
    if (class_exists_in_graph) {
      return (
        <div
          className="trace-entry-validation-pill trace-entry-validation-pill-ok"
          role="status"
          aria-live="polite"
          title={normalised_entry ?? smali_class ?? "valid entry"}
        >
          <span className="trace-entry-validation-pill-icon">✓</span>
          <span className="trace-entry-validation-pill-text">
            <code>{smali_class}</code>{" "}
            <span className="muted">
              · {method_count} method{method_count === 1 ? "" : "s"}
            </span>
          </span>
        </div>
      );
    }
    return (
      <div
        className="trace-entry-validation-pill trace-entry-validation-pill-warn"
        role="status"
        aria-live="polite"
        title={`${smali_class ?? entry} — class not found in call graph`}
      >
        <span className="trace-entry-validation-pill-icon">⚠</span>
        <span className="trace-entry-validation-pill-text">
          <code>{smali_class}</code> — class not found in call graph
        </span>
      </div>
    );
  }
  if (result.kind === "parse_error") {
    return (
      <div
        className="trace-entry-validation-pill trace-entry-validation-pill-err"
        role="status"
        aria-live="polite"
        title={result.detail}
      >
        <span className="trace-entry-validation-pill-icon">✗</span>
        <span className="trace-entry-validation-pill-text">
          couldn't parse — {result.detail}
        </span>
      </div>
    );
  }
  // ``unavailable`` — neutral copy + tooltip carries the underlying
  // status code so an operator hunting an outage has a hint.
  return (
    <div
      className="trace-entry-validation-pill trace-entry-validation-pill-neutral"
      role="status"
      aria-live="polite"
      title={`validation unavailable: ${result.status || "network"} — ${result.detail}`}
    >
      <span className="trace-entry-validation-pill-icon">—</span>
      <span className="trace-entry-validation-pill-text">
        validation unavailable
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CachedAnchorsList — small picker so the operator can flip between
// previously-traced anchors without re-typing the smali signature.
// ---------------------------------------------------------------------------

type CachedProps = {
  cached: TraceAnchorRow[];
  activeEntry: string | null;
  activeHops: number;
  onPick: (row: TraceAnchorRow) => void;
  onDelete: (row: TraceAnchorRow) => void;
};

function CachedAnchorsList({ cached, activeEntry, activeHops, onPick, onDelete }: CachedProps) {
  if (cached.length === 0) return null;
  return (
    <section className="trace-cached-anchors">
      <h3>Cached anchors ({cached.length})</h3>
      <ul>
        {cached.map((row) => {
          const isActive =
            row.entry_smali_id === activeEntry && row.hops === activeHops;
          return (
            <li key={`${row.entry_smali_id}#${row.hops}`} className={isActive ? "trace-cached-anchor-active" : ""}>
              <button
                type="button"
                className="trace-cached-anchor-pick"
                onClick={() => onPick(row)}
                title="Load this cached anchor"
              >
                <code>{row.entry_smali_id}</code>
                <span className="muted small">
                  hops={row.hops} · {new Date(row.created_at * 1000).toLocaleString()}
                </span>
              </button>
              <button
                type="button"
                className="trace-cached-anchor-delete"
                onClick={() => onDelete(row)}
                title="Delete this cached anchor"
                aria-label="Delete cached anchor"
              >
                ×
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

// ---------------------------------------------------------------------------
// TraceStatusFooter — same status-card the 10.6 placeholder shipped,
// trimmed to a footer strip now that the headline UI sits above it.
// ---------------------------------------------------------------------------

type StatusProps = {
  status: TraceStatusPayload | null;
  error: string | null;
  loading: boolean;
  appId: string;
};

function TraceStatusFooter({ status, error, loading, appId }: StatusProps) {
  return (
    <footer className="lab-trace-status">
      <h3>Cache status</h3>
      {loading && (
        <p className="muted small">Loading <code>/api/trace/{appId}/status</code>…</p>
      )}
      {error && (
        <p className="muted small" style={{ color: "var(--accent)" }}>{error}</p>
      )}
      {status && (
        <dl className="lab-trace-status-grid">
          <dt>Decompile</dt>
          <dd>{status.decompile_status}</dd>
          <dt>Call graph</dt>
          <dd>{status.call_graph.status}</dd>
          <dt>Trace cache</dt>
          <dd>{status.trace_cache.status}</dd>
          <dt>Cached anchors</dt>
          <dd>{status.trace_cache.anchor_count ?? 0}</dd>
          {status.trace_cache.error && (
            <>
              <dt>Cache error</dt>
              <dd style={{ color: "var(--accent)" }}>{status.trace_cache.error}</dd>
            </>
          )}
        </dl>
      )}
    </footer>
  );
}
