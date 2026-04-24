/**
 * Tiny client for ``/api/rag/*`` — currently only the rebuild endpoint is
 * needed from the UI (status is already part of ``/api/status/apps/{app_id}``).
 *
 * The rebuild endpoint returns 409 if the decompile cache isn't ready yet;
 * callers should gate the button on ``decompile.status === "ready"``.
 */

export type RagRebuildOk = {
  app_id: string;
  sha: string;
  kicked: boolean;
};

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status: number };

export async function rebuildRagIndex(appId: string): Promise<ApiResult<RagRebuildOk>> {
  try {
    const r = await fetch(`/api/rag/${encodeURIComponent(appId)}/rebuild`, {
      method: "POST",
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      return {
        ok: false,
        error: body?.detail ?? `HTTP ${r.status}`,
        status: r.status,
      };
    }
    return { ok: true, data: body as RagRebuildOk };
  } catch (e) {
    return { ok: false, error: (e as Error).message, status: 0 };
  }
}
