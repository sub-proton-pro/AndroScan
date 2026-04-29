/**
 * Hook Lab — HookBuilder pane.
 *
 * Renders, from left → right:
 *
 *   [ template picker ]
 *   [ auto-generated param form (text inputs from the picker's schema) ]
 *   [ debounced server-side render → Monaco read-only JS view + parse markers ]
 *   [ pentester-summary card ]
 *   [ Inject button row + status line ]
 *
 * The pentester summary is rendered verbatim from the server response;
 * the JS view always reflects the *server's* render (DEC-023's "Monaco
 * displays POST /render output" rule — clients can never smuggle hand-
 * edited JS to ``POST /sessions``).
 *
 * The Inject button is disabled when:
 *   - any required param is missing,
 *   - the latest render is in flight (we don't want a stale Inject), or
 *   - ``parse.ok`` is false **and** ``parse.available`` is true (when
 *     pyjsparser is unavailable we soften to a warning, per DEC-023's
 *     graceful-degradation policy).
 */
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor as MonacoEditorNS, IRange } from "monaco-editor";

import {
  createSession,
  listTemplates,
  renderTemplate,
  type CreateSessionResult,
  type FridaResult,
  type HookTemplate,
  type ParseInfo,
  type RenderResult,
} from "../api/frida";
import { listMethodsOnClass } from "../api/graph";
import { useWorkbench } from "../context/WorkbenchContext";
import { IconChevronDown, IconChevronUp } from "./Icons";

// Note on Monaco asset loading: ``@monaco-editor/react``'s default
// ``loader`` lazy-fetches the Monaco editor + workers from a jsdelivr
// CDN on first mount. That keeps our Vite bundle small (~1.1 MB
// total) but means the workbench needs internet on first use of the
// Hook Lab. Self-hosting is straightforward (``loader.config({paths:
// {vs: '/monaco/min/vs'}})`` + a postbuild step that copies the
// Monaco assets into ``../static/monaco``), but DEC-023 doesn't
// require it for v1 — flagged in ``KNOWN_ISSUES.md`` for later.

const PANEL_DESCRIPTION =
  "Pick a hook template, fill its parameters, review the rendered JS + summary, then Inject.";

const RENDER_DEBOUNCE_MS = 350;

// ---------------------------------------------------------------------------
// Return-value suggestions
//
// The ``force_return_value`` hook template carries a ``return_value_expr``
// param that is rendered into the Frida JS *as a raw expression* — the
// operator is responsible for picking a literal whose Java type is
// assignment-compatible with the target method's declared return type.
// Picking the wrong shape (``"true"`` for a method that returns
// ``java.lang.String``, or ``true`` instead of
// ``Java.use("java.lang.Boolean").$new(true)`` for a method that returns
// the boxed wrapper) shows up as a runtime ``ClassCastException`` only
// after Inject — not great for the usual flow where the operator picks a
// method node in the call graph and immediately wants to flip its
// outcome.
//
// The lookup runs against ``listMethodsOnClass`` whenever the form has
// both ``class_name`` and ``method_name`` filled; it returns the unique
// set of return types across overloads (rare for them to differ — same
// name typically means same return), and the suggestion table below
// translates each one into a small set of literal expressions the
// operator can click to populate the input. Static templates only — we
// deliberately don't query the LLM here because the latency would
// destroy the live-edit feel of the form, and the static table covers
// every Java type we've seen in the bypass planner's output (``Z``,
// ``I``, ``J``, ``Ljava/lang/String;``, the boxed wrappers, common
// reference types, void, arrays).

/** A pre-baked literal value to fill ``return_value_expr`` with, plus
 *  a one-line Java-type label that explains why the value fits. */
type ReturnSuggestion = {
  /** The literal JS expression that goes into ``return_value_expr``
   *  verbatim (the template renders it as raw JS inside ``Java.perform``). */
  value: string;
  /** Short, operator-facing description rendered as the chip's tooltip
   *  (e.g. ``"truthy boolean — flips boolean gates to ALLOW"``). */
  hint: string;
};

/** Suggestion bundle returned by :func:`suggestionsForReturnType` —
 *  wraps the chip list with a ``label`` (rendered as the
 *  "Return: ``<type>``" caption) and an optional ``note`` for the
 *  void / unknown / boxed-wrapper cases that need an explanation
 *  beyond the chip set itself. */
type SuggestionBundle = {
  label: string;
  values: ReturnSuggestion[];
  note?: string;
};

/** Map a Java return-type string (the form ``descriptor_to_java``
 *  produces and the call-graph stores in ``GraphNode.return_type``,
 *  e.g. ``"boolean"``, ``"java.lang.String"``, ``"int[]"``,
 *  ``"void"``) to the suggestion bundle the chip strip renders.
 *
 *  Returns ``null`` for type strings we don't have curated suggestions
 *  for; the chip strip then doesn't render at all. The "always works"
 *  fallback ``null`` literal is also surfaced for every reference type
 *  because returning ``null`` from a method that declares an object
 *  return is nearly always legal in Java (NPE risk shifts to the
 *  caller, which is exactly what bypass plans typically want — they
 *  flip a deny gate by neutering the source method). */
