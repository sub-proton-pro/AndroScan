/**
 * Discriminated-union renderer for ``DecisionPoint.predicate_origin``
 * (Phase 10 sub-step 10.7).
 *
 * One render per ``PredicateOrigin.kind`` variant:
 *
 *   * ``method_call``  — clickable method signature; click writes
 *                        ``pendingCodeNav`` for the called method's
 *                        Java file + flips the active tab to Inspect
 *                        so the operator can drill in.
 *   * ``field_read``   — field signature inline (no nav — the field's
 *                        defining class is implicit from the gate
 *                        method context). Indicates ``static`` vs
 *                        instance via a small badge.
 *   * ``const``        — the literal verbatim (operator's mental
 *                        model of the source survives) + the smali
 *                        opcode that loaded it.
 *   * ``param``        — the raw smali register (``p0``/``p1``/...)
 *                        + a short note that this is a method parameter.
 *   * ``composite``    — the smali opcode that produced the composite
 *                        value (e.g. ``add-int``, ``instance-of``).
 *
 * Phase 11 sub-step 11.6 / DEC-025 — ``method_call`` and ``field_read``
 * gain a small "via N helper method(s)" / "via N field write(s)"
 * pill (``DepthPill``) when the v2 inter-procedural slicer descended
 * past one or more terminals before stopping at this one. The pill
 * carries a tooltip that briefly explains what the depth means; per
 * Q2 (A) of the 11.6 planning checkpoint, the depth is a count only
 * (no path tracking) — operators audit the chain by following
 * "Open in Inspect" through the depth's terminal call.
 *
 * No state, no effects beyond the click handler that funnels through
 * ``WorkbenchContext``'s ``setPendingCodeNav`` + ``setTab``.
 */

import type { PredicateOrigin } from "../../api/trace";
import { useWorkbench } from "../../context/WorkbenchContext";
import { classNameToJavaRelPath } from "../../util/smaliClassToFile";

type Props = {
  origin: PredicateOrigin;
  /** The current ``appId`` from context — passed in by the parent so
   *  each badge doesn't have to re-call ``useWorkbench()``. ``null``
   *  disables the click-to-navigate behaviour. */
  appId: string | null;
};

/**
 * Phase 11 sub-step 11.6 — render the v2 slicer's descent-depth pill
 * when the slicer walked through one or more helpers / field writes
 * before terminating at this origin. Returns ``null`` for the v1
 * case (depth ``0`` / undefined).
 *
 * ``flavour`` is the human label distinction:
 *   * ``"helper"`` for ``MethodCallOrigin`` (descent walked through
 *     stateless helper methods).
 *   * ``"field-write"`` for ``FieldReadOrigin`` (descent walked
 *     same-class field-write sites).
 */
function DepthPill({
  depth,
  flavour,
}: {
  depth: number | undefined;
  flavour: "helper" | "field-write";
}) {
  if (!depth || depth <= 0) return null;
  const noun =
    flavour === "helper"
      ? depth === 1
        ? "helper method"
        : "helper methods"
      : depth === 1
      ? "field write"
      : "field writes";
  const tooltip =
    flavour === "helper"
      ? `Resolved through ${depth} stateless helper-method ${noun === "helper method" ? "hop" : "hops"} before stopping at this terminal. Hover the method link below to follow the chain into Inspect.`
      : `Resolved through ${depth} same-class field-write ${noun === "field write" ? "site" : "sites"} before stopping at this terminal. The field's most recent write was traced backwards.`;
  return (
    <span
      className="trace-predicate-origin-depth-pill"
      title={tooltip}
      aria-label={tooltip}
    >
      via {depth} {noun}
    </span>
  );
}

export function PredicateOriginView({ origin, appId }: Props) {
  const { setPendingCodeNav, setTab } = useWorkbench();

  if (origin.kind === "method_call") {
    const m = origin.method;
    const onClick = () => {
      if (!appId) return;
      setPendingCodeNav({
        appId,
        relPath: classNameToJavaRelPath(m.class_name),
        className: m.class_name,
        method: m.method_name,
      });
      setTab("inspect");
    };
    return (
      <div className="trace-predicate-origin trace-predicate-origin-method-call">
        <span className="trace-predicate-origin-tag">{origin.invoke_kind}</span>
        <button
          type="button"
          className="trace-predicate-origin-link"
          onClick={onClick}
          disabled={!appId}
          title={appId
            ? `Open ${m.class_name} in Inspect tab`
            : "No app selected"}
        >
          <code>
            {m.class_name}.{m.method_name}
            <span className="trace-predicate-origin-descriptor">
              ({m.param_descriptors.join(", ") || "—"}){m.return_descriptor}
            </span>
          </code>
        </button>
        <DepthPill depth={origin.descent_depth} flavour="helper" />
      </div>
    );
  }

  if (origin.kind === "field_read") {
    const f = origin.field;
    return (
      <div className="trace-predicate-origin trace-predicate-origin-field-read">
        <span className="trace-predicate-origin-tag">
          {origin.is_static ? "sget" : "iget"}
        </span>
        <code>
          {f.class_name}.{f.field_name}
          <span className="trace-predicate-origin-descriptor">: {f.type_descriptor}</span>
        </code>
        <DepthPill depth={origin.descent_depth} flavour="field-write" />
      </div>
    );
  }

  if (origin.kind === "const") {
    return (
      <div className="trace-predicate-origin trace-predicate-origin-const">
        <span className="trace-predicate-origin-tag">{origin.smali_op}</span>
        <code className="trace-predicate-origin-literal">{origin.value}</code>
      </div>
    );
  }

  if (origin.kind === "param") {
    return (
      <div className="trace-predicate-origin trace-predicate-origin-param">
        <span className="trace-predicate-origin-tag">param</span>
        <code>{origin.register}</code>
        <span className="muted small">
          (method parameter — bypass via the calling site)
        </span>
      </div>
    );
  }

  // composite
  return (
    <div className="trace-predicate-origin trace-predicate-origin-composite">
      <span className="trace-predicate-origin-tag">composite</span>
      <code>{origin.reason}</code>
      <span className="muted small">
        (intra-procedural slicer doesn't break this down further in v1)
      </span>
    </div>
  );
}
