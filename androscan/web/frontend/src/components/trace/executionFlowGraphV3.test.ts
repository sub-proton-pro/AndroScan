// Phase 13 v3.X-next.2 / DEC-031 — FE unit tests for the
// ``executionFlowGraphV3.ts`` emitter. Co-located ``*.test.ts`` per
// the v3.X-next.2.0 locked Q3=(a) file-placement decision.
//
// Coverage map (one ``it`` per row; each row's intent is folded
// into the test name so the TASKS verification matrix can read
// straight off the vitest output):
//
// **Pure helpers** (``methodKey`` / ``overloadKey`` /
// ``overloadKeyFromNodeId`` / ``closureMethodCount``):
//
//   * methodKey rebuilds the smali signature byte-equal to what
//     the slicer emits on the Python side.
//   * overloadKey drops the descriptor portion so overload-merging
//     dedupes correctly.
//   * overloadKeyFromNodeId is the inverse: extracts the overload
//     key from a methodKey-shaped node id.
//   * closureMethodCount sums entry + decision-source + plan-target
//     + plan-source unique smali signatures, deduplicated across
//     the five-source flatten (matches BE
//     ``extract_closure_methods``).
//
// **Emitter** (``buildExecutionFlowV3Graph`` + ``graphV3Stats``):
//
//   * Empty anchor emits just the entry node, no edges.
//   * Single-decision anchor emits ``entry`` + the gate as ``gate``
//     kind + verdict-edges to plan target (when planTarget +
//     verdict=="allow").
//   * ``hideRetPills=true`` (the v3.1 default) suppresses return-
//     pill terminals; ``hideRetPills=false`` brings them back.
//   * ``gatesOnly=true`` (the v3.1 default) drops framework-class
//     methods; ``gatesOnly=false`` keeps them.
//   * Entry method is always kept regardless of ``gatesOnly``.
//   * Plan targets are always kept regardless of ``gatesOnly``.
//   * ``verdictSummary`` populates correctly across multi-verdict
//     + multi-decision methods.
//   * ``unverdicted`` decisions (``branch_outcome == null``) count
//     into the ``unverdicted`` bucket of the chip.
//
// **CFG-position-aware invoke edges** (v3.X-next.2 headline):
//
//   * Single caller with N main-segment calls emits N invoke
//     edges chained ``caller → A → B → C``.
//   * Calls inside a branch arm chain independently from main-
//     segment + from a different arm.
//   * Calls are sorted by ``instruction_index`` even if the input
//     list is shuffled (defensive sort guard).
//   * Self-loop (caller == callee) is suppressed but the chain
//     still advances past it.
//   * Callees whose node was filtered out (e.g. framework callee
//     dropped by ``gatesOnly``) don't produce edges + don't break
//     the chain — subsequent calls in the same arm chain off the
//     previous surviving callee.
//   * Callers with no node (defensive — shouldn't happen post-
//     ingest) are skipped silently.
//   * Empty ``method_invocations`` entry on entry → Q5=(a)
//     gap-fallback emits the v3.1 synthesised ``call`` edges from
//     entry to every decision-source.
//   * Non-empty ``method_invocations`` entry on entry → no
//     synthesised ``call`` fallback (the invoke chain is the
//     primary signal).
//   * ``method_invocations`` missing the entry key but present
//     for other callers → the Q5=(a) gap-fallback STILL fires for
//     the entry (the fallback's pivot is "did entry emit at least
//     one invoke", not "is the field non-empty in the aggregate").
//
// **graphV3Stats**:
//
//   * Counts invokeEdges + callEdges + verdictEdges separately.
//   * Dangling-edge counter stays at 0 for well-formed anchors.

import { describe, it, expect } from "vitest";

import {
  buildExecutionFlowV3Graph,
  closureMethodCount,
  graphV3Stats,
  methodKey,
  overloadKey,
  overloadKeyFromNodeId,
} from "./executionFlowGraphV3";
import type {
  BehaviorAnchor,
  BranchOutcome,
  BypassPlan,
  CallSite,
  DecisionPoint,
  MethodRef,
} from "../../api/trace";