function suggestionsForReturnType(rt: string): SuggestionBundle | null {
  // Normalise generic-type junk we sometimes get from non-call-graph
  // sources (the call graph itself strips generics, but operators
  // pasting from javap output may include them). Generics don't
  // change the runtime type signature anyway.
  const t = rt.replace(/<[^>]*>/g, "").trim();
  if (!t) return null;

  // Arrays — treat ``T[]``, ``T[][]`` etc. uniformly. Frida marshals
  // JS arrays into Java arrays of the matching component type at the
  // implementation boundary, so ``[]`` and ``null`` are the universal
  // safe choices regardless of element type.
  if (t.endsWith("[]")) {
    return {
      label: t,
      values: [
        { value: "null", hint: "no return — most app code tolerates this for arrays" },
        { value: "[]", hint: "empty array — Frida marshals JS [] into a zero-length Java array" },
      ],
    };
  }

  // Primitives — rendered directly as JS literals; Frida unboxes them
  // into the matching Java primitive at the implementation boundary.
  switch (t) {
    case "boolean":
      return {
        label: "boolean",
        values: [
          { value: "true", hint: "truthy — flips boolean gates to the ALLOW side" },
          { value: "false", hint: "falsy — flips boolean gates to the DENY side" },
        ],
      };
    case "byte":
    case "short":
    case "int":
      return {
        label: t,
        values: [
          { value: "0", hint: "zero — common 'no error' / 'OK status' return" },
          { value: "1", hint: "one — common 'success' / 'true-ish' return" },
          { value: "-1", hint: "negative one — common 'error' / 'not found' sentinel" },
        ],
      };
    case "long":
      return {
        label: "long",
        values: [
          { value: "0", hint: "zero" },
          { value: "1", hint: "one" },
          { value: "-1", hint: "negative one" },
        ],
        // Frida accepts plain JS numbers for ``long`` only when the
        // value fits in a 53-bit double; for huge timestamps / hashes
        // operators should use the Int64 wrapper.
        note: "for values beyond 2^53 use Int64(\"...\")",
      };
    case "float":
    case "double":
      return {
        label: t,
        values: [
          { value: "0.0", hint: "zero" },
          { value: "1.0", hint: "one" },
          { value: "-1.0", hint: "negative one" },
        ],
      };
    case "char":
      return {
        label: "char",
        values: [
          { value: "0", hint: "null character — Frida casts JS number to Java char" },
          { value: "65", hint: "'A' (numeric form — Frida expects char-as-int)" },
        ],
      };
    case "void":
      return {
        label: "void",
        values: [],
        // Plain accurate note for any template that ever asks about a
        // void return type. The force_return_value-specific case is
        // handled by the warning banner inside the suggestion strip
        // (see ReturnValueSuggestionStrip), which pre-empts this note
        // with a one-click switch — but if a future template also
        // exposes a return-shaped param, this note is what they'll
        // see and it shouldn't lie about Frida's behaviour.
        note:
          "Frida can't attach a force_return_value-style hook to a void method " +
          "(it refuses an implementation that returns a value). Use force_method_skip " +
          "instead — it derives the empty return from the descriptor.",
      };
  }

  // Reference types — boxed primitives need ``Java.use(...).$new(...)``
  // because Frida can't auto-box a JS number into a Java Boolean
  // wrapper at the implementation boundary. Most app code that uses
  // boxed types is already null-tolerant (the boxing exists
  // specifically to allow null), so ``null`` is also surfaced.
  if (t === "java.lang.Boolean") {
    return {
      label: "java.lang.Boolean",
      values: [
        { value: "Java.use(\"java.lang.Boolean\").$new(true)", hint: "boxed true" },
        { value: "Java.use(\"java.lang.Boolean\").$new(false)", hint: "boxed false" },
        { value: "null", hint: "no value — forces caller to handle null" },
      ],
      note: "boxed wrapper — JS true/false won't auto-box at the Frida boundary",
    };
  }
  if (t === "java.lang.Integer") {
    return {
      label: "java.lang.Integer",
      values: [
        { value: "Java.use(\"java.lang.Integer\").$new(0)", hint: "boxed 0" },
        { value: "Java.use(\"java.lang.Integer\").$new(1)", hint: "boxed 1" },
        { value: "Java.use(\"java.lang.Integer\").$new(-1)", hint: "boxed -1" },
        { value: "null", hint: "no value" },
      ],
    };
  }
  if (t === "java.lang.Long") {
    return {
      label: "java.lang.Long",
      values: [
        { value: "Java.use(\"java.lang.Long\").$new(0)", hint: "boxed 0" },
        { value: "Java.use(\"java.lang.Long\").$new(1)", hint: "boxed 1" },
        { value: "null", hint: "no value" },
      ],
    };
  }
  if (
    t === "java.lang.Byte" ||
    t === "java.lang.Short" ||
    t === "java.lang.Float" ||
    t === "java.lang.Double" ||
    t === "java.lang.Character"
  ) {
    const inner = t.split(".").pop()!;
    return {
      label: t,
      values: [
        { value: `Java.use("${t}").$new(0)`, hint: `boxed ${inner.toLowerCase()} 0` },
        { value: "null", hint: "no value" },
      ],
    };
  }

  if (t === "java.lang.String") {
    return {
      label: "java.lang.String",
      values: [
        { value: "\"\"", hint: "empty string" },
        { value: "\"OK\"", hint: "common success sentinel" },
        { value: "\"FAIL\"", hint: "common failure sentinel" },
        { value: "null", hint: "no value — caller must handle null" },
      ],
    };
  }

  if (t === "java.lang.Object" || t === "java.lang.CharSequence") {
    return {
      label: t,
      values: [
        { value: "null", hint: "universal safe default for object returns" },
        { value: "\"OK\"", hint: "string literal — auto-coerces to Object/CharSequence" },
      ],
    };
  }

  // Generic reference type — ``null`` is the only universally-safe
  // default. We also emit a copy-pasteable ``Java.use(...).$new()``
  // template so the operator can spot the right ctor to fill in
  // (most app classes have a no-arg or single-arg ctor).
  return {
    label: t,
    values: [
      { value: "null", hint: "no value — works whenever the caller tolerates null" },
      {
        value: `Java.use("${t}").$new(/* args */)`,
        hint: "construct an instance — fill in ctor args inline",
      },
    ],
    note: "no-arg constructor may not exist; check the class for a usable ctor",
  };
}

