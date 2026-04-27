/**
 * Typed client for the static call-graph routes (`/api/graph/*`) shipped in
 * Hook Lab sub-step 4.1.
 *
 * The backend (see ``androscan/web/graph_routes.py`` +
 * ``androscan/analysis/call_graph.py``) returns nodes and classes separately
 * — nodes only carry a ``class_id`` foreign key. Consumers that need the
 * package / class name for display join the two on the client. We export
 * a small ``buildClassMap`` helper to make that join one line.
 */

// ---------------------------------------------------------------------------
// Result envelope reused from rag.ts
// ---------------------------------------------------------------------------

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

async function getJson<T>(url: string): Promise<ApiResult<T>> {
  try {
    const r = await fetch(url);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return {
        ok: false,
        error: (body as { detail?: string })?.detail ?? `HTTP ${r.status}`,
        status: r.status,
      };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message, status: 0 };
  }
}

async function postJson<T>(url: string): Promise<ApiResult<T>> {
  try {
    const r = await fetch(url, { method: "POST" });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return {
        ok: false,
        error: (body as { detail?: string })?.detail ?? `HTTP ${r.status}`,
        status: r.status,
      };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return { ok: false, error: (e as Error).message, status: 0 };
  }
}

// ---------------------------------------------------------------------------
// Domain types — mirror call_graph._{node,class,edge}_to_dict() exactly.
// ---------------------------------------------------------------------------

export type GraphEdgeKind =
  | "direct"
  | "static"
  | "super"
  | "virtual_dispatch"
  | "interface_dispatch"
  | "external";

export type GraphNode = {
  id: number;
  smali_id: string;
  class_id: number;
  method_name: string;
  descriptor: string;
  return_type: string;
  param_types: string[];
  access_flags: number;
  is_static: boolean;
  is_abstract: boolean;
  is_native: boolean;
  is_synthetic: boolean;
  is_constructor: boolean;
  is_external: boolean;
  smali_start_line: number | null;
  smali_end_line: number | null;
  may_have_unresolved_reflection: boolean;
};

export type GraphClass = {
  id: number;
  smali_class: string;
  class_name: string;
  package: string;
  simple_name: string;
  super_class: string | null;
  is_external: boolean;
  is_abstract: boolean;
  is_interface: boolean;
  smali_file: string | null;
  jadx_file: string | null;
};

export type GraphEdge = {
  src_id: number;
  dst_id: number;
  kind: GraphEdgeKind;
  invoke_op: string;
  src_line: number | null;
};

// ---------------------------------------------------------------------------
// Status payload
// ---------------------------------------------------------------------------

export type GraphIndexStatus = {
  status: "missing" | "pending" | "ready" | "failed";
  sha: string | null;
  fidelity_level: string | null;
  parser_version: string | null;
  built_at: number | null;
  finished_at: number | null;
  class_count: number | null;
  external_class_count: number | null;
  node_count: number | null;
  edge_count: number | null;
  error: string | null;
  db_path: string | null;
};

