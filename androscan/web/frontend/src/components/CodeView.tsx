import {
  Fragment,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  FONT_SIZE_REM,
  useCodeViewPrefs,
  type CodeViewPrefs,
} from "../util/codeViewPrefs";
import { findMethodBodyRange, tokenize, type Token } from "../util/highlight";
import { IconClose, IconGear, IconSearch } from "./Icons";

type Props = {
  source: string;
  /** Optional method name whose body should be visually emphasised
   *  (gradient tint on the body lines + accent on the gutter). */
  emphasizeMethod?: string | null;
  /** When set, scrolls to the line on first render (1-indexed). */
  scrollToLine?: number | null;
  /** Inclusive 1-indexed line range to keep persistently highlighted
   *  (e.g. the snippet range of a UI Mapping code candidate). The
   *  highlight survives scrolling and tab switches; it's cleared by the
   *  parent when the user picks a different target. */
  highlightRange?: [number, number] | null;
};

type LineRow = {
  tokens: Token[];
  inMethod: boolean;
  /** Char offset of the first token of this line in the original source.
   *  Used to translate global match offsets into per-line column offsets. */
  startOffset: number;
};

type Match = { line: number; start: number; end: number };

/**
 * Read-only code viewer. Layout is a CSS grid of (gutter, body) rows so
 * line numbers stay aligned even when word-wrap is enabled. Built-in
 * floating Find bar (top-right) and Gear popover for view preferences
 * (persisted via ``useCodeViewPrefs``).
 */