const RETURN_TYPE_LOOKUP_DEBOUNCE_MS = 300;

// ---------------------------------------------------------------------------
// Param-label display rendering
//
// HookTemplateParam.name carries the snake_case identifier the JS
// template uses (``class_name``, ``method_name``, ``return_value_expr``,
// ``return_descriptor``, ``event_label``, ``target_literal``,
// ``key_prefix``). Rendering those verbatim as the form label leaks
// implementation jargon into the operator-facing UI; we surface them
// as sentence-cased "Class name" / "Method name" / etc. instead.
//
// Default rendering: ``snake_case`` → "Sentence case" via
// :func:`prettyParamName`. New templates' params get this for free.
//
// The override table handles params whose technical name doesn't make
// for a clean operator-facing label: ``return_value_expr`` becomes
// "Return value" rather than "Return value expr" because the "_expr"
// suffix is internal type information (the field IS an expression by
// type — there's no other shape it could take), not something the
// operator needs to see.

const PARAM_DISPLAY_OVERRIDES: Record<string, string> = {
  return_value_expr: "Return value",
};

function prettyParamName(name: string): string {
  if (name in PARAM_DISPLAY_OVERRIDES) return PARAM_DISPLAY_OVERRIDES[name];
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// Template ids referenced by the void-method auto-switch. Hard-coded
// rather than inferred because only these two templates participate
// in the swap today (and both are register-emitted by the bypass
// planner — adding new force_* variants would require revisiting the
// strip's branching anyway).
const TEMPLATE_FORCE_RETURN_VALUE = "force_return_value";
const TEMPLATE_FORCE_METHOD_SKIP = "force_method_skip";

type Props = {
  appId: string | null;
  /** When the operator picks a method node in the call graph, the
   *  parent (LabTab) passes the class name + method name down so
   *  we can prefill the matching params. */
  prefillClassName?: string | null;
  prefillMethodName?: string | null;
  /** Default target package (the app's manifest id from
   *  ``app_meta.json``). The session ``package`` field falls back to
   *  this when the operator hasn't widened the prefix in
   *  Settings → per-app → Hook. */
  defaultPackage: string | null;
  /** Called once a session has been successfully created so the
   *  parent can switch the right pane to the new trace. */
  onSessionCreated: (result: CreateSessionResult) => void;
  /** Optional collapse state. When ``collapsed`` is true the body
   *  (template form, Monaco view, summary, Inject row) is hidden and
   *  only the header bar remains so the parent ``Panel`` can be shrunk
   *  to a thin bar — mirrors the AdbShell / ScopedLogcat pattern. */
  collapsed?: boolean;
  onToggle?: () => void;
};

export function HookBuilder({
  appId,
  prefillClassName,
  prefillMethodName,
  defaultPackage,
  onSessionCreated,
  collapsed = false,
  onToggle,
}: Props) {
  const [templates, setTemplates] = useState<HookTemplate[] | null>(null);
  const [templatesError, setTemplatesError] = useState<string | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(null);
  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [packageOverride, setPackageOverride] = useState<string>("");

  const [render, setRender] = useState<RenderResult | null>(null);
  const [renderError, setRenderError] = useState<string | null>(null);
  const [renderInFlight, setRenderInFlight] = useState(false);

  const [injectInFlight, setInjectInFlight] = useState(false);
  const [injectError, setInjectError] = useState<string | null>(null);
  const [persist, setPersist] = useState(true);

  // Resolved return type(s) for the (class_name, method_name) the
  // operator filled in. ``null`` = no lookup attempted yet (template
  // doesn't have a ``return_value_expr`` param, or class/method
  // missing); ``[]`` = lookup ran but no matching method was found
  // in the call graph (suppress chips silently — could be an external
  // library method, an obfuscated name we couldn't resolve, or simply
  // a typo). Multiple distinct types can come back when a method
  // name has overloads with different return types — rare in
  // practice but handled.
  const [returnTypes, setReturnTypes] = useState<string[] | null>(null);

  // Phase 10 sub-step 10.7: when a Trace mode ``BypassPlanCard``
  // clicks "Stage in Manual Hooks" we land here with a populated
  // form. Surfacing the source label as a small pill on the header
  // lets the operator tell at a glance that the form was populated
  // by an external source rather than typed by hand. Cleared the
  // moment the operator picks a different template or edits a param.
  const [stagedSourceLabel, setStagedSourceLabel] = useState<string | null>(null);

  // ---- 1a. Consume cross-mode pendingHookPrefill -------------------------
  // BypassPlanCard writes a prefill into context (template id + params
  // dict + source label) and flips ``labMode`` to ``manual-hooks``;
  // we consume it on mount/change, hydrate the form, and clear via
  // ``setPendingHookPrefill(null)``. The clear-on-consume rule mirrors
  // ``pendingCodeNav`` so a stage→edit→stage-again sequence reliably
  // re-applies the prefill.
  //
  // The ref-based handoff is needed because changing
  // ``selectedTemplateId`` re-fires the template-change effect below,
  // which rebuilds ``paramValues`` from the template's declared
  // defaults — that would clobber the staged values. We stash the
  // staged params in a ref *before* setting the template id; the
  // template-change effect drains the ref into the rebuilt param dict
  // (overlaying staged values on top of declared defaults) and clears
  // the ref so subsequent unrelated template changes start clean.
  const { pendingHookPrefill, setPendingHookPrefill } = useWorkbench();
  const stagedParamsRef = useRef<Record<string, string> | null>(null);
  useEffect(() => {
    if (!pendingHookPrefill) return;
    if (pendingHookPrefill.appId && appId && pendingHookPrefill.appId !== appId) {
      // Stage was for a different app — drop it rather than clobber the
      // current form. Shouldn't happen in practice (Trace mode shows
      // the same appId) but defensive.
      setPendingHookPrefill(null);
      return;
    }
    stagedParamsRef.current = { ...pendingHookPrefill.params };
    setStagedSourceLabel(pendingHookPrefill.sourceLabel ?? null);
    setRender(null);
    setRenderError(null);
    if (pendingHookPrefill.templateId !== selectedTemplateId) {
      // Template-change effect will pick up stagedParamsRef.
      setSelectedTemplateId(pendingHookPrefill.templateId);
    } else {
      // Same template — apply the staged params directly since the
      // template-change effect won't fire.
      setParamValues((prev) => ({ ...prev, ...stagedParamsRef.current }));
      stagedParamsRef.current = null;
    }
    setPendingHookPrefill(null);
    // ``selectedTemplateId`` excluded from deps — including it would
    // re-fire the consumer on every template pick.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingHookPrefill, appId, setPendingHookPrefill]);

  // ---- 1. Fetch template catalog on mount -------------------------------

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const r = await listTemplates();
      if (cancelled) return;
      if (r.ok) {
        setTemplates(r.data.templates);
        if (r.data.templates.length > 0 && selectedTemplateId == null) {
          setSelectedTemplateId(r.data.templates[0].id);
        }
      } else {
        setTemplatesError(r.error);
      }
    })();
    return () => {
      cancelled = true;
    };
    // ``selectedTemplateId`` intentionally omitted from deps — we only
    // want to seed once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedTemplate = useMemo<HookTemplate | null>(() => {
    if (!templates || !selectedTemplateId) return null;
    return templates.find((t) => t.id === selectedTemplateId) ?? null;
  }, [templates, selectedTemplateId]);

  // Reset / re-seed param values whenever the template changes. We
  // honour three prefill sources, applied in increasing priority:
  //
  //   1. the param's declared default (lowest),
  //   2. ``class_name`` / ``method_name`` from the selected graph node,
  //   3. staged params from a Trace mode "Stage in Manual Hooks"
  //      (highest — overrides everything because the operator
  //      explicitly chose this plan).
  //
  // The graph-node prefill wins over declared defaults so picking a
  // node in the call graph re-targets the form even after the
  // operator typed something. Staged params win over the graph
  // prefill so Trace plans that target a *different* method than the
  // currently-selected graph node still land correctly.
  useEffect(() => {
    if (!selectedTemplate) {
      setParamValues({});
      return;
    }
    const next: Record<string, string> = {};
    for (const p of selectedTemplate.params) {
      next[p.name] = p.default ?? "";
    }
    if (prefillClassName && "class_name" in next) {
      next["class_name"] = prefillClassName;
    }
    if (prefillMethodName && "method_name" in next) {
      next["method_name"] = prefillMethodName;
    }
    if ("event_label" in next && !next["event_label"]) {
      // Sensible default operators almost always want.
      const label = prefillMethodName
        ? `${prefillMethodName}_trace`
        : `${selectedTemplate.id}_trace`;
      next["event_label"] = label;
    }
    // Drain staged params from Trace mode (overrides everything).
    const staged = stagedParamsRef.current;
    if (staged) {
      for (const [k, v] of Object.entries(staged)) {
        next[k] = v;
      }
      stagedParamsRef.current = null;
    }
    setParamValues(next);
    setRender(null);
    setRenderError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTemplateId, prefillClassName, prefillMethodName]);

  // ---- 2. Debounced render ---------------------------------------------

  const renderInputsKey = useMemo(() => {
    if (!selectedTemplate) return "";
    return `${selectedTemplate.id}\u0001${JSON.stringify(paramValues)}`;
  }, [selectedTemplate, paramValues]);

  const renderInflightKeyRef = useRef<string>("");

  useEffect(() => {
    if (!selectedTemplate) {
      setRender(null);
      setRenderError(null);
      return;
    }
    // Don't fire a render if any required field is empty — we'd just
    // get a 400 back. Instead we surface the missing-fields hint
    // locally; the Inject button stays disabled until the form is
    // complete, at which point this effect re-runs and the render
    // happens.
    const missing = selectedTemplate.params
      .filter((p) => p.required && !(paramValues[p.name] ?? "").trim())
      .map((p) => p.name);
    if (missing.length > 0) {
      setRender(null);
      setRenderError(null);
      return;
    }

    const myKey = renderInputsKey;
    renderInflightKeyRef.current = myKey;
    setRenderInFlight(true);
    const timer = window.setTimeout(async () => {
      const result = await renderTemplate(selectedTemplate.id, paramValues);
      // Drop stale results — a faster keystroke superseded us.
      if (renderInflightKeyRef.current !== myKey) return;
      setRenderInFlight(false);
      if (result.ok) {
        setRender(result.data);
        setRenderError(null);
      } else {
        setRender(null);
        setRenderError(result.error);
      }
    }, RENDER_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [renderInputsKey, selectedTemplate, paramValues]);

  // ---- 2b. Resolve return-type for ``return_value_expr`` suggestions ----
  //
  // Fires only for templates that actually expose a ``return_value_expr``
  // param (today: just ``force_return_value``); the chip strip is what
  // turns "the operator has to know off the top of their head whether
  // the target method returns ``boolean`` vs ``Boolean`` vs
  // ``java.lang.String``" into a one-click pick.
  //
  // The lookup goes through ``listMethodsOnClass`` rather than
  // re-parsing the call-graph DB on the client because:
  //
  //   * the route already lives in the same origin (no extra CORS /
  //     auth surface),
  //   * it accepts dotted Java class names directly so we can pass
  //     ``paramValues.class_name`` verbatim without converting to the
  //     Smali descriptor form, and
  //   * its ``namePrefix`` filter lets us narrow to the exact method
  //     server-side and we further filter to exact name matches on
  //     the client (the prefix matcher would otherwise pull in
  //     ``isLicensedV2`` when the operator typed ``isLicensed``).
  //
  // Stale-response handling mirrors the render effect: an inflight
  // ref tracks the latest query key; an out-of-order response gets
  // dropped before it can clobber the chip strip with stale types.
  const hasReturnValueExprParam = useMemo(
    () => selectedTemplate?.params.some((p) => p.name === "return_value_expr") ?? false,
    [selectedTemplate],
  );
  const returnTypeLookupKey = useMemo(() => {
    if (!hasReturnValueExprParam || !appId) return "";
    const cls = (paramValues["class_name"] ?? "").trim();
    const meth = (paramValues["method_name"] ?? "").trim();
    if (!cls || !meth) return "";
    return `${appId}\u0001${cls}\u0001${meth}`;
  }, [hasReturnValueExprParam, appId, paramValues]);
  const returnTypeInflightKeyRef = useRef<string>("");
  useEffect(() => {
    if (!returnTypeLookupKey) {
      setReturnTypes(null);
      return;
    }
    const myKey = returnTypeLookupKey;
    returnTypeInflightKeyRef.current = myKey;
    const cls = (paramValues["class_name"] ?? "").trim();
    const meth = (paramValues["method_name"] ?? "").trim();
    const timer = window.setTimeout(async () => {
      const r = await listMethodsOnClass(appId!, cls, {
        namePrefix: meth,
        limit: 50,
      });
      if (returnTypeInflightKeyRef.current !== myKey) return;
      if (!r.ok) {
        // Don't surface lookup errors as a separate UI element —
        // call-graph not built yet, class not in graph, etc. all
        // legitimately mean "no chips for now". The render-side
        // ``parse`` errors are the ones operators actually need
        // to see.
        setReturnTypes([]);
        return;
      }
      const exactMatches = r.data.methods.filter((m) => m.method_name === meth);
      const uniqueTypes = Array.from(
        new Set(exactMatches.map((m) => m.return_type).filter((t) => !!t)),
      );
      setReturnTypes(uniqueTypes);
    }, RETURN_TYPE_LOOKUP_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [returnTypeLookupKey, appId, paramValues]);

  // ---- 3. Param form helpers -------------------------------------------

  const onParamChange = useCallback((name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }));
    // Operator-edited the form — drop the "Staged from Trace" pill so
    // the badge doesn't lie about the form's provenance.
    setStagedSourceLabel(null);
  }, []);

  // ---- 3a. Auto-switch force_return_value → force_method_skip --------
  //
  // Surfaced by the ReturnValueSuggestionStrip when the call-graph
  // lookup says the target method returns void: force_return_value
  // physically can't attach (Frida refuses an implementation that
  // returns a value to a void Java method), so we offer a one-click
  // pivot to the only template that handles void cleanly.
  //
  // Carries forward class_name / method_name / event_label so the
  // operator doesn't retype anything, and seeds return_descriptor to
  // ``"V"`` (the JVM descriptor for void) so force_method_skip's
  // generated stub returns nothing — which is the whole point of the
  // switch. The handoff goes through stagedParamsRef + setSelectedTemplateId
  // exactly like the BypassPlanCard "Stage in Manual Hooks" flow, so
  // the template-change effect drains the ref into the new param dict
  // (overlaying the staged values on top of force_method_skip's
  // declared defaults) without an extra effect or race.
  const switchToMethodSkip = useCallback(() => {
    stagedParamsRef.current = {
      class_name: paramValues["class_name"] ?? "",
      method_name: paramValues["method_name"] ?? "",
      event_label: paramValues["event_label"] ?? "",
      return_descriptor: "V",
    };
    setStagedSourceLabel(
      "Auto-switched from force_return_value (target method returns void)",
    );
    setRender(null);
    setRenderError(null);
    setSelectedTemplateId(TEMPLATE_FORCE_METHOD_SKIP);
  }, [paramValues]);

  const onSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!appId || !selectedTemplate) return;
      const targetPackage = (packageOverride || defaultPackage || "").trim();
      if (!targetPackage) {
        setInjectError("No target package — set defaultPackage or the override field.");
        return;
      }
      setInjectInFlight(true);
      setInjectError(null);
      const result = await createSession({
        app_id: appId,
        package: targetPackage,
        template_id: selectedTemplate.id,
        params: paramValues,
        spawn: false,
        persist,
      });
      setInjectInFlight(false);
      if (result.ok) {
        onSessionCreated(result.data);
      } else {
        setInjectError(result.error);
      }
    },
    [
      appId,
      selectedTemplate,
      packageOverride,
      defaultPackage,
      paramValues,
      persist,
      onSessionCreated,
    ],
  );

  // ---- 4. Render --------------------------------------------------------

  const missingRequired = useMemo(() => {
    if (!selectedTemplate) return [] as string[];
    return selectedTemplate.params
      .filter((p) => p.required && !(paramValues[p.name] ?? "").trim())
      .map((p) => p.name);
  }, [selectedTemplate, paramValues]);

  const parse: ParseInfo | null = render?.parse ?? null;
  const injectDisabled =
    !appId ||
    !selectedTemplate ||
    !render ||
    renderInFlight ||
    injectInFlight ||
    missingRequired.length > 0 ||
    (parse?.available === true && parse?.ok === false);

  return (
    <div
      className={
        collapsed
          ? "pane-scroll hooklab-builder collapsed"
          : "pane-scroll hooklab-builder"
      }
    >
      <header className="pane-head">
        {onToggle && (
          <button
            type="button"
            className="logcat-toggle-btn"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? "Expand hook builder" : "Collapse hook builder"}
            title={collapsed ? "Expand hook builder" : "Collapse hook builder"}
          >
            {collapsed ? <IconChevronUp size={10} /> : <IconChevronDown size={10} />}
          </button>
        )}
        <h2>Hook Builder</h2>
        {!collapsed && stagedSourceLabel && (
          <span
            className="hookbuilder-staged-pill"
            title="This form was populated from a Trace mode bypass plan. Edit any field or pick a different template to dismiss this badge."
          >
            {stagedSourceLabel}
          </span>
        )}
        {!collapsed && (
          <span className="muted small">{PANEL_DESCRIPTION}</span>
        )}
      </header>

      {!collapsed && templatesError && (
        <p className="hook-error" role="alert">
          Failed to load templates: {templatesError}
        </p>
      )}

      {!collapsed && (
      <form onSubmit={onSubmit} className="hookbuilder-form">
        {/* Template picker */}
        <div className="hookbuilder-row">
          <label className="hookbuilder-label">Template</label>
          <select
            className="hookbuilder-select"
            value={selectedTemplateId ?? ""}
            onChange={(e) => {
              setSelectedTemplateId(e.target.value || null);
              setStagedSourceLabel(null);
            }}
            disabled={!templates || templates.length === 0}
          >
            {!templates && <option value="">loading…</option>}
            {templates && templates.length === 0 && (
              <option value="">(no templates registered)</option>
            )}
            {templates?.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} ({t.id})
              </option>
            ))}
          </select>
        </div>

        {selectedTemplate && (
          <p className="hookbuilder-desc muted small">{selectedTemplate.description}</p>
        )}

        {/* Param fields */}
        {selectedTemplate?.params.map((p) => {
          const value = paramValues[p.name] ?? "";
          const requiredAndMissing = p.required && !value.trim();
          // Type-aware suggestion strip is opt-in per param name;
          // currently only ``return_value_expr`` (the
          // ``force_return_value`` template). Wrapping the input in a
          // flex-stack so the chips sit immediately under it (still
          // aligned with the input column of the .hookbuilder-row
          // grid) keeps the form scan-readable when no chips are
          // present.
          const suggestRow =
            p.name === "return_value_expr" ? (
              <ReturnValueSuggestionStrip
                returnTypes={returnTypes}
                templateId={selectedTemplateId}
                onPick={(v) => onParamChange(p.name, v)}
                onSwitchToMethodSkip={switchToMethodSkip}
              />
            ) : null;
          // ``js_body`` (the Custom template's only param) is the one
          // field today that holds a multi-line code body — every
          // other param is a single identifier (class / method /
          // event_label / etc.). Render it as a tall monospace
          // ``<textarea>`` instead of the default ``<input>`` so the
          // operator can paste a real Frida script without it
          // collapsing to a single horizontally-scrolling line.
          // Pairs with ``.hookbuilder-row-textarea`` (top-aligns the
          // label) + ``.hookbuilder-textarea`` (mono font, vertical
          // resize, ~12 rows visible). Any future template that
          // ships a multi-line body just names its param ``js_body``
          // and gets the same treatment for free.
          const isCodeBody = p.name === "js_body";
          return (
            <div
              key={p.name}
              className={`hookbuilder-row ${isCodeBody ? "hookbuilder-row-textarea" : ""}`.trim()}
            >
              <label
                className="hookbuilder-label"
                htmlFor={`hookbuilder-param-${p.name}`}
                title={p.description}
              >
                {prettyParamName(p.name)}
                {p.required && <span className="hookbuilder-required"> *</span>}
              </label>
              <div className="hookbuilder-input-stack">
                {isCodeBody ? (
                  <textarea
                    id={`hookbuilder-param-${p.name}`}
                    className={`hookbuilder-input hookbuilder-textarea ${requiredAndMissing ? "hookbuilder-input-bad" : ""}`}
                    value={value}
                    placeholder={
                      // Multi-line placeholder so the empty state
                      // shows the operator a known-good shape they
                      // can use as a starting point. Single-quoted
                      // string literals to avoid colliding with the
                      // form's own JSX double quotes; Frida's JS
                      // engine accepts either.
                      "Java.perform(function () {\n" +
                      "  var Foo = Java.use('com.example.Foo');\n" +
                      "  Foo.bar.implementation = function () {\n" +
                      "    send({ class: 'Foo', method: 'bar', phase: 'forced' });\n" +
                      "    return true;\n" +
                      "  };\n" +
                      "});"
                    }
                    onChange={(e) => onParamChange(p.name, e.target.value)}
                    spellCheck={false}
                    autoComplete="off"
                    rows={12}
                    // ``off`` for both wrap + autocorrect: code
                    // bodies must not get soft-wrapped (it changes
                    // visual line numbers operators are matching to
                    // Monaco markers) and must never be
                    // autocorrected (variable names aren't English).
                    wrap="off"
                    autoCorrect="off"
                    autoCapitalize="off"
                  />
                ) : (
                  <input
                    id={`hookbuilder-param-${p.name}`}
                    className={`hookbuilder-input ${requiredAndMissing ? "hookbuilder-input-bad" : ""}`}
                    value={value}
                    placeholder={p.description}
                    onChange={(e) => onParamChange(p.name, e.target.value)}
                    spellCheck={false}
                    autoComplete="off"
                  />
                )}
                {suggestRow}
              </div>
            </div>
          );
        })}

        {/* Target package + persist toggle */}
        <div className="hookbuilder-row">
          <label className="hookbuilder-label" htmlFor="hookbuilder-package">
            Package
          </label>
          <input
            id="hookbuilder-package"
            className="hookbuilder-input"
            value={packageOverride}
            placeholder={defaultPackage ?? "com.example.app"}
            onChange={(e) => setPackageOverride(e.target.value)}
            spellCheck={false}
            autoComplete="off"
          />
        </div>
        <div className="hookbuilder-row hookbuilder-row-persist">
          <label className="hookbuilder-label" htmlFor="hookbuilder-persist">
            Persist
          </label>
          <span className="hookbuilder-persist">
            <input
              id="hookbuilder-persist"
              type="checkbox"
              checked={persist}
              onChange={(e) => setPersist(e.target.checked)}
            />
            <span className="muted small">
              {persist
                ? "writes JSONL trace to the run folder"
                : "no on-disk trace (live WS only)"}
            </span>
          </span>
        </div>

        {/* Sensitive APIs callout */}
        {selectedTemplate && selectedTemplate.sensitive_apis.length > 0 && (
          <div className="hookbuilder-callout">
            <strong className="muted small">Touches:</strong>{" "}
            {selectedTemplate.sensitive_apis.map((a, i) => (
              <code key={a}>
                {a}
                {i < selectedTemplate.sensitive_apis.length - 1 && ", "}
              </code>
            ))}
          </div>
        )}

        {/* Rendered JS via Monaco */}
        <div className="hookbuilder-monaco-wrap">
          <header className="hookbuilder-monaco-header">
            <span className="muted small">
              Rendered JS{renderInFlight ? " (refreshing…)" : ""}
            </span>
            {parse && (
              <span
                className={
                  parse.available
                    ? parse.ok
                      ? "hookbuilder-parse-ok"
                      : "hookbuilder-parse-bad"
                    : "muted small"
                }
              >
                {parse.available
                  ? parse.ok
                    ? "parse: ok"
                    : `parse: ${parse.error ?? "error"}${
                        parse.line ? ` (line ${parse.line})` : ""
                      }`
                  : "parse: pyjsparser not installed (warning only)"}
              </span>
            )}
          </header>
          <MonacoView
            value={render?.rendered.js ?? ""}
            parse={parse}
            placeholder={
              missingRequired.length > 0
                ? `// fill required field${missingRequired.length === 1 ? "" : "s"}: ${missingRequired.join(
                    ", ",
                  )}`
                : renderError
                  ? `// render failed: ${renderError}`
                  : "// pick a template to begin"
            }
          />
        </div>

        {/* Pentester summary */}
        {render?.rendered.summary && (
          <div className="hookbuilder-summary">
            <header>
              <strong>Pentester summary</strong>
              <span className="muted small">deterministic, derived from params</span>
            </header>
            <pre>{render.rendered.summary}</pre>
          </div>
        )}

        {/* Inject row */}
        <div className="hookbuilder-actions">
          <button
            type="submit"
            className="hookbuilder-inject"
            disabled={injectDisabled}
            title={
              injectDisabled
                ? missingRequired.length
                  ? `Fill required: ${missingRequired.join(", ")}`
                  : parse?.available && !parse?.ok
                    ? "Rendered JS failed to parse — fix params before Inject."
                    : "Render is loading…"
                : "Attach to the package and load the rendered script."
            }
          >
            {injectInFlight ? "Injecting…" : "Inject"}
          </button>
          {injectError && (
            <span className="hook-error" role="alert">
              {injectError}
            </span>
          )}
        </div>
      </form>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReturnValueSuggestionStrip — chip strip rendered under the