export type GraphStatusResponse = {
  app_id: string;
  decompile_status: string;
  call_graph: GraphIndexStatus;
  call_graph_meta?: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// Endpoint payloads
// ---------------------------------------------------------------------------

export type GraphListResponse = {
  app_id: string;
  sha: string;
  limit: number;
  offset: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  classes: GraphClass[];
  total_nodes: number;
  total_edges: number;
  total_classes: number;
  error?: string;
};

export type GraphNeighborEntry = {
  node: GraphNode;
  edge: GraphEdge;
};

export type GraphNeighborsResponse = {
  app_id: string;
  node: GraphNode;
  callers: GraphNeighborEntry[];
  callees: GraphNeighborEntry[];
  classes: GraphClass[];
};

export type GraphPathsResponse = {
  app_id: string;
  paths: number[][];
  from?: number;
  to?: number;
  max_hops?: number;
  max_paths?: number;
  include_external?: boolean;
  error?: string;
};

export type GraphRebuildResponse = {
  app_id: string;
  sha: string;
  kicked: boolean;
};

// ---------------------------------------------------------------------------
// Endpoint wrappers
// ---------------------------------------------------------------------------

export function fetchGraphStatus(
  appId: string,
  opts: { verbose?: boolean } = {},
): Promise<ApiResult<GraphStatusResponse>> {
  const q = opts.verbose ? "?verbose=true" : "";
  return getJson(`/api/graph/${encodeURIComponent(appId)}/status${q}`);
}

export type FetchGraphOpts = {
  packagePrefix?: string | null;
  kind?: GraphEdgeKind | null;
  includeExternal?: boolean;
  limit?: number;
  offset?: number;
};

export function fetchGraph(
  appId: string,
  opts: FetchGraphOpts = {},
): Promise<ApiResult<GraphListResponse>> {
  const params = new URLSearchParams();
  if (opts.packagePrefix) params.set("package_prefix", opts.packagePrefix);
  if (opts.kind) params.set("kind", opts.kind);
  if (opts.includeExternal) params.set("include_external", "true");
  if (typeof opts.limit === "number") params.set("limit", String(opts.limit));
  if (typeof opts.offset === "number") params.set("offset", String(opts.offset));
  const qs = params.toString();
  return getJson(
    `/api/graph/${encodeURIComponent(appId)}${qs ? `?${qs}` : ""}`,
  );
}

export function fetchNeighbors(
  appId: string,
  nodeRef: string | number,
  opts: { limitEach?: number } = {},
): Promise<ApiResult<GraphNeighborsResponse>> {
  const ref = encodeURIComponent(String(nodeRef));
  const params = new URLSearchParams();
  if (typeof opts.limitEach === "number") {
    params.set("limit_each", String(opts.limitEach));
  }
  const qs = params.toString();
  return getJson(
    `/api/graph/${encodeURIComponent(appId)}/neighbors/${ref}${qs ? `?${qs}` : ""}`,
  );
}

export function fetchPaths(
  appId: string,
  source: string | number,
  target: string | number,
  opts: {
    maxHops?: number;
    maxPaths?: number;
    includeExternal?: boolean;
  } = {},
): Promise<ApiResult<GraphPathsResponse>> {
  const params = new URLSearchParams({
    source: String(source),
    target: String(target),
  });
  if (typeof opts.maxHops === "number") params.set("max_hops", String(opts.maxHops));
  if (typeof opts.maxPaths === "number") params.set("max_paths", String(opts.maxPaths));
  if (opts.includeExternal) params.set("include_external", "true");
  return getJson(
    `/api/graph/${encodeURIComponent(appId)}/paths?${params.toString()}`,
  );
}

export function rebuildGraph(
  appId: string,
  opts: { dropApktool?: boolean } = {},
): Promise<ApiResult<GraphRebuildResponse>> {
  const q = opts.dropApktool ? "?drop_apktool=true" : "";
  return postJson(`/api/graph/${encodeURIComponent(appId)}/rebuild${q}`);
}

// ---------------------------------------------------------------------------
// Client-side join helpers — used by CallGraphView to render labels.
// ---------------------------------------------------------------------------

export type ClassMap = ReadonlyMap<number, GraphClass>;

export function buildClassMap(classes: GraphClass[]): ClassMap {
  const m = new Map<number, GraphClass>();
  for (const c of classes) m.set(c.id, c);
  return m;
}

/** Format a node as ``ClassName.method(paramTypes)`` for compact tooltip lines.
 * Inner classes keep their ``$``; the caller is responsible for stripping if it wants
 * the file path (see ``util/smaliClassToFile.ts``). */
export function formatMethodSignature(
  node: GraphNode,
  klass: GraphClass | null | undefined,
): string {
  const cls = klass?.class_name ?? `class#${node.class_id}`;
  const params = node.param_types.join(", ");
  return `${cls}.${node.method_name}(${params})`;
}