// ---------------------------------------------------------------------------
// Fixture builders — keep call sites concise + readable. Defaults
// match the simplest anchor the v3 emitter can consume (no
// decisions, no plans, no invocations).

function mref(
  className: string,
  methodName: string,
  params: string[] = [],
  ret = "V",
): MethodRef {
  return {
    class_name: className,
    method_name: methodName,
    param_descriptors: params,
    return_descriptor: ret,
  };
}

function outcome(
  verdicts: Array<{ verdict: "allow" | "deny" | "neutral"; score?: number }>,
  confidence = 0.9,
): BranchOutcome {
  return {
    verdicts: verdicts.map((v, i) => ({
      branch_label: `:cond_${i}`,
      verdict: v.verdict,
      score: v.score ?? (v.verdict === "allow" ? 1 : v.verdict === "deny" ? -1 : 0),
      reasons: [],
    })),
    confidence,
    reasons: [],
  };
}

function decision(
  method: MethodRef,
  instructionIndex: number,
  outcome: BranchOutcome | null = null,
  sourceLine: number | null = null,
): DecisionPoint {
  return {
    method,
    instruction_index: instructionIndex,
    source_line: sourceLine,
    kind: "if_eqz",
    predicate_registers: ["v0"],
    branches: [],
    predicate_origin: null,
    branch_outcome: outcome,
  };
}

function plan(
  source: MethodRef,
  sourceInstructionIndex: number,
  target: MethodRef | null,
): BypassPlan {
  return {
    template_id: "stub",
    params: {},
    rationale: "",
    risk: "low",
    risks: [],
    target_method: target,
    source_decision_method: source,
    source_decision_instruction_index: sourceInstructionIndex,
  };
}

function callsite(
  caller: MethodRef,
  callee: MethodRef,
  instructionIndex: number,
  inBranchOf: number | null = null,
  branchLabel: string | null = null,
): CallSite {
  return {
    caller,
    callee,
    instruction_index: instructionIndex,
    in_branch_of: inBranchOf,
    branch_label: branchLabel,
  };
}

function anchor(over: Partial<BehaviorAnchor>): BehaviorAnchor {
  return {
    entry_method: over.entry_method ?? mref("com.example.Foo", "entry"),
    hops: over.hops ?? 3,
    truncated: over.truncated ?? false,
    incomplete: over.incomplete ?? false,
    decisions: over.decisions ?? [],
    plans: over.plans ?? [],
    advanced_plans: over.advanced_plans ?? [],
    rationale: over.rationale ?? "",
    low_confidence_decision_indices: over.low_confidence_decision_indices ?? [],
    method_invocations: over.method_invocations,
  };
}

// ---------------------------------------------------------------------------
// Pure helpers

describe("methodKey / overloadKey / overloadKeyFromNodeId", () => {
  it("methodKey rebuilds the smali signature with dotted → slashed class", () => {
    expect(methodKey(mref("com.example.Foo", "bar", ["I"], "Z"))).toBe(
      "Lcom/example/Foo;->bar(I)Z",
    );
  });

  it("methodKey defaults missing return descriptor to V", () => {
    const m = { ...mref("com.example.Foo", "bar"), return_descriptor: "" };
    expect(methodKey(m as MethodRef)).toBe("Lcom/example/Foo;->bar()V");
  });

  it("overloadKey drops the descriptor portion for dedup", () => {
    expect(overloadKey(mref("com.example.Foo", "bar", ["I"], "Z"))).toBe(
      "Lcom/example/Foo;->bar",
    );
    expect(overloadKey(mref("com.example.Foo", "bar", ["J"], "I"))).toBe(
      "Lcom/example/Foo;->bar",
    );
  });

  it("overloadKeyFromNodeId is the inverse of methodKey", () => {
    const id = methodKey(mref("com.example.Foo", "bar", ["I"], "Z"));
    expect(overloadKeyFromNodeId(id)).toBe("Lcom/example/Foo;->bar");
  });

  it("overloadKeyFromNodeId tolerates a methodKey with no paren (no-arg sig)", () => {
    expect(overloadKeyFromNodeId("Lcom/example/Foo;->bar")).toBe(
      "Lcom/example/Foo;->bar",
    );
  });
});

