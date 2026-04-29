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

  // ---- 3. Param form helpers -------------------------------------------

  const onParamChange = useCallback((name: string, value: string) => {
    setParamValues((prev) => ({ ...prev, [name]: value }));
    // Operator-edited the form — drop the "Staged from Trace" pill so
    // the badge doesn't lie about the form's provenance.
    setStagedSourceLabel(null);
  }, []);

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
          return (
            <div key={p.name} className="hookbuilder-row">
              <label
                className="hookbuilder-label"
                htmlFor={`hookbuilder-param-${p.name}`}
                title={p.description}
              >
                {p.name}
                {p.required && <span className="hookbuilder-required"> *</span>}
              </label>
              <input
                id={`hookbuilder-param-${p.name}`}
                className={`hookbuilder-input ${requiredAndMissing ? "hookbuilder-input-bad" : ""}`}
                value={value}
                placeholder={p.description}
                onChange={(e) => onParamChange(p.name, e.target.value)}
                spellCheck={false}
                autoComplete="off"
              />
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
