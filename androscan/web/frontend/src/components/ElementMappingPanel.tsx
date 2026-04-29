import { useEffect, useRef, useState } from "react";
import { fetchSource } from "../api/code";
import type {
  CodeCandidate,
  MapResult,
  ResolutionCandidate,
  ResolutionRagHit,
} from "../api/inspect";
import { CodeView } from "./CodeView";
import { IconOpenIn } from "./Icons";

type Props = {
  appId: string | null;
  result: MapResult | null;
  busy: boolean;
  error: string | null;
  /** Optional: open this candidate in the Code Browser tab, scroll to it
   *  and keep the snippet's whole line range visually highlighted there. */
  onOpenInBrowser?: (file: string, startLine: number, endLine: number) => void;
  /** Phase 10 sub-step 10.8: cross-tab "Trace this behaviour" handoff.
   *  Invoked from the ``BestBanner``'s pen-shaped button; receives the
   *  fuser's pick so the host (``InspectTab``) can convert it into a
   *  Smali entry-method prefix and seed the Lab → Trace mode form via
   *  ``setPendingTraceEntry`` + ``setLabMode("trace")`` + ``setTab("lab")``.
   *  The button only renders when the prop is supplied. */
  onTraceBehaviour?: (best: ResolutionCandidate) => void;
};

/**
 * Compute the source line range covered by ``c.snippet`` based on how
 * the server builds it (``_snippet`` in ``inspect_map.py`` uses
 * ``before=1, after=2`` around the matched line, clamped at file edges).
 * We can't recover the exact clamp without the file length, so we infer
 * the start from the matched line and derive the end from the snippet's
 * own line count.
 */
function snippetRange(c: CodeCandidate): { start: number; end: number } {
  const numLines = Math.max(1, c.snippet.split("\n").length);
  const start = c.line >= 2 ? c.line - 1 : 1;
  const end = start + numLines - 1;
  return { start, end };
}

/**
 * For ``ResolutionCandidate`` we only have the entry line. The fuser
 * doesn't carry an end-line, so we pick a small window so the highlight
 * in the Code Browser viewer is visible but not overwhelming.
 */
function resolutionRange(c: ResolutionCandidate): { start: number; end: number } {
  const start = Math.max(1, c.line || 1);
  const numLines = c.snippet ? Math.max(1, c.snippet.split("\n").length) : 4;
  return { start, end: start + numLines - 1 };
}

const KIND_LABEL: Record<CodeCandidate["kind"], string> = {
  findViewById: "findViewById",
  onClick_near: "near onClick",
  compose_id: "compose id",
  reference: "reference",
};

const RESOLUTION_KIND_LABEL: Record<ResolutionCandidate["kind"], string> = {
  ...KIND_LABEL,
  rag: "RAG",
};

/**
 * Sub-activity script the progress simulator walks through while the real
 * /api/inspect/map call is in flight. We don't get progress events from the
 * server, so we model the realistic sequence with believable per-step
 * latencies. Hardcoded timings make sub-300 ms taps feel alive without
 * misleading the user when the server is genuinely slow (the bar caps at
 * 95 % until the real result arrives).
 */
const MAP_PROGRESS_STEPS: { pct: number; label: string; afterMs: number }[] = [
  { pct: 8,  label: "Probing adb device…",            afterMs: 0 },
  { pct: 22, label: "Capturing UI hierarchy (uiautomator dump)…", afterMs: 250 },
  { pct: 42, label: "Parsing UI XML and locating tapped element…", afterMs: 600 },
  { pct: 62, label: "Resolving R.id reference for this view…",     afterMs: 1100 },
  { pct: 78, label: "Searching decompiled sources for handlers…",  afterMs: 1700 },
  { pct: 92, label: "Ranking code candidates and snippets…",       afterMs: 2700 },
  { pct: 95, label: "Finalising mapping…",                          afterMs: 4000 },
];