describe("closureMethodCount", () => {
  it("returns 1 for an anchor with just the entry", () => {
    expect(closureMethodCount(anchor({}))).toBe(1);
  });

  it("dedupes across entry + decision sources + plan targets + plan sources", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const callee = mref("com.example.Foo", "validatePin", ["I"], "Z");
    const result = closureMethodCount(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1)],
        plans: [plan(gate, 1, callee)],
      }),
    );
    expect(result).toBe(3);
  });

  it("does not double-count when the same method appears in multiple roles", () => {
    const entry = mref("com.example.Foo", "entry");
    const result = closureMethodCount(
      anchor({
        entry_method: entry,
        decisions: [decision(entry, 1)],
        plans: [plan(entry, 1, entry)],
      }),
    );
    expect(result).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// buildExecutionFlowV3Graph — basic shape

describe("buildExecutionFlowV3Graph — empty anchor", () => {
  it("emits a single entry node + no edges for the simplest input", () => {
    const g = buildExecutionFlowV3Graph(anchor({}));
    expect(g.nodes).toHaveLength(1);
    expect(g.nodes[0].kind).toBe("entry");
    expect(g.edges).toHaveLength(0);
  });
});

describe("buildExecutionFlowV3Graph — verdict routing", () => {
  it("routes the 'allow' verdict through a resolved plan target as a verdict edge", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const callee = mref("com.example.Foo", "openSession", [], "V");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "allow" }]))],
        plans: [plan(gate, 1, callee)],
      }),
    );
    const allowEdges = g.edges.filter((e) => e.kind === "allow");
    expect(allowEdges).toHaveLength(1);
    expect(allowEdges[0].source).toBe(methodKey(gate));
    expect(allowEdges[0].target).toBe(methodKey(callee));
    expect(allowEdges[0].label).toBe("Ret: True");
  });

  it("populates verdictSummary on multi-verdict gate methods", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [
          decision(gate, 1, outcome([{ verdict: "allow" }, { verdict: "deny" }])),
          decision(gate, 2, outcome([{ verdict: "neutral" }])),
          decision(gate, 3, null),
        ],
      }),
    );
    const gateNode = g.nodes.find((n) => n.id === methodKey(gate));
    expect(gateNode?.verdictSummary).toEqual({
      allow: 1,
      deny: 1,
      neutral: 1,
      unverdicted: 1,
    });
  });
});

describe("buildExecutionFlowV3Graph — hideRetPills", () => {
  it("v3.1 default (hideRetPills=true) suppresses return-pill terminals", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
      }),
    );
    expect(g.nodes.filter((n) => n.kind === "return_pill")).toHaveLength(0);
  });

  it("hideRetPills=false emits return-pill terminals for branches with no plan target", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
      }),
      { hideRetPills: false },
    );
    expect(g.nodes.filter((n) => n.kind === "return_pill")).toHaveLength(1);
  });
});

describe("buildExecutionFlowV3Graph — gatesOnly framework filter", () => {
  it("v3.1 default (gatesOnly=true) drops methods on a framework class", () => {
    const entry = mref("com.example.Foo", "entry");
    const fwGate = mref("kotlin.jvm.internal.Intrinsics", "checkNotNullParam", [], "V");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(fwGate, 1, outcome([{ verdict: "neutral" }]))],
      }),
    );
    expect(g.nodes.find((n) => n.id === methodKey(fwGate))).toBeUndefined();
  });

  it("gatesOnly=false keeps framework methods", () => {
    const entry = mref("com.example.Foo", "entry");
    const fwGate = mref("kotlin.jvm.internal.Intrinsics", "checkNotNullParam", [], "V");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(fwGate, 1, outcome([{ verdict: "neutral" }]))],
      }),
      { gatesOnly: false },
    );
    expect(g.nodes.find((n) => n.id === methodKey(fwGate))).toBeDefined();
  });

  it("entry method on a framework class still survives gatesOnly=true", () => {
    const entry = mref("android.app.Activity", "onCreate", ["Landroid/os/Bundle;"], "V");
    const g = buildExecutionFlowV3Graph(anchor({ entry_method: entry }));
    expect(g.nodes.find((n) => n.id === methodKey(entry))).toBeDefined();
  });

  it("plan-target method on a framework class still survives gatesOnly=true", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const fwTarget = mref("android.util.Log", "i", ["Ljava/lang/String;"], "I");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "allow" }]))],
        plans: [plan(gate, 1, fwTarget)],
      }),
    );
    expect(g.nodes.find((n) => n.id === methodKey(fwTarget))).toBeDefined();
  });
});

