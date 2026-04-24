import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useWorkbench } from "../context/WorkbenchContext";
import type {
  Hypothesis,
  SeverityOverride,
  TriageEntry,
  TriageStatus,
} from "../types";

const STATUS_LABELS: Record<TriageStatus, string> = {
  confirmed: "Confirmed",
  false_positive: "False positive",
  suppressed: "Suppress",
  needs_review: "Needs review",
};

const SEVERITY_OPTIONS: Array<{ value: SeverityOverride; label: string }> = [
  { value: null, label: "(no override)" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
  { value: "informational", label: "Informational" },
];

// Map any severity-ish value (string label or 1-5 score) to a normalized
// lowercase key matching the CLI's _severity_label_colored() palette.
function severityKey(raw: unknown): string {
  if (typeof raw === "number") {
    return ({ 5: "critical", 4: "high", 3: "medium", 2: "low", 1: "informational" } as Record<
      number,
      string
    >)[raw] ?? "unknown";
  }
  const s = String(raw ?? "").trim().toLowerCase();
  if (!s) return "unknown";
  if (s.startsWith("crit")) return "critical";
  if (s.startsWith("high")) return "high";
  if (s.startsWith("med")) return "medium";
  if (s.startsWith("low")) return "low";
  if (s.startsWith("info")) return "informational";
  return s;
}

function severityLabel(key: string): string {
  return (
    {
      critical: "Critical",
      high: "High",
      medium: "Medium",
      low: "Low",
      informational: "Informational",
    } as Record<string, string>
  )[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
}

type Props = {
  finding: Hypothesis;
  index: number;
  selected: boolean;
  onSelect: () => void;
  triageEntry?: TriageEntry;
};

export function FindingCard({ finding, index, selected, onSelect, triageEntry }: Props) {
  const { updateTriage } = useWorkbench();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showNote, setShowNote] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [noteDraft, setNoteDraft] = useState(triageEntry?.note ?? "");

  // Use `||` (not `??`) so empty/whitespace ids — which legacy reports may
  // contain when the LLM omitted the field — also fall back to a synthetic id.
  const findingId =
    (typeof finding.id === "string" && finding.id.trim()) || `finding-${index}`;
  const rawSev =
    triageEntry?.severity_override ??
    finding.severity ??
    (finding as { exploitability?: unknown }).exploitability ??
    "unknown";
  const sevKey = severityKey(rawSev);
  const sevLabel = severityLabel(sevKey);

  const apply = async (
    update: { status?: TriageStatus; severity_override?: SeverityOverride; note?: string },
  ) => {
    setBusy(true);
    setError(null);
    const res = await updateTriage(findingId, update);
    setBusy(false);
    if (!res.ok) setError(res.error);
  };

  return (
    <li
      className={selected ? "finding-card finding-card-selected" : "finding-card"}
      onClick={onSelect}
    >
      <div className="finding-row">
        <button
          type="button"
          className="finding-collapse-btn"
          aria-label={collapsed ? "Expand finding" : "Collapse finding"}
          aria-expanded={!collapsed}
          onClick={(e) => {
            e.stopPropagation();
            setCollapsed((c) => !c);
          }}
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed ? "+" : "−"}
        </button>
        <span className={`sev-bracket sev-${sevKey}`} title={`Severity: ${sevLabel}`}>
          [{sevLabel}]
        </span>
        <strong className="finding-title">{finding.title ?? findingId}</strong>
        {!collapsed && (
          <span className="muted small">
            {finding.component_type ?? ""} {finding.component_name ?? ""}
          </span>
        )}
        {triageEntry?.status && (
          <span className={`triage-pill triage-${triageEntry.status}`}>
            {STATUS_LABELS[triageEntry.status]}
          </span>
        )}
      </div>
      {!collapsed && finding.description && (
        <div className="finding-desc">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{finding.description}</ReactMarkdown>
        </div>
      )}
      {!collapsed &&
        Array.isArray(finding.evidence_refs) &&
        finding.evidence_refs.length > 0 && (
          <div className="finding-evidence">
            <span className="muted small">evidence:</span>
            {finding.evidence_refs.map((ref) => (
              <code key={ref} className="evidence-ref">
                {ref}
              </code>
            ))}
          </div>
        )}
      {!collapsed && (
      <div className="triage-row" onClick={(e) => e.stopPropagation()}>
        {(Object.keys(STATUS_LABELS) as TriageStatus[]).map((s) => (
          <button
            key={s}
            type="button"
            className={`triage-btn triage-${s}${triageEntry?.status === s ? " triage-active" : ""}`}
            disabled={busy}
            onClick={() => apply({ status: s })}
            title={STATUS_LABELS[s]}
          >
            {STATUS_LABELS[s]}
          </button>
        ))}
        <label className="muted small triage-sev">
          severity:
          <select
            value={(triageEntry?.severity_override ?? "") as string}
            disabled={busy}
            onChange={(e) =>
              apply({
                severity_override: (e.target.value || null) as SeverityOverride,
              })
            }
          >
            {SEVERITY_OPTIONS.map((opt) => (
              <option key={String(opt.value)} value={(opt.value ?? "") as string}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="ghost-mini"
          onClick={() => setShowNote((v) => !v)}
          title="Add or edit a note"
        >
          {showNote ? "hide note" : triageEntry?.note ? "edit note" : "+ note"}
        </button>
      </div>
      )}
      {!collapsed && showNote && (
        <div className="triage-note" onClick={(e) => e.stopPropagation()}>
          <textarea
            rows={2}
            value={noteDraft}
            placeholder="Why is this confirmed / suppressed / etc?"
            onChange={(e) => setNoteDraft(e.target.value)}
            maxLength={2000}
          />
          <div className="triage-note-row">
            <span className="muted small">{noteDraft.length}/2000</span>
            <button
              type="button"
              className="ghost"
              disabled={busy || noteDraft === (triageEntry?.note ?? "")}
              onClick={() => apply({ note: noteDraft })}
            >
              Save note
            </button>
          </div>
        </div>
      )}
      {!collapsed && triageEntry?.note && !showNote && (
        <p className="triage-saved-note muted small">note: {triageEntry.note}</p>
      )}
      {!collapsed && error && <p className="muted small err">triage failed: {error}</p>}
    </li>
  );
}