// ``return_value_expr`` input when the (class_name, method_name) pair
// resolves to one or more concrete return types via the call-graph
// lookup. Each chip is a literal expression the operator can click to
// populate the input verbatim.
//
// Visibility states:
//
//   * ``returnTypes === null`` — no lookup attempted yet (template
//     doesn't have a return_value_expr param, or class/method not
//     filled). Render nothing — the input behaves as a plain text
//     field.
//   * ``returnTypes === []`` — lookup ran but came up empty (call
//     graph not built, class/method not in graph, typo). Render a
//     small muted hint instead of an empty chip strip so the operator
//     understands the lookup did try and didn't crash silently.
//   * ``returnTypes.length === 1`` — common case: one return type,
//     one chip strip with its suggested literals.
//   * ``returnTypes.length > 1`` — overloads with distinct return
//     types. Render a separate strip for each, prefixed with the
//     type label, so the operator can see they're picking the right
//     overload's literal.

type StripProps = {
  returnTypes: string[] | null;
  /** Currently-selected template id. The strip uses this to detect
   *  the void + ``force_return_value`` combination — Frida physically
   *  refuses to attach an implementation that returns a value to a
   *  void Java method, so the chip strip transforms into an
   *  actionable warning banner with a one-click pivot to the
   *  ``force_method_skip`` template instead. ``null`` (no template
   *  selected) suppresses the special case along with everything
   *  else. */
  templateId: string | null;
  onPick: (value: string) => void;
  /** Wired only when the host can perform the template switch
   *  (presently always true inside HookBuilder). The button isn't
   *  rendered when this is omitted, falling back to the generic
   *  void note from ``suggestionsForReturnType``. */
  onSwitchToMethodSkip?: () => void;
};