export function CodeView({
  source,
  emphasizeMethod,
  scrollToLine,
  highlightRange,
}: Props) {
  const [prefs, setPrefs] = useCodeViewPrefs();

  const lines = useMemo(
    () => splitIntoLines(source, emphasizeMethod ?? null),
    [source, emphasizeMethod],
  );

  // ---- Find state -------------------------------------------------------
  const [findOpen, setFindOpen] = useState(false);
  const [findQuery, setFindQuery] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [useRegex, setUseRegex] = useState(false);
  const [activeMatchIdx, setActiveMatchIdx] = useState(0);
  const findInputRef = useRef<HTMLInputElement | null>(null);

  // Reset find when the file changes.
  useEffect(() => {
    setFindQuery("");
    setActiveMatchIdx(0);
  }, [source]);

  const { matches, regexError } = useMemo(
    () => findMatches(source, findQuery, caseSensitive, useRegex),
    [source, findQuery, caseSensitive, useRegex],
  );

  useEffect(() => {
    if (matches.length === 0) {
      setActiveMatchIdx(0);
    } else if (activeMatchIdx >= matches.length) {
      setActiveMatchIdx(0);
    }
  }, [matches.length, activeMatchIdx]);

  // ---- Gear menu state --------------------------------------------------
  const [gearOpen, setGearOpen] = useState(false);
  const gearRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!gearOpen) return;
    const onDoc = (e: MouseEvent) => {
      const tgt = e.target as Node | null;
      if (gearRef.current && tgt && !gearRef.current.contains(tgt)) {
        setGearOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [gearOpen]);

  // ---- Current line state ----------------------------------------------
  const [currentLine, setCurrentLine] = useState<number | null>(null);

  // ---- Match index → DOM line refs --------------------------------------
  // We store the gutter ``.code-line-num`` element rather than the wrapping
  // ``.code-row`` because rows use ``display: contents`` and therefore have
  // no layout box for ``scrollIntoView`` to target.
  const rowRefs = useRef<Record<number, HTMLElement | null>>({});

  // Scroll into view whenever ``scrollToLine`` changes (and once after
  // the source first finishes loading with a target already pending).
  // Running on every value change lets the parent jump to a new
  // candidate snippet inside the same file without unmount/remount
  // tricks. The destination row is also painted with a persistent
  // ``highlighted`` class via ``highlightRange`` so it stays visible
  // after the scroll completes.
  useEffect(() => {
    if (!scrollToLine) return;
    if (lines.length === 0) return;
    const el = rowRefs.current[scrollToLine];
    if (el) el.scrollIntoView({ block: "center" });
  }, [scrollToLine, lines.length]);

  // Active match → scroll into view whenever the index changes.
  useEffect(() => {
    if (matches.length === 0) return;
    const m = matches[activeMatchIdx];
    if (!m) return;
    const el = rowRefs.current[m.line];
    if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeMatchIdx, matches]);

  const goNext = () => {
    if (matches.length === 0) return;
    setActiveMatchIdx((i) => (i + 1) % matches.length);
  };
  const goPrev = () => {
    if (matches.length === 0) return;
    setActiveMatchIdx((i) => (i - 1 + matches.length) % matches.length);
  };

  const openFind = () => {
    setFindOpen(true);
    setTimeout(() => findInputRef.current?.focus(), 0);
  };
  const closeFind = () => {
    setFindOpen(false);
    setFindQuery("");
  };

  // ---- Render -----------------------------------------------------------
  const cls = [
    "code-view",
    `theme-${prefs.theme}`,
    prefs.wordWrap ? "word-wrap" : "",
    prefs.showWhitespace ? "show-ws" : "",
    prefs.highlightCurrentLine ? "current-on" : "",
    emphasizeMethod ? "has-emphasis" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={cls}
      style={{ fontSize: FONT_SIZE_REM[prefs.fontSize] }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && findOpen) {
          e.preventDefault();
          closeFind();
        }
      }}
    >
      {/* Floating top-right toolbar: find + gear */}
      <div className="code-toolbar" onClick={(e) => e.stopPropagation()}>
        {findOpen ? (
          <div className="code-find" role="search">
            <input
              ref={findInputRef}
              type="text"
              className="code-find-input"
              placeholder="find in code"
              value={findQuery}
              onChange={(e) => setFindQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (e.shiftKey) goPrev();
                  else goNext();
                }
              }}
              spellCheck={false}
              autoComplete="off"
            />
            <span className="code-find-count">
              {regexError
                ? "regex error"
                : findQuery
                  ? matches.length === 0
                    ? "0 / 0"
                    : `${activeMatchIdx + 1} / ${matches.length}`
                  : ""}
            </span>
            <button
              type="button"
              className={caseSensitive ? "code-find-tog active" : "code-find-tog"}
              onClick={() => setCaseSensitive((v) => !v)}
              title="Match case"
              aria-pressed={caseSensitive}
            >
              Aa
            </button>
            <button
              type="button"
              className={useRegex ? "code-find-tog active" : "code-find-tog"}
              onClick={() => setUseRegex((v) => !v)}
              title="Regular expression"
              aria-pressed={useRegex}
            >
              .*
            </button>
            <button
              type="button"
              className="code-find-nav"
              onClick={goPrev}
              disabled={matches.length === 0}
              title="Previous match (Shift+Enter)"
              aria-label="Previous match"
            >
              ◀
            </button>
            <button
              type="button"
              className="code-find-nav"
              onClick={goNext}
              disabled={matches.length === 0}
              title="Next match (Enter)"
              aria-label="Next match"
            >
              ▶
            </button>
            <button
              type="button"
              className="code-find-close"
              onClick={closeFind}
              title="Close find (Esc)"
              aria-label="Close find"
            >
              <IconClose />
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="code-toolbtn"
            onClick={openFind}
            title="Find in code"
            aria-label="Find in code"
          >
            <IconSearch />
          </button>
        )}

        <div className="code-gear-wrap" ref={gearRef}>
          <button
            type="button"
            className={gearOpen ? "code-toolbtn active" : "code-toolbtn"}
            onClick={() => setGearOpen((v) => !v)}
            title="View options"
            aria-label="View options"
            aria-expanded={gearOpen}
          >
            <IconGear />
          </button>
          {gearOpen && (
            <GearMenu prefs={prefs} setPrefs={setPrefs} />
          )}
        </div>
      </div>

      <div className="code-rows">
        {lines.map((line, i) => {
          const lineNo = i + 1;
          const lineMatches = matches
            .filter((m) => m.line === lineNo)
            .map((m) => ({
              start: m.start - line.startOffset,
              end: m.end - line.startOffset,
              active:
                matches.length > 0 && matches[activeMatchIdx] === m,
            }));
          const inHighlight =
            !!highlightRange &&
            lineNo >= highlightRange[0] &&
            lineNo <= highlightRange[1];
          const rowCls = [
            "code-row",
            line.inMethod ? "in-method" : "",
            prefs.highlightCurrentLine && currentLine === lineNo
              ? "current"
              : "",
            inHighlight ? "highlighted" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <div
              key={i}
              className={rowCls}
              onClick={() => {
                if (prefs.highlightCurrentLine) setCurrentLine(lineNo);
              }}
            >
              <span
                ref={(el) => {
                  rowRefs.current[lineNo] = el;
                }}
                className={
                  line.inMethod
                    ? "code-line-num in-method"
                    : "code-line-num"
                }
              >
                {lineNo}
              </span>
              <span className="code-line">
                {renderLine(line.tokens, lineMatches, prefs.showWhitespace)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Gear popover
// ---------------------------------------------------------------------------

function GearMenu({
  prefs,
  setPrefs,
}: {
  prefs: CodeViewPrefs;
  setPrefs: (p: Partial<CodeViewPrefs>) => void;
}) {
  return (
    <div className="code-gear" role="menu">
      <h4 className="code-gear-title">View options</h4>

      <label className="code-gear-row">
        <input
          type="checkbox"
          checked={prefs.wordWrap}
          onChange={(e) => setPrefs({ wordWrap: e.target.checked })}
        />
        <span>Word wrap</span>
      </label>
      <label className="code-gear-row">
        <input
          type="checkbox"
          checked={prefs.showWhitespace}
          onChange={(e) => setPrefs({ showWhitespace: e.target.checked })}
        />
        <span>Show whitespace</span>
      </label>
      <label className="code-gear-row">
        <input
          type="checkbox"
          checked={prefs.highlightCurrentLine}
          onChange={(e) =>
            setPrefs({ highlightCurrentLine: e.target.checked })
          }
        />
        <span>Highlight current line</span>
      </label>

      <div className="code-gear-row">
        <span>Font size</span>
        <div className="code-gear-seg" role="radiogroup" aria-label="Font size">
          {(["s", "m", "l"] as const).map((sz) => (
            <button
              type="button"
              key={sz}
              role="radio"
              aria-checked={prefs.fontSize === sz}
              className={
                prefs.fontSize === sz
                  ? "code-gear-seg-btn active"
                  : "code-gear-seg-btn"
              }
              onClick={() => setPrefs({ fontSize: sz })}
            >
              {sz.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      <div className="code-gear-row">
        <span>Theme</span>
        <div className="code-gear-seg" role="radiogroup" aria-label="Theme">
          <button
            type="button"
            role="radio"
            aria-checked={prefs.theme === "dark"}
            className={
              prefs.theme === "dark"
                ? "code-gear-seg-btn active"
                : "code-gear-seg-btn"
            }
            onClick={() => setPrefs({ theme: "dark" })}
          >
            Dark
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={prefs.theme === "hc"}
            className={
              prefs.theme === "hc"
                ? "code-gear-seg-btn active"
                : "code-gear-seg-btn"
            }
            onClick={() => setPrefs({ theme: "hc" })}
          >
            High contrast
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Source → lines + match search + line renderer
// ---------------------------------------------------------------------------

function splitIntoLines(
  src: string,
  emphasizeMethod: string | null,
): LineRow[] {
  const tokens = tokenize(src);
  const range = emphasizeMethod
    ? findMethodBodyRange(src, emphasizeMethod)
    : null;

  const rows: LineRow[] = [];
  let cur: Token[] = [];
  let pos = 0;
  let inMethod = false;
  let lineStart = 0;

  const flush = () => {
    rows.push({ tokens: cur, inMethod, startOffset: lineStart });
    cur = [];
    inMethod = false;
    lineStart = pos;
  };

  for (const tok of tokens) {
    const parts = tok.text.split("\n");
    for (let pi = 0; pi < parts.length; pi += 1) {
      const part = parts[pi];
      if (part.length > 0) {
        const tokenStart = pos;
        const tokenEnd = pos + part.length;
        if (range && tokenStart < range.end && tokenEnd > range.start) {
          inMethod = true;
        }
        cur.push({ kind: tok.kind, text: part });
        pos += part.length;
      }
      if (pi < parts.length - 1) {
        // newline char itself
        pos += 1;
        flush();
      }
    }
  }
  if (cur.length > 0 || rows.length === 0) flush();
  return rows;
}

function findMatches(
  src: string,
  query: string,
  caseSensitive: boolean,
  useRegex: boolean,
): { matches: Match[]; regexError: boolean } {
  if (!query) return { matches: [], regexError: false };
  let re: RegExp;
  try {
    const pattern = useRegex
      ? query
      : query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    re = new RegExp(pattern, caseSensitive ? "g" : "gi");
  } catch {
    return { matches: [], regexError: true };
  }

  const out: Match[] = [];
  let m: RegExpExecArray | null;
  // Build cumulative line-start offsets once for O(log n) match→line lookups.
  const lineStartByOffset: number[] = [0];
  for (let i = 0; i < src.length; i += 1) {
    if (src[i] === "\n") lineStartByOffset.push(i + 1);
  }
  const offsetToLine = (off: number): number => {
    let lo = 0;
    let hi = lineStartByOffset.length - 1;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (lineStartByOffset[mid] <= off) lo = mid;
      else hi = mid - 1;
    }
    return lo + 1; // 1-indexed
  };

  while ((m = re.exec(src)) !== null) {
    if (m[0].length === 0) {
      re.lastIndex += 1;
      continue;
    }
    const start = m.index;
    const end = m.index + m[0].length;
    out.push({ line: offsetToLine(start), start, end });
    if (out.length > 5000) break; // sanity cap
  }
  return { matches: out, regexError: false };
}

function renderLine(
  tokens: Token[],
  matches: { start: number; end: number; active: boolean }[],
  showWs: boolean,
): ReactNode[] {
  const out: ReactNode[] = [];
  let pos = 0;
  let key = 0;
  for (const tok of tokens) {
    const tokStart = pos;
    const tokEnd = pos + tok.text.length;

    // Build cut points for this token: token boundaries + any match
    // boundary that falls strictly inside the token.
    const cuts: number[] = [tokStart, tokEnd];
    for (const m of matches) {
      if (m.start < tokEnd && m.end > tokStart) {
        if (m.start > tokStart) cuts.push(m.start);
        if (m.end < tokEnd) cuts.push(m.end);
      }
    }
    cuts.sort((a, b) => a - b);
    const unique = Array.from(new Set(cuts));

    for (let i = 0; i + 1 < unique.length; i += 1) {
      const segStart = unique[i];
      const segEnd = unique[i + 1];
      if (segEnd === segStart) continue;
      const text = tok.text.slice(segStart - tokStart, segEnd - tokStart);
      let matchClass = "";
      for (const m of matches) {
        if (m.start <= segStart && m.end >= segEnd) {
          matchClass = m.active ? " tk-match tk-match-active" : " tk-match";
          break;
        }
      }
      out.push(
        <span key={key++} className={`tk tk-${tok.kind}${matchClass}`}>
          {showWs ? renderWs(text) : text}
        </span>,
      );
    }

    pos = tokEnd;
  }
  if (out.length === 0) {
    // Empty line: render a zero-width placeholder so grid rows have height.
    out.push(<Fragment key="blank">{"\u200b"}</Fragment>);
  }
  return out;
}

function renderWs(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // Split into runs of (printable) vs (space/tab) so non-whitespace stays
  // crisp. We deliberately leave NBSP / unusual unicode whitespace alone.
  const re = /([ \t]+)|([^ \t]+)/g;
  let m: RegExpExecArray | null;
  let key = 0;
  while ((m = re.exec(text)) !== null) {
    if (m[1]) {
      const visual = m[1].replace(/ /g, "·").replace(/\t/g, "→");
      out.push(
        <span key={key++} className="tk-ws">
          {visual}
        </span>,
      );
    } else if (m[2]) {
      out.push(<Fragment key={key++}>{m[2]}</Fragment>);
    }
  }
  return out;
}
