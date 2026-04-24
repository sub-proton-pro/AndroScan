export type ElementInfo = {
  bounds: [number, number, number, number];
  cls: string;
  resource_id: string;
  text: string;
  content_desc: string;
  package: string;
  clickable: boolean;
  enabled: boolean;
};

export type CodeCandidate = {
  file: string;
  line: number;
  snippet: string;
  kind: "findViewById" | "compose_id" | "onClick_near" | "reference";
};

/**
 * One scored handler candidate as produced by the ``resolve_ui_element``
 * fuser (server-side ``androscan/skills/resolve_ui_element.py``). The same
 * shape is used for both deterministic grep hits and RAG-derived chunks;
 * the ``source`` field tells them apart for UI badging.
 */
export type ResolutionCandidate = {
  file: string;
  line: number;
  snippet: string;
  /** ``rag`` for RAG-derived candidates; otherwise the original grep kind. */
  kind: "findViewById" | "compose_id" | "onClick_near" | "reference" | "rag";
  class_name: string | null;
  method_name: string | null;
  /** ``"deterministic"`` (grep) or ``"rag"`` (vector retrieval). */
  source: "deterministic" | "rag";
  /** Fuser score (additive; higher = stronger handler signal). */
  score: number;
  /** Human-readable factor list — what bumped this candidate up. */
  reasons: string[];
};

/** Top-k RAG chunk as returned by ``androscan.rag.search.query``. */
export type ResolutionRagHit = {
  file: string;
  start_line: number;
  end_line: number;
  class_name: string;
  method_name: string | null;
  kind: string;
  score: number;
  content: string;
};

/**
 * Fuser block attached to ``MapResult`` by ``POST /api/inspect/map``. The
 * server runs the deterministic scorer + (when available) Lane-1 RAG
 * enrichment so the UI can render a single ``best`` answer with reasons
 * without an LLM round-trip per click.
 */
export type Resolution = {
  best: ResolutionCandidate | null;
  alternatives: ResolutionCandidate[];
  rag_hits: ResolutionRagHit[];
  rag_query: string | null;
  rag_error: string | null;
  /** Echo of the element + foreground for chat context. */
  element: ElementInfo | null;
  foreground_activity: string | null;
  /** One-line human summary of the picked handler + reasons. */
  summary: string;
};

export type MapResult = {
  x: number;
  y: number;
  sha: string | null;
  foreground_activity: string | null;
  element: ElementInfo | null;
  short_resource_id: string | null;
  candidates: CodeCandidate[];
  ui_dump_ok: boolean;
  decompile_status: string | null;
  /** Always present unless the fuser threw; see {@link Resolution}. */
  resolution?: Resolution;
};

export async function mapTap(appId: string, x: number, y: number): Promise<MapResult | null> {
  const r = await fetch("/api/inspect/map", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app_id: appId, x: Math.round(x), y: Math.round(y) }),
  });
  if (!r.ok) return null;
  return (await r.json()) as MapResult;
}