// ---------------------------------------------------------------------------
// v3.X-next.2 — invoke edges from ``method_invocations``

describe("buildExecutionFlowV3Graph — invoke edges (main segment chaining)", () => {
  it("chains N main-segment calls as entry → A → B → C", () => {
    const entry = mref("com.example.Foo", "entry");
    const a = mref("com.example.Foo", "stepA");
    const b = mref("com.example.Foo", "stepB");
    const c = mref("com.example.Foo", "stepC");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [
            callsite(entry, a, 1),
            callsite(entry, b, 2),
            callsite(entry, c, 3),
          ],
        },
      }),
    );
    const invokeEdges = g.edges.filter((e) => e.kind === "invoke");
    expect(invokeEdges).toHaveLength(3);
    expect(invokeEdges[0]).toMatchObject({
      source: methodKey(entry),
      target: methodKey(a),
      instructionIndex: 1,
    });
    expect(invokeEdges[1]).toMatchObject({
      source: methodKey(a),
      target: methodKey(b),
      instructionIndex: 2,
    });
    expect(invokeEdges[2]).toMatchObject({
      source: methodKey(b),
      target: methodKey(c),
      instructionIndex: 3,
    });
  });

  it("sorts callsites by instruction_index even when the input list is shuffled", () => {
    const entry = mref("com.example.Foo", "entry");
    const a = mref("com.example.Foo", "stepA");
    const b = mref("com.example.Foo", "stepB");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [callsite(entry, b, 5), callsite(entry, a, 2)],
        },
      }),
    );
    const invokes = g.edges.filter((e) => e.kind === "invoke");
    expect(invokes.map((e) => e.instructionIndex)).toEqual([2, 5]);
  });

  it("self-loop callsites don't emit an edge but still advance the chain", () => {
    const entry = mref("com.example.Foo", "entry");
    const b = mref("com.example.Foo", "stepB");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [callsite(entry, entry, 1), callsite(entry, b, 2)],
        },
      }),
    );
    const invokes = g.edges.filter((e) => e.kind === "invoke");
    expect(invokes).toHaveLength(1);
    expect(invokes[0]).toMatchObject({
      source: methodKey(entry),
      target: methodKey(b),
    });
  });
});

describe("buildExecutionFlowV3Graph — invoke edges (branch arm forking)", () => {
  it("forks main-segment and branch-arm calls into independent chains", () => {
    const entry = mref("com.example.Foo", "entry");
    const a = mref("com.example.Foo", "stepA");
    const b = mref("com.example.Foo", "stepB");
    const trueArmCallee = mref("com.example.Foo", "armT");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [
            callsite(entry, a, 1),
            callsite(entry, trueArmCallee, 2, /*inBranchOf*/ 4, ":cond_0"),
            callsite(entry, b, 5),
          ],
        },
      }),
    );
    const invokes = g.edges.filter((e) => e.kind === "invoke");
    expect(invokes).toHaveLength(3);
    expect(invokes[0]).toMatchObject({
      source: methodKey(entry),
      target: methodKey(a),
      inBranchOf: null,
    });
    expect(invokes[1]).toMatchObject({
      source: methodKey(entry),
      target: methodKey(trueArmCallee),
      inBranchOf: 4,
      branchLabel: ":cond_0",
    });
    expect(invokes[2]).toMatchObject({
      source: methodKey(a),
      target: methodKey(b),
      inBranchOf: null,
    });
  });

  it("isolates two distinct branch arms into separate chains", () => {
    const entry = mref("com.example.Foo", "entry");
    const armT1 = mref("com.example.Foo", "armT1");
    const armT2 = mref("com.example.Foo", "armT2");
    const armF1 = mref("com.example.Foo", "armF1");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [
            callsite(entry, armT1, 1, 4, ":cond_0"),
            callsite(entry, armF1, 2, 4, ":cond_1"),
            callsite(entry, armT2, 3, 4, ":cond_0"),
          ],
        },
      }),
    );
    const invokes = g.edges.filter((e) => e.kind === "invoke");
    expect(invokes).toHaveLength(3);
    expect(invokes[0].target).toBe(methodKey(armT1));
    expect(invokes[0].source).toBe(methodKey(entry));
    expect(invokes[1].target).toBe(methodKey(armF1));
    expect(invokes[1].source).toBe(methodKey(entry));
    expect(invokes[2].target).toBe(methodKey(armT2));
    expect(invokes[2].source).toBe(methodKey(armT1));
  });
});

