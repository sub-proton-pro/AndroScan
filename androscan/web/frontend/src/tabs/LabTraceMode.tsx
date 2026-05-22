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
 *   5. Result region: ``BehaviorAnchorCard`` header + ``BehaviorTrace``
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
 * **v2.1.8 — Trace mode now carries its own ``ChatDock``** (right-side
 * collapsible pane, mirrors the Manual Hooks chat-panel pattern).
 * Closes the v2.1.4 / 10.8 collision: 10.8 deliberately removed the
 * chat dock from Trace mode (operators were supposed to switch to
 * Manual Hooks for chat), but v2.1.4's "Ask AI" button assumed a
 * ``ChatDock tab="lab"`` was reachable from Trace mode — so clicking
 * the button silently set ``pendingChatPrefill`` with no consumer
 * mounted to receive it. v2.1.8 wraps the trace content in a
 * horizontal :class:`PanelGroup` (``autoSaveId="lab-trace-h"``) with
 * the existing trace surface on the left and a collapsible
 * ``ChatDock`` on the right; both Manual Hooks and Trace modes share
 * ``WorkbenchContext.chats["lab"]`` (they're never co-mounted, so
 * there's no rendering conflict, and the unified history reads
 * naturally as one Lab conversation rather than per-mode scratch
 * surfaces). The ``onActiveAnchorChange`` callback to the parent
 * ``LabTab`` is preserved so Manual Hooks's chat-attachment builder
 * still folds the active anchor into its own ``trace`` attachment
 * when the operator mode-hops over to Manual Hooks.
 *
 * **v2.1.9 — AppPicker placement migration (closes v2.1.8 visual-
 * stacking regression).** v2.1.8 also bundled a per-Lab-mode
 * ``<AppPicker />`` UX request — Trace's was added to the
 * ``<header className="pane-head">`` via ``<span className=
 * "pane-head-actions">``; Manual Hooks + Graph got new
 * ``<header className="lab-mode-head">`` strips. Operator dogfood
 * caught that Manual Hooks's strip stacked vertically directly under
 * the global header's AppPicker (two identical "Select project…"
 * dropdowns on consecutive lines), which read as a layout bug
 * rather than a deliberate redundancy. v2.1.9 closes the
 * regression by surfacing the picker **only in the empty-state
 * when ``appId`` is null** — in Trace mode the picker now lives
 * inline with the empty-state CTA card (replacing the prior
 * one-line ``<p className="muted small">No app selected — pick a
 * project from the dropdown above"</p>``); the picker disappears
 * entirely once an app is selected and operators rely on the
 * global header for mid-session project switching. Symmetric
 * change in ``LabTab.ManualHooksMode``. Graph mode keeps its
 * v2.1.8 ``.lab-mode-head`` strip per operator scope choice on
 * the v2.1.9 design questionnaire (the single-pane CallGraphView
 * has no non-empty body to render when ``appId`` is null, so the
 * ergonomics differ from Trace / Manual Hooks).
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
 *
 * **v2.1.3 — Tier-1 "Find similar classes" suggestions on the ⚠
 * validation-pill state**:
 *
 *   * When the v2.1.2 pill renders ⚠ (input parsed cleanly but the
 *     class isn't in the call graph — typo, wrong package, stale
 *     class name from a crash report), an inline "Find similar
 *     classes" button grows next to the pill.
 *   * Click → fires ``POST /api/trace/{app_id}/suggest-similar-classes``
 *     (a deliberate operator action, NOT debounced — we want the
 *     suggestion list to materialise within one tap of the click).
 *   * Backend fuzzy-matches the operator's typed class name against
 *     the call graph's ``classes.simple_name`` column via
 *     :func:`difflib.SequenceMatcher` and returns up to 5 candidates
 *     with ratio >= 0.6 (no LLM in v2.1.3; the LLM-fallback path
 *     ships in v2.1.5 wired through the same endpoint).
 *   * Candidates render as clickable suggestion pills below the
 *     validation pill (``trace-similar-classes-list``); each pill
 *     shows ``simple_name`` (bold lead) + ``package`` (muted trail)
 *     + a confidence-based opacity cue.
 *   * Click a candidate → seeds ``entryDraft`` with the candidate's
 *     Smali class-prefix (``Lcom/example/MainActivity;->``) → the
 *     v2.1.2 coalescer auto-re-fires (entryDraft change triggers
 *     the debounced effect) → MethodPicker activates because the
 *     new entry is a class-prefix → operator picks an overload →
 *     trace fires via the existing 11.2 auto-fire path.
 *   * **No auto-fire on Tier-1 candidate click** (per spec) — the
 *     click is "I pick this class", not "I pick this entry method";
 *     the MethodPicker is still the explicit trace-fire step.
 *   * Suggestion state clears on ``entryDraft`` change (so a stale
 *     candidate list from a previous typo doesn't linger after the
 *     operator types something new).
 *
 * **v2.1.4 — Tier-2(a) "Ask AI" button + chat-dock prefill**
 * (with **v2.1.7 patch** for the empty-entry case — see end of section):
 *
 *   * A small "Ask AI" button lives in the inline-row form alongside
 *     the Hops field and Advanced toggle (always visible — works
 *     regardless of whether Advanced is expanded).
 *   * Click → writes ``pendingChatPrefill = {tab: "lab", message:
 *     <prompt>}`` to ``WorkbenchContext`` (timestamp-stamped via the
 *     setter, mirrors the existing ``pendingTraceEntry`` /
 *     ``pendingHookPrefill`` re-fire semantics).
 *   * The chat dock observes ``pendingChatPrefill`` and writes the
 *     message into its ``draft`` state + focuses the textarea +
 *     clears the pending state. The parent component ALSO observes
 *     it and calls ``chatRef.current?.expand()`` to ensure the dock
 *     is visible (a prefill into a collapsed dock is invisible).
 *   * **v2.1.8 collision fix:** the ``chatRef.expand()`` consumer
 *     was originally only wired in ``LabTab.ManualHooksMode``; when
 *     the operator was on Trace mode, ``ManualHooksMode`` was
 *     unmounted and the prefill went unconsumed (no chat dock to
 *     expand, no observable feedback). v2.1.8 ships a parallel
 *     ``chatRef.expand()`` effect inside ``LabTraceMode`` itself
 *     (alongside the ``ChatDock`` it now carries) so Ask AI works
 *     in-place from Trace mode without the operator having to
 *     mode-hop to Manual Hooks.
 *   * **v2.1.7 patch — free-form prompt template:** the button is
 *     always enabled (originally v2.1.4 disabled it when the Smali
 *     entry field was empty, which collided with v2.1.1's hide-
 *     Smali-by-default decision and made the rescue rope unreachable
 *     for the operators who most needed it). Two prompt templates:
 *     - non-empty entry: ``I want to trace `<entry>`. What entry
 *       methods should I consider?`` (original v2.1.4 template).
 *     - empty entry: ``I'm looking for a trace entry method in this
 *       app. `` (open invitation — operator continues typing in the
 *       chat textarea before sending).
 *   * NO new skill ships in v2.1.4 / v2.1.7. The pre-filled prompt
 *     leverages the existing tier-3 skills
 *     (``search_decompiled_sources`` for RAG over decompiled
 *     sources + ``query_call_graph`` for call-graph context + the
 *     v2.1.5 ``suggest_trace_entry`` skill which surfaces top-3
 *     candidates as ``<TraceEntryCandidateWidget>`` cards via the
 *     chat-widget pattern) — the LLM agentic loop picks them up
 *     automatically based on the prompt's intent.
 *     v2.1.5 adds a dedicated ``suggest_trace_entry`` skill, but
 *     v2.1.4 ships chat-only ahead of that.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ImperativePanelHandle,
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from "react-resizable-panels";
import { IconChevronDown, IconChevronUp } from "../components/Icons";
import { AppPicker } from "../components/AppPicker";
import { ChatDock } from "../components/ChatDock";
import { BehaviorAnchorCard } from "../components/trace/BehaviorAnchorCard";
import { BypassPlanCard } from "../components/trace/BypassPlanCard";
import { BehaviorTrace } from "../components/trace/BehaviorTrace";
import { ExecutionFlowV3 } from "../components/trace/ExecutionFlowV3";
import { Inspector } from "../components/trace/Inspector";
import {
  TraceModeToggle,
  initialTraceMode,
  type TraceMode,
} from "../components/trace/TraceModeToggle";
import {
  closureMethodCount,
  type ExecutionFlowV3Node,
} from "../components/trace/executionFlowGraphV3";
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
  suggestSimilarClasses,
  useDynamicTrace,
  useTraceAnchor,
  type BehaviorAnchor,
  type NormaliseEntryResponse,
  type SimilarClassCandidate,
  type TraceAnchorRow,
  type TraceStatusPayload,
} from "../api/trace";
import type { ChatAttachment } from "../types";
import { useWorkbench } from "../context/WorkbenchContext";
import {
  classNameToJavaRelPath,
  javaRelPathToSmaliMethodPrefix,
} from "../util/smaliClassToFile";
import { renderTraceAttachment } from "../util/traceChatAttachment";

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
  const {
    pendingTraceEntry,
    setPendingTraceEntry,
    pendingChatPrefill,
    setPendingChatPrefill,
    dossier,
    bumpTraceCacheVersion,
  } = useWorkbench();

  // v2.1.8 — chat dock right-pane handle + collapsed state. Mirrors
  // the Manual Hooks chat-panel pattern; ``react-resizable-panels``
  // persists the size + collapsed state via the ``autoSaveId`` on
  // the parent :class:`PanelGroup` so the operator's last layout
  // survives reloads + mode-hops.
  const chatRef = useRef<ImperativePanelHandle>(null);
  const [chatCollapsed, setChatCollapsed] = useState(false);

  // v2.1.8 — expand the chat panel whenever a lab-tab prefill
  // arrives via ``pendingChatPrefill`` (typically from this mode's
  // own "Ask AI" button, but also fires for any other surface
  // writing ``tab: "lab"`` — none today; future-proof). The prefill
  // ITSELF is consumed inside :class:`ChatDock` (writes the message
  // into ``draft`` and clears the pending state); this effect owns
  // the panel-expand half because the imperative
  // ``chatRef.expand()`` lives in this layer. Both consumers run in
  // the same commit cycle and close over the original pre-clear
  // value, so order doesn't matter — see WorkbenchContext.tsx's
  // ``pendingChatPrefill`` doc-block for the two-consumer rationale.
  //
  // Parallel effect lives in ``LabTab.ManualHooksMode`` for the
  // mode-hopped case (operator clicks Ask AI in Trace, then switches
  // to Manual Hooks before the prefill is consumed — Manual Hooks's
  // mount-time effect catches the still-non-null prefill and expands
  // its own chat panel). The two effects are mutually exclusive
  // (only one mode is mounted at a time) so they never race.
  useEffect(() => {
    if (!pendingChatPrefill) return;
    if (pendingChatPrefill.tab !== "lab") return;
    chatRef.current?.expand();
    // ``setPendingChatPrefill`` is intentionally NOT called here —
    // ChatDock is the canonical clearer (it owns the textarea +
    // the consumed state); this effect's job is purely to make
    // the dock visible. The dep on ``pendingChatPrefill?.ts``
    // re-fires the expand on every fresh prefill (re-click of
    // "Ask AI"), which is idempotent on an already-expanded panel.
  }, [pendingChatPrefill?.ts]);

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
    setSimilarLoading(false);
    setSimilarCandidates(null);
    setSimilarError(null);
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
  //
  // v2.1.10 — also bump the context-level ``traceCacheVersion`` so
  // the ``LabTab.ManualHooksMode`` overlay re-fetches its
  // ``anchoredMethods`` map. v2.1.10 always-mounts both Lab modes
  // simultaneously (so Trace state survives mode-hops); the previous
  // unmount-on-switch design relied on the remount to re-fire the
  // overlay fetch — see the ``traceCacheVersion`` doc-block in
  // WorkbenchContext.tsx for the full rationale.
  useEffect(() => {
    if (state.kind === "loaded" && state.from === "build") {
      setCachedReloadTick((t) => t + 1);
      bumpTraceCacheVersion();
    }
  }, [state, bumpTraceCacheVersion]);

  const lowConfidenceSet = useMemo(() => {
    if (state.kind !== "loaded") return new Set<number>();
    return new Set(state.anchor.low_confidence_decision_indices);
  }, [state]);

  // v2.1.8 — chat attachments + "show context" summary for the
  // embedded :class:`ChatDock`. Smaller surface than Manual Hooks's
  // builder (no selected method / decompiled source / Frida session
  // — those are Manual Hooks concerns) — Trace mode contributes the
  // active app + the active behaviour anchor (when one is loaded).
  // Re-uses the shared :func:`renderTraceAttachment` so the ``trace``
  // attachment shape is byte-identical to what Manual Hooks would
  // surface for the same anchor (LLM sees the same thing regardless
  // of which mode triggered the chat).
  const activeAnchorForChat: BehaviorAnchor | null =
    state.kind === "loaded" ? state.anchor : null;

  const traceChatAttachments = useMemo<ChatAttachment[]>(() => {
    const out: ChatAttachment[] = [];
    if (appId) {
      out.push({ kind: "default", name: "selection", text: `app_id: ${appId}` });
    }
    if (activeAnchorForChat) {
      out.push({
        kind: "trace",
        name:
          activeAnchorForChat.entry_method.class_name +
          "." +
          activeAnchorForChat.entry_method.method_name,
        text: renderTraceAttachment(activeAnchorForChat),
      });
    }
    return out;
  }, [appId, activeAnchorForChat]);

  const traceChatContextSummary = useMemo<string>(() => {
    const lines: string[] = [];
    if (appId) {
      lines.push(`Active app: ${appId}`);
    } else {
      lines.push("Active app: — (pick a project from the dropdown above).");
    }
    lines.push("");
    if (activeAnchorForChat) {
      const entry = activeAnchorForChat.entry_method;
      lines.push(
        `Active behaviour trace: ${entry.class_name}.${entry.method_name} ` +
          `· hops=${activeAnchorForChat.hops} · ${activeAnchorForChat.decisions.length} ` +
          `decision(s) · ${activeAnchorForChat.plans.length} plan(s) ` +
          `(+${activeAnchorForChat.advanced_plans.length} advanced)`,
      );
      lines.push(
        "Trace attachment: included (entry header + decision timeline + " +
          "top bypass plans, capped at 6,000 chars).",
      );
    } else {
      lines.push(
        "Active behaviour trace: — (use Browse classes / Advanced / " +
          "Cached anchors above to load one; the LLM will then see the " +
          "decision timeline and top plans).",
      );
    }
    lines.push("");
    lines.push(
      "Tip: click ✨ Ask AI on the controls row to seed a starter prompt " +
        "asking about candidate trace entry methods — useful when you " +
        "don't yet know which class to trace.",
    );
    return lines.join("\n");
  }, [appId, activeAnchorForChat]);

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

  // v2.1.3 similar-classes state. Triggered explicitly by the
  // operator clicking the "Find similar classes" button on the ⚠
  // validation pill (NOT a debounced effect — see the v2.1.3 doc
  // block on the component for the rationale). Cleared on
  // ``entryDraft`` change so a stale candidate list doesn't
  // linger after the operator starts typing again.
  const [similarLoading, setSimilarLoading] = useState(false);
  const [similarCandidates, setSimilarCandidates] = useState<
    SimilarClassCandidate[] | null
  >(null);
  const [similarError, setSimilarError] = useState<string | null>(null);

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

  // v2.1.3 — clear the Tier-1 similar-classes suggestion list
  // whenever the operator's entry changes. Without this, a stale
  // candidate list from the previous typo would still hang under
  // the validation pill after the operator typed a corrected name
  // (or seeded a fresh entry from Browse / cross-tab handoff).
  // Intentionally NOT debounced — the suggestions are only fetched
  // on the explicit "Find similar classes" button click, so the
  // clear must fire on the keystroke (not on the debounced
  // settle) to keep the UI honest.
  useEffect(() => {
    setSimilarLoading(false);
    setSimilarCandidates(null);
    setSimilarError(null);
  }, [trimmedEntry]);

  // v2.1.3 — explicit-click handler for the "Find similar classes"
  // button on the ⚠ validation pill. POSTs the operator's typed
  // input to the v2.1.3 suggestion endpoint and renders the
  // returned candidates as clickable pills. NOT debounced — this
  // is a deliberate operator action.
  const onFindSimilarClasses = useCallback(async () => {
    if (!appId || !trimmedEntry || similarLoading) return;
    setSimilarLoading(true);
    setSimilarError(null);
    setSimilarCandidates(null);
    const r = await suggestSimilarClasses(appId, trimmedEntry);
    setSimilarLoading(false);
    if (r.ok) {
      setSimilarCandidates(r.data.candidates);
    } else {
      setSimilarCandidates([]);
      setSimilarError(
        r.status > 0
          ? `Suggestion lookup failed (${r.status}): ${r.error}`
          : `Suggestion lookup failed: ${r.error}`,
      );
    }
  }, [appId, trimmedEntry, similarLoading]);

  // v2.1.3 — operator picked one of the fuzzy / LLM candidates.
  // Seed entryDraft with ``<smali_class>->`` (class-prefix shape that
  // activates the v1 MethodPicker) — this triggers the v2.1.2
  // coalescer auto-re-fire (entryDraft change → debounced effect)
  // and clears the picker's previous selection so the new class's
  // overload list materialises. NO auto-fire on the trace itself
  // (per spec — operator picks an overload via the picker, which
  // is the explicit Trace step).
  const onPickSimilarClass = useCallback((c: SimilarClassCandidate) => {
    const seed = `${c.smali_class}->`;
    setEntryDraft(seed);
    setSeedLabel(null);
    // Don't activate the trace here — let the picker handle it
    // via the existing 11.2 auto-fire on overload-pick path.
  }, []);

  // v2.1.4 — Tier-2(a) "Ask AI" handler. Writes a pre-filled prompt
  // to ``pendingChatPrefill`` so the chat dock pops open (LabTab
  // expands the panel) with a textarea seeded with a natural-
  // language starter the operator can edit / extend before sending.
  //
  // **v2.1.7 patch:** the handler now produces a free-form prompt
  // that works with OR without text in the (hidden-by-default per
  // v2.1.1) Smali entry field. The original v2.1.4 implementation
  // gated the button on ``trimmedEntry`` being non-empty, which
  // collided with v2.1.1's hide-Smali-by-default decision: the
  // operators who most need Ask AI (the ones who don't know the
  // class) couldn't reach it because the entry field they were
  // supposed to type into was hidden behind the Advanced toggle.
  // The fix aligns the button's behaviour with its stated purpose
  // (Tier-2(a) rescue rope for "I don't know what class this lives
  // in"). When ``trimmedEntry`` is empty, the prefill is an open
  // invitation ("I'm looking for a trace entry method. ") that the
  // operator continues typing in the chat textarea — same UX as
  // typing into the chat dock directly, just one click closer.
  const onAskAI = useCallback(() => {
    const message = trimmedEntry
      ? `I want to trace \`${trimmedEntry}\`. What entry methods should I consider?`
      : `I'm looking for a trace entry method in this app. `;
    setPendingChatPrefill({
      tab: "lab",
      message,
      sourceLabel: "Trace mode → entry suggestions",
    });
  }, [trimmedEntry, setPendingChatPrefill]);

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
      // v2.1.10 — see the post-build effect above for the rationale.
      // Manual Hooks's anchoredMethods overlay reads from the same
      // cache; a delete removes glyphs that the overlay was carrying,
      // so the consumer needs the same refresh signal.
      bumpTraceCacheVersion();
    }
  };

  return (
    <PanelGroup direction="horizontal" autoSaveId="lab-trace-h" className="lab-trace-shell">
      <Panel defaultSize={70} minSize={40} className="panel">
        <div className="lab-trace-mode pane-scroll">
          <header className="pane-head">
            <h2>Behavior Trace</h2>
            <span className="muted small">
              UI element ➜ decision points ➜ bypass plans
            </span>
          </header>

          {/* v2.1.9 — empty-state CTA card surfacing the AppPicker
              inline with the "No app selected" message. v2.1.8
              originally placed the picker in the pane-head's
              right-side actions slot, which stacked vertically
              under the global header's AppPicker (visible directly
              above the mode pane) and read as a layout bug rather
              than a deliberate redundancy. v2.1.9 surfaces the
              picker only when it's contextually useful (operator has
              no app selected and needs to pick one); when an
              ``appId`` is set, the in-pane picker disappears and
              operators rely on the global header for mid-session
              switching. Symmetric with Manual Hooks mode's empty-
              state card; Graph mode keeps its v2.1.8 ``.lab-mode-head``
              strip per operator scope choice on the v2.1.9 design
              questionnaire (single-pane CallGraphView with no
              non-empty body to render when ``appId`` is null —
              different ergonomics than the Trace / Manual Hooks
              cases). */}
          {!appId && (
            <div className="lab-empty-state" role="status" aria-live="polite">
              <div className="lab-empty-state-card">
                <h3 className="lab-empty-state-title">No app selected</h3>
                <p className="lab-empty-state-body">
                  Pick a project below to start tracing behaviour. The
                  same dropdown lives in the top-right header — both
                  surfaces are kept in sync.
                </p>
                <AppPicker />
              </div>
            </div>
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
            {/* v2.1.4 — Tier-2(a) "Ask AI" button. Pre-fills the chat
                dock with a natural-language starter the operator can
                edit / extend, then expands the dock (parent LabTab
                observes ``pendingChatPrefill`` and calls
                ``chatRef.current?.expand()``). v2.1.7 patch: always
                enabled — when the entry is empty the prefill is an
                open invitation ("I'm looking for a trace entry
                method.") rather than a degenerate "I want to trace
                ``" template. See ``onAskAI`` handler comment for the
                v2.1.1 / v2.1.4 collision the patch resolves. */}
            <button
              type="button"
              className="trace-ask-ai-button"
              onClick={onAskAI}
              title={
                trimmedEntry
                  ? "Open the chat dock with a pre-filled prompt asking about entry methods related to your typed input"
                  : "Open the chat dock to describe what you want to trace — the LLM will suggest candidate entry methods"
              }
              aria-label="Ask AI for entry-method suggestions"
            >
              <span className="trace-ask-ai-icon" aria-hidden="true">✨</span>
              <span className="trace-ask-ai-label">Ask AI</span>
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
            similarLoading={similarLoading}
            similarCount={similarCandidates?.length ?? null}
            onFindSimilar={onFindSimilarClasses}
          />

          {/* v2.1.3 — Tier-1 fuzzy / LLM suggestion list. Renders
              under the validation pill when the operator has clicked
              "Find similar classes" on the ⚠ state. The pill itself
              owns the button + spinner; this region renders only
              the result-list (or its loading / empty / error
              states). */}
          <SimilarClassesList
            loading={similarLoading}
            candidates={similarCandidates}
            error={similarError}
            onPick={onPickSimilarClass}
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
      </Panel>
      <PanelResizeHandle className="resize-h" />
      {/* v2.1.8 — collapsible chat dock right pane. Mirrors the
          ``ChatDock`` panel pattern from ``LabTab.ManualHooksMode``;
          ``react-resizable-panels`` persists size + collapsed state
          via the parent ``autoSaveId="lab-trace-h"``. When collapsed,
          renders the same right-rail "Chat" sentinel button operators
          recognise from the Manual Hooks layout. */}
      <Panel
        ref={chatRef}
        defaultSize={30}
        minSize={12}
        collapsible
        collapsedSize={3}
        onCollapse={() => setChatCollapsed(true)}
        onExpand={() => setChatCollapsed(false)}
        className="panel chat-panel"
      >
        {chatCollapsed ? (
          <button
            type="button"
            className="sidebar-rail rail-right"
            onClick={() => chatRef.current?.expand()}
            title="Expand chat dock"
            aria-label="Expand chat dock"
          >
            <span className="sidebar-rail-chevron" aria-hidden="true">
              <IconChevronUp />
            </span>
            <span className="sidebar-rail-label">Chat</span>
          </button>
        ) : (
          <ChatDock
            tab="lab"
            attachments={traceChatAttachments}
            contextSummary={traceChatContextSummary}
            onCollapse={() => chatRef.current?.collapse()}
          />
        )}
      </Panel>
    </PanelGroup>
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
  // v3.X-next.2 / DEC-031 N7 — source-line pill click handler.
  // Mirrors the Inspector's "Open source" path: writes
  // ``pendingCodeNav`` with the node's class / method / rel-path
  // (derived via :func:`classNameToJavaRelPath`) + switches the
  // top-level tab to Inspect so the Code Browser actually mounts
  // to consume the nav. The handler is declared once at the
  // ``TraceResultRegion`` scope so a stable identity passes
  // through ``ExecutionFlowV3``'s ``useMemo`` deps without
  // triggering needless graph rebuilds on parent re-render.
  const { setPendingCodeNav, setTab } = useWorkbench();
  const onSourceLineClick = useCallback(
    (target: { className: string; methodName: string; sourceLine: number }) => {
      if (!appId) return;
      setPendingCodeNav({
        appId,
        relPath: classNameToJavaRelPath(target.className),
        className: target.className,
        method: target.methodName,
      });
      setTab("inspect");
    },
    [appId, setPendingCodeNav, setTab],
  );

  // Behavior Trace collapse state (legacy alias: "decision timeline").
  // Lives at the parent level (rather than inside ``BehaviorTrace``)
  // so the toggle can sit next to the section
  // header — matches the chevron-before-title pattern the HookBuilder
  // / AdbShell / Chat sections use, and keeps the section chrome
  // (count, future actions) co-located with the toggle. Variable name
  // kept verbatim from v2.1 so the surrounding state-machine reads
  // unchanged; semantic meaning is "Behavior Trace section collapsed?"
  // under the 13.5 rebrand.
  const [decisionsCollapsed, setDecisionsCollapsed] = useState(false);
  // Phase 13 sub-step 13.6 — Execution Flow flowchart collapse state.
  // Defaults open so the operator sees the new visual surface on
  // first paint; collapses with the same chevron pattern as the
  // Behavior Trace list below it. 13.7's Inspector pane lives inside
  // this section (right-side fixed-width pane); 13.8 adds the
  // Static / Dynamic / Both mode toggle + live-value chips.
  const [executionFlowCollapsed, setExecutionFlowCollapsed] = useState(false);
  // v3.X-next.2 — V3 is now the **production** renderer (no more
  // ``?flow=v3`` URL gate). The two URL escape hatches DEC-030 Q5
  // / Q6 locked still survive as the operator-power-user overrides
  // against the emitter's v3.1 defaults (``hideRetPills=true`` /
  // ``gatesOnly=true``):
  //
  //   * ``?pills=show``     — restore the per-branch ``Ret: X``
  //                           return-pill terminals (the v3.0
  //                           shape; cleaner side-by-side
  //                           comparison with the verdict-summary
  //                           chip during operator triage).
  //   * ``?methods=all``    — drop the framework-package filter
  //                           (``kotlin.*`` / ``androidx.*`` /
  //                           ``java.*``); useful when the
  //                           operator is hunting for a synthetic
  //                           accessor / kotlin getter that the
  //                           default filter hid.
  //
  // ``hideRetPills`` / ``gatesOnly`` pass through as ``undefined``
  // unless the operator explicitly flips the param, in which case
  // V3's emitter consumes the override. v3.X-next.2.0 ratified
  // these as the long-term operator-visible knobs (no v3.X-next.3
  // re-shape planned).
  const v3Overrides = useMemo(() => {
    if (typeof window === "undefined") {
      return {
        hideRetPills: undefined as boolean | undefined,
        gatesOnly: undefined as boolean | undefined,
      };
    }
    const params = new URLSearchParams(window.location.search);
    const pillsParam = params.get("pills");
    const methodsParam = params.get("methods");
    return {
      hideRetPills:
        pillsParam === "show"
          ? false
          : pillsParam === "hide"
            ? true
            : undefined,
      gatesOnly:
        methodsParam === "all"
          ? false
          : methodsParam === "gates-only"
            ? true
            : undefined,
    };
  }, []);
  // Phase 13 sub-step 13.6 / 13.7 — selected ExecutionFlow node,
  // lifted to this level so 13.7's Inspector pane (sibling of
  // ``ExecutionFlow``) can read the same selection without prop-
  // drilling and without re-running the graph-build helper. We
  // store the full ``ExecutionFlowNode`` (not just the id) so the
  // Inspector gets ``overloadCount`` + ``possiblyInlined`` + the
  // synthetic-sink guard fields directly from the graph layer.
  const [selectedFlowNode, setSelectedFlowNode] =
    useState<ExecutionFlowV3Node | null>(null);

  // Phase 13 sub-step 13.8 — dynamic-trace lifecycle owned by the
  // shared :func:`useDynamicTrace` hook. The hook's state drives
  // (a) the fired-edge / fired-node accent in ``ExecutionFlow``, (b)
  // the per-method live-value chips on edges + depth pills on
  // nodes, (c) the Inspector's Summary section + Live observation
  // panel, and (d) the Run / Stop button + status copy below the
  // section header. The hook owns its WebSocket lifecycle; we just
  // call ``start()`` / ``stop()`` from the button handlers and read
  // ``state`` for the read-only views.
  const dynamic = useDynamicTrace(appId);

  // Phase 13 sub-step 13.8 — overlay mode (Static / Dynamic / Both).
  // The default is computed against ``hasDynamicData`` (set once
  // any entry event lands) per DEC-029's locked auto-default rule —
  // see :func:`initialTraceMode` for the lookup priority
  // (localStorage → hasDynamicData → "static").
  const hasDynamicData = dynamic.state.firedMethods.size > 0;
  const [mode, setMode] = useState<TraceMode>(() =>
    initialTraceMode(appId, hasDynamicData),
  );

  // Re-evaluate the initial mode when the active app changes —
  // a different app has its own localStorage key and its own
  // hasDynamicData (which resets to false because the hook clears
  // its state on appId change too).
  useEffect(() => {
    setMode(initialTraceMode(appId, false));
  }, [appId]);

  // Clear the Inspector selection whenever the active anchor changes
  // (operator seeds a new entry, runs a new build, or the anchor's
  // method set changes underneath us). Without this, a stale
  // ``selectedFlowNode.id`` would carry over and the Inspector would
  // try to resolve a method that no longer exists in the new
  // ``BehaviorAnchor`` — :func:`Inspector.resolveSelection` returns
  // ``null`` in that case, but the empty-state UX is cleaner than
  // showing the empty placeholder under a "selected" header.
  //
  // Phase 13.8 — the same guard ALSO resets the dynamic-trace
  // accumulators (firedMethods / liveValues / summaries) by
  // calling ``dynamic.reset()``. The previous anchor's runtime
  // state shouldn't bleed into a different anchor's overlay (the
  // overload keys could even collide across anchors for
  // unrelated methods that happen to share a class+method
  // namespace).
  const anchorKey =
    state.kind === "loaded"
      ? `${state.anchor.entry_method.class_name}#${state.anchor.entry_method.method_name}#${state.anchor.hops}`
      : null;
  useEffect(() => {
    setSelectedFlowNode(null);
    dynamic.reset();
    // ``dynamic.reset`` is a stable callback per the hook's
    // useCallback definition; intentional partial deps to avoid
    // re-firing on every state change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorKey]);

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

      {/* Phase 13 sub-step 13.6 / 13.7 / 13.8 — Execution Flow
          flowchart + Inspector + dynamic-trace overlay surface. New
          primary visual surface for the active anchor; sits above
          the (legacy-shaped) Behavior Trace list during the 13.6 →
          13.10 build-out so operators can dogfood the flowchart
          alongside the familiar linear list. 13.8 added: mode toggle
          (Static / Dynamic / Both), Run / Stop dynamic-trace button
          in the section head, fired-edge / fired-node accent
          rendering, per-method live values + LLM summaries fed by
          the dynamic-trace WebSocket. */}
      <section className="trace-section">
        <header className="trace-section-head">
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={() => setExecutionFlowCollapsed((c) => !c)}
            aria-expanded={!executionFlowCollapsed}
            aria-controls="execution-flow-body"
            aria-label={
              executionFlowCollapsed
                ? "Expand execution flow"
                : "Collapse execution flow"
            }
            title={
              executionFlowCollapsed
                ? "Expand execution flow"
                : "Collapse execution flow"
            }
          >
            {executionFlowCollapsed ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
          </button>
          <h3>
            Execution Flow{" "}
            <span className="muted small">
              ({anchor.decisions.length} decision
              {anchor.decisions.length === 1 ? "" : "s"})
            </span>
          </h3>
          {/* 13.8 — section-head right cluster. Mode toggle pills +
              Run / Stop dynamic-trace button + live status copy
              when a session is active. Run button is the basic
              v1; threshold-based color coding lands in 13.9. */}
          <div className="trace-section-head-actions">
            <TraceModeToggle
              appId={appId}
              hasDynamicData={hasDynamicData}
              mode={mode}
              onModeChange={setMode}
            />
            <DynamicTraceRunControl
              dynamic={dynamic}
              anchor={anchor}
            />
          </div>
        </header>
        {!executionFlowCollapsed && (
          <div id="execution-flow-body" className="execution-flow-row">
            <ExecutionFlowV3
              anchor={anchor}
              selectedNodeId={selectedFlowNode?.id ?? null}
              onNodeClick={(node) => setSelectedFlowNode(node)}
              hideRetPills={v3Overrides.hideRetPills}
              gatesOnly={v3Overrides.gatesOnly}
              mode={mode}
              firedMethods={dynamic.state.firedMethods}
              liveValues={dynamic.state.liveValues}
              hookFailed={dynamic.state.hookFailed}
              onSourceLineClick={onSourceLineClick}
            />
            <Inspector
              anchor={anchor}
              selectedNodeId={selectedFlowNode?.id ?? null}
              selectedNodeData={selectedFlowNode}
              appId={appId}
              onClear={() => setSelectedFlowNode(null)}
              summaries={dynamic.state.summaries}
              liveValues={dynamic.state.liveValues}
              mode={mode}
              hookFailed={dynamic.state.hookFailed}
            />
          </div>
        )}
      </section>

      <section className="trace-section">
        <header className="trace-section-head">
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={() => setDecisionsCollapsed((c) => !c)}
            aria-expanded={!decisionsCollapsed}
            aria-controls="behavior-trace-body"
            aria-label={decisionsCollapsed ? "Expand behavior trace" : "Collapse behavior trace"}
            title={decisionsCollapsed ? "Expand behavior trace" : "Collapse behavior trace"}
          >
            {decisionsCollapsed ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
          </button>
          <h3>
            Behavior Trace ({anchor.decisions.length})
          </h3>
        </header>
        {!decisionsCollapsed && (
          <div id="behavior-trace-body">
            <BehaviorTrace
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
// DynamicTraceRunControl — Phase 13 sub-step 13.8 (introduced) +
// 13.9 (threshold-colored Run button per DEC-029 lock-in).
//
// Compact Run / Stop control + live status copy that lives in the
// Execution Flow section header. Wraps the :func:`useDynamicTrace`
// hook's ``start()`` / ``stop()`` actions and renders the
// connection state (idle / starting / running / stopping /
// disconnected / error) as operator-readable copy.
//
// Wire shape: the button posts the active anchor's
// ``entry_method.smali_signature`` + ``hops`` to
// ``POST /api/trace/{app_id}/dynamic`` (Phase 13.2). The BE looks
// up the cached ``BehaviorAnchor`` by ``(entry, hops)``, extracts
// the closure, and spins up a Frida session. The hook then opens
// the multiplexed ``/ws/trace/{app_id}/{session_id}`` WebSocket
// (Phase 13.3) and starts streaming events.
//
// 13.9 — threshold-color coding on the idle / running button.
// DEC-029 locks four bands keyed on the hook count:
//
//   * ≤ 20 hooks → green (lightweight; the device should feel
//     responsive even on a budget Android Go target).
//   * 21-50 hooks → yellow (acceptable; mid-range devices may
//     show a brief startup hitch but no perceived lag).
//   * 51-100 hooks → orange (operator-visible warning band; tooltip
//     copy "device may stutter" appears here and above).
//   * > 100 hooks → red (heavy; the BE caps at the operator-tunable
//     ``hop_cap`` default of 50, so this band only reaches red when
//     the operator has explicitly raised the cap — but the visual
//     warning still fires regardless).
//
// Pre-run (no ``readyStats`` yet) the count comes from
// :func:`closureMethodCount` against the loaded ``BehaviorAnchor``
// (same five-source flatten the BE's
// :func:`extract_closure_methods` performs); post-run it comes from
// ``state.readyStats.methods_attempted`` (the actual count the BE
// attempted to hook, post cap-truncation). Switching mid-flight
// keeps the operator-visible color stable as the trace progresses
// from "intent" to "actual".
// ---------------------------------------------------------------------------

/** Threshold band classifier — pure helper so unit tests can pin
 *  the boundary semantics if 13.x ever needs them. The four bands
 *  match DEC-029's locked ladder; values are inclusive of the
 *  upper bound of the lower band (≤ 20, ≤ 50, ≤ 100, > 100). */
type ThresholdBand = "green" | "yellow" | "orange" | "red";

function thresholdBand(hookCount: number): ThresholdBand {
  if (hookCount <= 20) return "green";
  if (hookCount <= 50) return "yellow";
  if (hookCount <= 100) return "orange";
  return "red";
}

/** Operator-readable tooltip copy for a given hook count + band.
 *  Green / yellow keep the descriptive copy; orange / red prepend
 *  the "device may stutter" warning DEC-029 specced. */
function thresholdTooltip(hookCount: number): string {
  const band = thresholdBand(hookCount);
  const summary = `${hookCount} hook${hookCount === 1 ? "" : "s"}`;
  if (band === "orange" || band === "red") {
    return `${summary} · device may stutter — Frida overhead grows with the hook count`;
  }
  return `${summary} · within the comfortable performance band for most devices`;
}

type DynamicTraceRunControlProps = {
  dynamic: ReturnType<typeof useDynamicTrace>;
  anchor: BehaviorAnchor;
};

function DynamicTraceRunControl({ dynamic, anchor }: DynamicTraceRunControlProps) {
  const { state, start, stop, reset } = dynamic;
  // Build the entry-method Smali signature from the anchor's
  // entry_method MethodRef. The BE's ``StartDynamicTraceBody.entry``
  // accepts the same signature shape ``trace_cache.read_anchor``
  // looks up under, so this is byte-equal to what the cache POST
  // path already wrote (the anchor we're rendering). Synthesised
  // inline to avoid a cross-module import for ``MethodRef`` →
  // smali-signature; mirrors :func:`Inspector.methodSig`.
  const entry = useMemo(() => {
    const m = anchor.entry_method;
    const cn = (m.class_name || "").replace(/\./g, "/");
    return `L${cn};->${m.method_name}(${(m.param_descriptors || []).join("")})${m.return_descriptor || "V"}`;
  }, [anchor]);

  // 13.9 — threshold-color hook count. Pre-run we use the static
  // closure count (mirrors BE's ``extract_closure_methods``);
  // post-run (after the ``ready`` event) we use the actual attempted
  // count from the BE. The value is stable through the
  // starting / running transition because both pre- and post- counts
  // map the same anchor to the same closure (modulo the ``hop_cap``
  // truncation, which the BE applies AFTER the static count — so
  // the post-run value can be ≤ pre-run, never greater).
  const closureSize = useMemo(() => closureMethodCount(anchor), [anchor]);
  const hookCount = state.readyStats?.methods_attempted ?? closureSize;
  const band = thresholdBand(hookCount);
  const tooltipBase = thresholdTooltip(hookCount);

  const onRun = useCallback(() => {
    void start({ entry, hops: anchor.hops });
  }, [start, entry, anchor.hops]);

  const onStop = useCallback(() => {
    void stop();
  }, [stop]);

  const isRunning = state.connection === "running";
  const isBusy = state.connection === "starting" || state.connection === "stopping";

  // v3.X-next.6 — operator-recovery affordances on top of Run / Stop.
  // The Stop happy path deliberately preserves runtime overlays
  // (``firedMethods`` / ``liveValues`` / ``summaries`` / ``hookFailed``)
  // so the operator can post-mortem-inspect a stopped trace. That
  // posture leaves no in-UI path to recover from "I mis-clicked in
  // the Android app mid-trace" — pre-v3.X-next.6 the only options
  // were anchor-switch (heavy-handed, fires the ``anchorKey`` reset
  // useEffect in :func:`LabTraceMode`) or full page reload.
  //
  // **Restart** (primary recovery; visible whenever there's runtime
  // state OR an active session): one-click ``stop → start(prev)``.
  // The hook's ``start()`` already wipes state to
  // ``INITIAL_DYNAMIC_TRACE_STATE`` before re-arming, so no explicit
  // ``reset()`` is needed in between — calling ``start()`` after
  // ``stop()`` lands a clean slate by itself.
  //
  // **Clear overlay** (secondary; visible only when there's runtime
  // state AND the session is not running): pure local-state wipe via
  // the hook's ``reset()``. Doesn't touch the BE — the BE's
  // session-timeout GC catches any abandoned session (matches the
  // ``useDynamicTrace`` unmount cleanup posture). Hidden during
  // ``running`` because clearing while events stream in would just
  // re-populate immediately (misleading affordance).
  //
  // Both buttons disable while ``isBusy`` (``starting`` /
  // ``stopping``) so the operator can't queue a Restart on top of an
  // in-flight transition. Empty-overlay edge case (no fires, no
  // summaries, no hookFailed) hides both buttons so the Run / Stop
  // control stays uncluttered when there's nothing worth restarting
  // or clearing.
  const hasRuntimeState =
    state.firedMethods.size > 0 ||
    state.liveValues.size > 0 ||
    state.summaries.size > 0 ||
    state.hookFailed.size > 0;
  const showRestart = (isRunning || hasRuntimeState) && !isBusy;
  const showClear = hasRuntimeState && !isRunning && !isBusy;

  const onRestart = useCallback(async () => {
    if (state.connection === "running") {
      await stop();
    }
    void start({ entry, hops: anchor.hops });
  }, [state.connection, stop, start, entry, anchor.hops]);

  const onClear = useCallback(() => {
    reset();
  }, [reset]);
  const showStop = isRunning || isBusy;
  const buttonLabel = (() => {
    if (state.connection === "starting") return "Starting…";
    if (state.connection === "stopping") return "Stopping…";
    if (isRunning) return "Stop dynamic trace";
    return `Run dynamic trace (${hookCount})`;
  })();

  const buttonTitle = (() => {
    if (isRunning) {
      return "Stop the active dynamic trace and detach the Frida session";
    }
    return `${tooltipBase}. Start a dynamic Frida trace on the closure of methods reachable from this anchor.`;
  })();

  const statusCopy = (() => {
    if (state.connection === "running" && state.readyStats) {
      const r = state.readyStats;
      return `${r.methods_hooked} hooked / ${r.methods_attempted} attempted${
        r.methods_failed > 0 ? ` · ${r.methods_failed} failed` : ""
      }`;
    }
    if (state.connection === "running") return "hooks installing…";
    if (state.connection === "disconnected") return "WebSocket dropped — re-run to resume";
    if (state.connection === "stopped") return "trace stopped";
    if (state.connection === "error") return state.error ?? "error";
    return null;
  })();

  // 13.9 — threshold-color class list. The base class lives in
  // App.css; the band-specific class adds the colour-tint overrides.
  // ``-running`` neutralises the band tint so a Stop button doesn't
  // shout the colour after the trace is up — the green / yellow /
  // orange / red signal is operator-targeting, "should I run this".
  // Once the trace is running the operator wants the calmer Stop
  // affordance. The disabled-when-busy posture is unchanged from
  // 13.8.
  const buttonClass = [
    "lab-dynamic-trace-button",
    !isRunning && !isBusy && `lab-dynamic-trace-button-${band}`,
    isRunning && "lab-dynamic-trace-button-running",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="lab-dynamic-trace-controls">
      <button
        type="button"
        className={buttonClass}
        onClick={showStop ? onStop : onRun}
        disabled={isBusy}
        title={buttonTitle}
      >
        {buttonLabel}
      </button>
      {showRestart && (
        <button
          type="button"
          className="lab-dynamic-trace-button lab-dynamic-trace-button-restart"
          onClick={() => void onRestart()}
          disabled={isBusy}
          title="Stop the current trace (if running), forget what fired, and re-run from scratch — useful if you mis-interacted with the Android app mid-trace"
        >
          Restart
        </button>
      )}
      {showClear && (
        <button
          type="button"
          className="lab-dynamic-trace-button lab-dynamic-trace-button-clear"
          onClick={onClear}
          disabled={isBusy}
          title="Forget what fired without re-running — clears fired-method accents, live-value chips, and per-method summaries from the overlay without touching the backend session"
        >
          Clear overlay
        </button>
      )}
      {statusCopy && (
        <span
          className={[
            "lab-dynamic-trace-status",
            state.connection === "error" && "lab-dynamic-trace-status-err",
            state.connection === "disconnected" && "lab-dynamic-trace-status-warn",
          ]
            .filter(Boolean)
            .join(" ")}
          role="status"
          aria-live="polite"
        >
          {statusCopy}
        </span>
      )}
      {state.dropCount > 0 && (
        <span
          className="lab-dynamic-trace-drops"
          title="The session's ring buffer overflowed; some events were dropped before they reached the UI"
        >
          {state.dropCount} drop{state.dropCount === 1 ? "" : "s"}
        </span>
      )}
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
//                      the call graph. v2.1.3 grows a "Find similar
//                      classes" button on the right of the pill — on
//                      click, the suggestion endpoint fuzzy-matches
//                      against the call graph's class list and the
//                      <SimilarClassesList> sibling renders the
//                      candidates as clickable pills.
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
  /** v2.1.3 — ``true`` while the "Find similar classes" suggestion
   *  fetch is in flight. The pill renders the button as a disabled
   *  spinner in this state. */
  similarLoading: boolean;
  /** v2.1.3 — number of suggestion candidates already fetched for
   *  the current entry, or ``null`` if the operator hasn't clicked
   *  the button yet. Used to label the button as "Find again" once
   *  the operator has fetched at least once for this entry. */
  similarCount: number | null;
  /** v2.1.3 — click handler for the "Find similar classes" button.
   *  Only wired when the pill renders the ⚠ state. */
  onFindSimilar: () => void;
};

function EntryValidationPill({
  entry,
  loading,
  result,
  similarLoading,
  similarCount,
  onFindSimilar,
}: EntryValidationPillProps) {
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
    // v2.1.3 — when the input parses cleanly but the class isn't
    // in the call graph, grow the "Find similar classes" button on
    // the right of the pill. Operator click → fuzzy / LLM
    // suggestion lookup → <SimilarClassesList> sibling renders the
    // candidates as clickable suggestion pills.
    const buttonLabel =
      similarCount !== null && !similarLoading ? "Find again" : "Find similar classes";
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
        <button
          type="button"
          className="trace-entry-validation-pill-action"
          onClick={onFindSimilar}
          disabled={similarLoading}
          title="Fuzzy-match against the call graph's class list"
        >
          {similarLoading ? (
            <>
              <span className="trace-entry-spinner" aria-hidden="true" />{" "}
              searching…
            </>
          ) : (
            buttonLabel
          )}
        </button>
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
// SimilarClassesList — Phase 11 v2.1 sub-step v2.1.3.
// Renders the Tier-1 fuzzy / LLM suggestion-candidate list under the
// validation pill. Visible only after the operator has clicked
// "Find similar classes" on the ⚠ pill (see <EntryValidationPill>).
//
// States:
//
//   * Loading — operator clicked the button; backend lookup in
//               flight. (The button itself shows a spinner; this
//               region renders nothing during loading to avoid
//               "ghost" placeholder pills flickering in.)
//   * Empty   — backend returned no candidates above the cutoff.
//               Shows operator-facing copy explaining the next step
//               (try Browse / Advanced).
//   * Error   — network / 404 / 5xx; shows the underlying detail
//               so the operator knows whether to retry.
//   * List    — N candidates; each renders as a clickable pill with
//               simple_name (lead) + package (muted trail) + a
//               confidence-based opacity cue. Click → seeds entryDraft
//               with the candidate's class-prefix → coalescer
//               auto-re-fires → MethodPicker activates.
// ---------------------------------------------------------------------------

type SimilarClassesListProps = {
  loading: boolean;
  candidates: SimilarClassCandidate[] | null;
  error: string | null;
  onPick: (c: SimilarClassCandidate) => void;
};

function SimilarClassesList({ loading, candidates, error, onPick }: SimilarClassesListProps) {
  if (loading) return null;
  if (candidates === null && !error) return null;
  if (error) {
    return (
      <div className="trace-similar-classes trace-similar-classes-err" role="status">
        {error}
      </div>
    );
  }
  if (candidates && candidates.length === 0) {
    return (
      <div className="trace-similar-classes trace-similar-classes-empty" role="status">
        No similar classes found. Try the Browse panel or paste a full Smali signature
        via the Advanced toggle.
      </div>
    );
  }
  return (
    <div className="trace-similar-classes" role="region" aria-label="Similar classes">
      <div className="trace-similar-classes-head">
        <span className="trace-similar-classes-title">Similar classes</span>
        <span className="muted small">
          ({candidates!.length}) · click to seed
        </span>
      </div>
      <ul className="trace-similar-classes-list">
        {candidates!.map((c) => {
          // Confidence-based opacity cue: 1.0 → fully opaque, 0.6 → 0.65.
          // Matches the linear ramp the operator sees on other
          // confidence-styled pills in the Trace pane.
          const opacity = 0.65 + 0.35 * Math.min(1, Math.max(0, (c.confidence - 0.6) / 0.4));
          return (
            <li key={c.smali_class}>
              <button
                type="button"
                className="trace-similar-classes-pill"
                onClick={() => onPick(c)}
                title={`${c.smali_class} — ${c.rationale}`}
                style={{ opacity }}
              >
                <span className="trace-similar-classes-simple">{c.simple_name}</span>
                <span className="trace-similar-classes-package muted small">
                  {c.package || "(no package)"}
                </span>
                <span className="trace-similar-classes-confidence muted small">
                  {(c.confidence * 100).toFixed(0)}%
                </span>
              </button>
            </li>
          );
        })}
      </ul>
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
