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