describe("buildExecutionFlowV3Graph — invoke edges (filtered callees)", () => {
  it("skips invoke edges whose callee was framework-filtered + chain skips the gap", () => {
    const entry = mref("com.example.Foo", "entry");
    const a = mref("com.example.Foo", "stepA");
    const fwCallee = mref("kotlin.jvm.internal.Intrinsics", "checkNotNullParam", [], "V");
    const b = mref("com.example.Foo", "stepB");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        method_invocations: {
          [entrySig]: [
            callsite(entry, a, 1),
            callsite(entry, fwCallee, 2),
            callsite(entry, b, 3),
          ],
        },
      }),
    );
    const invokes = g.edges.filter((e) => e.kind === "invoke");
    expect(invokes).toHaveLength(2);
    expect(invokes[1]).toMatchObject({
      source: methodKey(a),
      target: methodKey(b),
    });
  });
});

describe("buildExecutionFlowV3Graph — Q5=(a) gap-fallback", () => {
  it("emits synthesised call edges when method_invocations is absent", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
      }),
    );
    const callEdges = g.edges.filter((e) => e.kind === "call");
    expect(callEdges).toHaveLength(1);
    expect(callEdges[0]).toMatchObject({
      source: methodKey(entry),
      target: methodKey(gate),
    });
  });

  it("emits synthesised call edges when method_invocations is an empty object", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
        method_invocations: {},
      }),
    );
    expect(g.edges.filter((e) => e.kind === "call")).toHaveLength(1);
  });

  it("suppresses the synthesised call fallback when entry has at least one invoke edge", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
        method_invocations: {
          [entrySig]: [callsite(entry, gate, 1)],
        },
      }),
    );
    expect(g.edges.filter((e) => e.kind === "call")).toHaveLength(0);
    expect(g.edges.filter((e) => e.kind === "invoke")).toHaveLength(1);
  });

  it("still fires the gap-fallback when method_invocations is non-empty BUT the entry's own list is missing", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const callee = mref("com.example.Foo", "createSession");
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "neutral" }]))],
        method_invocations: {
          [methodKey(gate)]: [callsite(gate, callee, 1)],
        },
      }),
    );
    expect(g.edges.filter((e) => e.kind === "call")).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// graphV3Stats

describe("graphV3Stats", () => {
  it("counts invokeEdges + callEdges + verdictEdges in separate buckets", () => {
    const entry = mref("com.example.Foo", "entry");
    const gate = mref("com.example.Foo", "checkPin", ["I"], "Z");
    const callee = mref("com.example.Foo", "openSession");
    const entrySig = methodKey(entry);
    const g = buildExecutionFlowV3Graph(
      anchor({
        entry_method: entry,
        decisions: [decision(gate, 1, outcome([{ verdict: "allow" }]))],
        plans: [plan(gate, 1, callee)],
        method_invocations: {
          [entrySig]: [callsite(entry, gate, 1)],
        },
      }),
    );
    const s = graphV3Stats(g);
    expect(s.invokeEdges).toBeGreaterThanOrEqual(1);
    expect(s.callEdges).toBe(0);
    expect(s.verdictEdges).toBeGreaterThanOrEqual(1);
    expect(s.dangling).toBe(0);
  });

  it("reports zero dangling edges for well-formed anchors", () => {
    const entry = mref("com.example.Foo", "entry");
    const g = buildExecutionFlowV3Graph(anchor({ entry_method: entry }));
    expect(graphV3Stats(g).dangling).toBe(0);
  });
});