function ReturnValueSuggestionStrip({
  returnTypes,
  templateId,
  onPick,
  onSwitchToMethodSkip,
}: StripProps) {
  if (returnTypes === null) return null;

  // Special case: force_return_value + void target. Pre-empts the
  // regular bundle render because the operator's whole form is on a
  // dead-end — letting them fill return_value_expr and click Inject
  // would just hand them the same Frida attach-time rejection. The
  // banner with the Switch button is the only correct path forward.
  const hasVoid = returnTypes.includes("void");
  if (
    hasVoid &&
    templateId === TEMPLATE_FORCE_RETURN_VALUE &&
    onSwitchToMethodSkip
  ) {
    return (
      <div className="hookbuilder-suggestions hookbuilder-suggestions-void">
        <span className="hookbuilder-suggestion-void-msg">
          <strong>This method returns void.</strong> Frida can&apos;t attach{" "}
          <code>force_return_value</code> to a void method (it refuses an
          implementation that returns a value). Use{" "}
          <code>force_method_skip</code> instead — it derives the empty
          return from the descriptor and runs cleanly on void.
        </span>
        <button
          type="button"
          className="hookbuilder-suggestion-switch"
          onClick={onSwitchToMethodSkip}
          title={
            "Switch the template, carry the class / method / event label " +
            "over, and seed return_descriptor=\"V\""
          }
        >
          Switch to force_method_skip
        </button>
      </div>
    );
  }

  if (returnTypes.length === 0) {
    return (
      <div className="hookbuilder-suggestions hookbuilder-suggestions-empty">
        <span className="muted small">
          Fill class &amp; method to see type-aware suggestions, or type a JS
          literal directly.
        </span>
      </div>
    );
  }
  // One bundle per resolved return type. We deliberately don't union
  // values across types because the values themselves are type-specific
  // (returning ``true`` from a method that declares ``Object`` is fine,
  // but rendering it next to ``"OK"`` in the same flat strip would
  // hide which value belongs to which type).
  const bundles = returnTypes
    .map((rt) => ({ rt, bundle: suggestionsForReturnType(rt) }))
    .filter((b): b is { rt: string; bundle: SuggestionBundle } => b.bundle !== null);
  if (bundles.length === 0) {
    return (
      <div className="hookbuilder-suggestions hookbuilder-suggestions-empty">
        <span className="muted small">
          Return type{" "}
          {returnTypes.map((t, i) => (
            <span key={t}>
              <code>{t}</code>
              {i < returnTypes.length - 1 ? ", " : ""}
            </span>
          ))}{" "}
          — no curated suggestions; type a JS expression directly.
        </span>
      </div>
    );
  }
  return (
    <div className="hookbuilder-suggestions">
      {bundles.map(({ rt, bundle }) => (
        <div key={rt} className="hookbuilder-suggestion-bundle">
          <span className="hookbuilder-suggestion-label muted small">
            Return: <code>{bundle.label}</code>
          </span>
          {bundle.values.length > 0 && (
            <span className="hookbuilder-suggestion-chips">
              {bundle.values.map((s) => (
                <button
                  key={s.value}
                  type="button"
                  className="hookbuilder-suggestion-chip"
                  title={s.hint}
                  onClick={() => onPick(s.value)}
                >
                  {s.value}
                </button>
              ))}
            </span>
          )}
          {bundle.note && (
            <span className="hookbuilder-suggestion-note muted small">
              {bundle.note}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// MonacoView — read-only JS view with optional inline error marker.

type MonacoViewProps = {
  value: string;
  parse: ParseInfo | null;
  placeholder: string;
};

const MONACO_OPTIONS: MonacoEditorNS.IStandaloneEditorConstructionOptions = {
  readOnly: true,
  minimap: { enabled: false },
  fontSize: 12,
  scrollBeyondLastLine: false,
  automaticLayout: true,
  wordWrap: "off",
  renderLineHighlight: "none",
  lineNumbers: "on",
  fontFamily: '"JetBrains Mono","SF Mono",Consolas,monospace',
};

function MonacoView({ value, parse, placeholder }: MonacoViewProps) {
  const editorRef = useRef<MonacoEditorNS.IStandaloneCodeEditor | null>(null);
  const monacoRef = useRef<typeof import("monaco-editor") | null>(null);

  const onMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor;
    monacoRef.current = monaco;
  }, []);

  // Push parse errors into Monaco's marker layer so they appear as
  // squiggles + gutter icons instead of out-of-band text. We use a
  // stable owner id (``"androscan-jsparse"``) so successive renders
  // replace the previous marker set rather than stacking.
  useEffect(() => {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    if (!editor || !monaco) return;
    const model = editor.getModel();
    if (!model) return;
    if (!parse || parse.ok || !parse.available) {
      monaco.editor.setModelMarkers(model, "androscan-jsparse", []);
      return;
    }
    const line = Math.max(parse.line ?? 1, 1);
    const lineLen = model.getLineLength(Math.min(line, model.getLineCount()));
    const range: IRange = {
      startLineNumber: line,
      endLineNumber: line,
      startColumn: parse.column ?? 1,
      endColumn: Math.max(lineLen + 1, (parse.column ?? 1) + 1),
    };
    monaco.editor.setModelMarkers(model, "androscan-jsparse", [
      {
        ...range,
        message: parse.error ?? "JS parse error",
        severity: monaco.MarkerSeverity.Error,
        source: "pyjsparser",
      },
    ]);
  }, [parse, value]);

  return (
    <div style={monacoHostStyle}>
      <Editor
        defaultLanguage="javascript"
        value={value || placeholder}
        theme="vs-dark"
        options={MONACO_OPTIONS}
        onMount={onMount}
      />
    </div>
  );
}

const monacoHostStyle: CSSProperties = {
  height: 280,
  border: "1px solid var(--border)",
  borderRadius: 4,
  overflow: "hidden",
};

// ---- Re-export for the parent so it can store the create result -----------
export type { CreateSessionResult, FridaResult };
