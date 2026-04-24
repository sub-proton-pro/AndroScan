import type { SeverityOverride, TriageEntry, TriageMap, TriageStatus } from "../types";

export async function fetchTriage(appId: string, runTs: string): Promise<TriageMap> {
  const r = await fetch(`/api/triage/${encodeURIComponent(appId)}/${encodeURIComponent(runTs)}`);
  if (!r.ok) return {};
  const body = (await r.json()) as { triage?: TriageMap };
  return body.triage ?? {};
}

export type TriageUpdate = {
  status?: TriageStatus;
  severity_override?: SeverityOverride;
  note?: string;
};

export async function postTriage(
  appId: string,
  runTs: string,
  findingId: string,
  update: TriageUpdate,
): Promise<{ ok: true; entry: TriageEntry } | { ok: false; error: string }> {
  const r = await fetch(
    `/api/triage/${encodeURIComponent(appId)}/${encodeURIComponent(runTs)}/${encodeURIComponent(findingId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
  );
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    return { ok: false, error: body?.detail ?? body?.error ?? `HTTP ${r.status}` };
  }
  return { ok: true, entry: body.entry as TriageEntry };
}