export function ElementMappingPanel({
  appId,
  result,
  busy,
  error,
  onOpenInBrowser,
  onTraceBehaviour,
}: Props) {
  const [openIdx, setOpenIdx] = useState<number | null>(null);
  const [openSrc, setOpenSrc] = useState<string | null>(null);
  const [progressPct, setProgressPct] = useState(0);
  const [steps, setSteps] = useState<string[]>([]);
  const timersRef = useRef<number[]>([]);

  // Reset open candidate whenever the underlying mapping changes.
  useEffect(() => {
    setOpenIdx(null);
    setOpenSrc(null);
  }, [result]);

  // Drive the progress bar + sub-activity log while ``busy`` is true.
  useEffect(() => {
    timersRef.current.forEach((id) => window.clearTimeout(id));
    timersRef.current = [];
    if (!busy) {
      setProgressPct(0);
      setSteps([]);
      return;
    }
    setProgressPct(2);
    setSteps([]);
    for (const step of MAP_PROGRESS_STEPS) {
      const id = window.setTimeout(() => {
        setProgressPct(step.pct);
        setSteps((prev) => [...prev, step.label]);
      }, step.afterMs);
      timersRef.current.push(id);
    }
    return () => {
      timersRef.current.forEach((id) => window.clearTimeout(id));
      timersRef.current = [];
    };
  }, [busy]);

  if (busy) {
    const pct = Math.max(2, Math.min(95, progressPct));
    return (
      <div className="map-panel map-panel-busy">
        <header className="map-progress-head">
          <strong>Mapping tap to code…</strong>
          <span className="muted small">{pct}%</span>
        </header>
        <div
          className="map-progress-bar"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="map-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <ul className="map-progress-steps">
          {steps.map((s, i) => (
            <li
              key={i}
              className={
                i === steps.length - 1
                  ? "map-progress-step active"
                  : "map-progress-step done"
              }
            >
              {s}
            </li>
          ))}
        </ul>
      </div>
    );
  }
  if (error) {
    return (
      <div className="map-panel">
        <p className="muted small err">map failed: {error}</p>
      </div>
    );
  }
  if (!result) {
    return (
      <div className="map-panel">
        <p className="muted">Tap an element in the mirror to map it to code.</p>
      </div>
    );
  }

  const el = result.element;
  const resolution = result.resolution ?? null;
  const best = resolution?.best ?? null;
  const alternatives = resolution?.alternatives ?? [];
  const ragHits = resolution?.rag_hits ?? [];
  return (
    <div className="map-panel">
      <header className="map-head">
        <h3>UI element → code</h3>
        <span className="muted small">
          ({result.x}, {result.y})
          {result.foreground_activity ? ` · fg: ${result.foreground_activity}` : ""}
        </span>
      </header>

      {best && (
        <BestBanner
          best={best}
          alternativesCount={alternatives.length}
          onOpenInBrowser={onOpenInBrowser}
          onTraceBehaviour={onTraceBehaviour}
        />
      )}

      {!el ? (
        <p className="muted small">No UI element at that location (or uiautomator dump failed).</p>
      ) : (
        <dl className="element-grid">
          <dt>resource-id</dt>
          <dd><code>{el.resource_id || "(none)"}</code></dd>
          <dt>class</dt>
          <dd><code>{el.cls || "(none)"}</code></dd>
          <dt>text</dt>
          <dd>{el.text || <span className="muted">(empty)</span>}</dd>
          <dt>content-desc</dt>
          <dd>{el.content_desc || <span className="muted">(empty)</span>}</dd>
          <dt>bounds</dt>
          <dd>
            [{el.bounds[0]}, {el.bounds[1]}] – [{el.bounds[2]}, {el.bounds[3]}] · clickable={String(el.clickable)}
          </dd>
        </dl>
      )}

      {alternatives.length > 0 && (
        <ResolutionAlternatives
          alternatives={alternatives}
          onOpenInBrowser={onOpenInBrowser}
        />
      )}

      <h4 className="candidates-head">
        Code candidates{" "}
        <span className="muted small">({result.candidates.length})</span>
      </h4>
      {result.candidates.length === 0 ? (
        <p className="muted small">
          {result.decompile_status === "ready"
            ? "No handler references found for this resource id."
            : `Decompile not ready (status=${result.decompile_status ?? "unknown"}). Build the cache from the left pane to enable handler search.`}
        </p>
      ) : (
        <ol className="candidates">
          {result.candidates.map((c, i) => {
            const open = openIdx === i;
            const range = snippetRange(c);
            const rangeLabel =
              range.start === range.end
                ? `L${range.start}`
                : `L${range.start}–${range.end}`;
            return (
              <li key={`${c.file}:${c.line}:${i}`} className="candidate">
                <div className="candidate-head-row">
                  <button
                    type="button"
                    className="candidate-head"
                    onClick={async () => {
                      if (open) {
                        setOpenIdx(null);
                        setOpenSrc(null);
                        return;
                      }
                      setOpenIdx(i);
                      setOpenSrc(null);
                      if (appId) {
                        const text = await fetchSource(appId, c.file);
                        setOpenSrc(text ?? "(failed to load)");
                      }
                    }}
                  >
                    <span className={`kind-pill kind-${c.kind}`}>
                      {KIND_LABEL[c.kind]}
                    </span>
                    <code className="candidate-file">{c.file}</code>
                    <span className="muted small">{rangeLabel}</span>
                  </button>
                  {onOpenInBrowser && (
                    <button
                      type="button"
                      className="candidate-open-btn"
                      onClick={() =>
                        onOpenInBrowser(c.file, range.start, range.end)
                      }
                      title="Open in Code browser"
                      aria-label="Open in Code browser"
                    >
                      <IconOpenIn />
                    </button>
                  )}
                </div>
                <pre className="candidate-snippet">{c.snippet}</pre>
                {open && openSrc !== null && (
                  <div className="candidate-source-wrap">
                    <CodeView
                      source={openSrc}
                      scrollToLine={c.line}
                      highlightRange={[range.start, range.end]}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}

      {ragHits.length > 0 && (
        <RagHitsList
          ragHits={ragHits}
          ragQuery={resolution?.rag_query ?? null}
          ragError={resolution?.rag_error ?? null}
          onOpenInBrowser={onOpenInBrowser}
        />
      )}
      {ragHits.length === 0 && resolution?.rag_error && (
        <p className="muted small map-rag-note">
          RAG note: {resolution.rag_error}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components: best banner, alternatives row, RAG hits list

function SourceBadge({ source }: { source: ResolutionCandidate["source"] }) {
  const label = source === "rag" ? "RAG" : "regex";
  return (
    <span
      className={`source-badge source-${source}`}
      title={
        source === "rag"
          ? "From the Lane-1 vector index over decompiled sources"
          : "From the deterministic handler grep (findViewById / onClick / …)"
      }
    >
      {label}
    </span>
  );
}

function BestBanner({
  best,
  alternativesCount,
  onOpenInBrowser,
  onTraceBehaviour,
}: {
  best: ResolutionCandidate;
  alternativesCount: number;
  onOpenInBrowser?: Props["onOpenInBrowser"];
  onTraceBehaviour?: Props["onTraceBehaviour"];
}) {
  const { start, end } = resolutionRange(best);
  const fqMethod =
    best.method_name ??
    (best.kind === "rag" ? "(method)" : "(handler line)");
  return (
    <section className="best-banner" aria-label="Best handler match">
      <header className="best-banner-head">
        <span className="best-banner-label">Best handler</span>
        <SourceBadge source={best.source} />
        <span className={`kind-pill kind-${best.kind}`}>
          {RESOLUTION_KIND_LABEL[best.kind]}
        </span>
        <span className="best-banner-score" title="Fuser score (higher = stronger)">
          score {best.score.toFixed(3)}
        </span>
        {onOpenInBrowser && (
          <button
            type="button"
            className="best-banner-open ghost-mini"
            onClick={() => onOpenInBrowser(best.file, start, end)}
            title="Open in Code browser"
          >
            <IconOpenIn /> open
          </button>
        )}
        {onTraceBehaviour && (
          <button
            type="button"
            className="best-banner-trace ghost-mini"
            onClick={() => onTraceBehaviour(best)}
            title="Trace this behaviour in Lab → Trace mode (seeds the entry-method form from this handler)"
          >
            trace ↗
          </button>
        )}
      </header>
      <div className="best-banner-loc">
        <strong className="best-banner-class">
          {best.class_name ?? "?"}
          {best.method_name ? `.${fqMethod}()` : ""}
        </strong>{" "}
        <code className="best-banner-file">
          {best.file}:{best.line}
        </code>
      </div>
      {best.reasons.length > 0 && (
        <ul className="best-banner-reasons">
          {best.reasons.map((r, i) => (
            <li key={i}>{r}</li>
          ))}
        </ul>
      )}
      {alternativesCount > 0 && (
        <p className="muted small best-banner-alt-note">
          {alternativesCount} alternative{alternativesCount === 1 ? "" : "s"} below.
        </p>
      )}
      {best.snippet && <pre className="best-banner-snippet">{best.snippet}</pre>}
    </section>
  );
}

function ResolutionAlternatives({
  alternatives,
  onOpenInBrowser,
}: {
  alternatives: ResolutionCandidate[];
  onOpenInBrowser?: Props["onOpenInBrowser"];
}) {
  return (
    <details className="resolution-alts" open={false}>
      <summary>
        Alternatives <span className="muted small">({alternatives.length})</span>
      </summary>
      <ol className="resolution-alts-list">
        {alternatives.map((a, i) => {
          const { start, end } = resolutionRange(a);
          return (
            <li key={`${a.file}:${a.line}:${i}`} className="resolution-alt">
              <div className="resolution-alt-head">
                <SourceBadge source={a.source} />
                <span className={`kind-pill kind-${a.kind}`}>
                  {RESOLUTION_KIND_LABEL[a.kind]}
                </span>
                <code className="candidate-file">
                  {a.file}:{a.line}
                </code>
                <span className="muted small">
                  {a.score.toFixed(3)}
                </span>
                {onOpenInBrowser && (
                  <button
                    type="button"
                    className="candidate-open-btn"
                    onClick={() => onOpenInBrowser(a.file, start, end)}
                    title="Open in Code browser"
                    aria-label="Open in Code browser"
                  >
                    <IconOpenIn />
                  </button>
                )}
              </div>
              {a.reasons.length > 0 && (
                <p className="muted small resolution-alt-reasons">
                  {a.reasons.join(" · ")}
                </p>
              )}
            </li>
          );
        })}
      </ol>
    </details>
  );
}

function RagHitsList({
  ragHits,
  ragQuery,
  ragError,
  onOpenInBrowser,
}: {
  ragHits: ResolutionRagHit[];
  ragQuery: string | null;
  ragError: string | null;
  onOpenInBrowser?: Props["onOpenInBrowser"];
}) {
  return (
    <details className="rag-hits" open={false}>
      <summary>
        RAG hits <span className="muted small">({ragHits.length})</span>
        {ragQuery && (
          <span className="muted small rag-query-label" title={ragQuery}>
            · query: <em>{ragQuery}</em>
          </span>
        )}
      </summary>
      {ragError && <p className="muted small">RAG note: {ragError}</p>}
      <ol className="rag-hits-list">
        {ragHits.map((h, i) => (
          <li key={`${h.file}:${h.start_line}:${i}`} className="rag-hit">
            <div className="rag-hit-head">
              <SourceBadge source="rag" />
              <code className="candidate-file">
                {h.file}:{h.start_line}–{h.end_line}
              </code>
              <span className="muted small">
                {h.class_name}
                {h.method_name ? `.${h.method_name}` : ""} · {h.score.toFixed(3)}
              </span>
              {onOpenInBrowser && (
                <button
                  type="button"
                  className="candidate-open-btn"
                  onClick={() => onOpenInBrowser(h.file, h.start_line, h.end_line)}
                  title="Open in Code browser"
                  aria-label="Open in Code browser"
                >
                  <IconOpenIn />
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}
