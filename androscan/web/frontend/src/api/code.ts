export type DecompileStatus = {
  status: "missing" | "pending" | "ready" | "failed" | "unknown" | "error";
  sha?: string;
  apk_path?: string;
  started_ts?: number;
  finished_ts?: number;
  file_count?: number;
  sources_dir?: string;
  tree_available?: boolean;
  error?: string;
};

export type CodeClass = { name: string; methods: string[]; rel_path: string };
export type CodePackage = { name: string; classes: CodeClass[] };
export type CodeTree = { packages: CodePackage[] };

export async function getDecompileStatus(appId: string): Promise<DecompileStatus> {
  const r = await fetch(`/api/decompile/${encodeURIComponent(appId)}`);
  if (!r.ok) return { status: "error", error: `HTTP ${r.status}` };
  return (await r.json()) as DecompileStatus;
}

export async function startDecompile(appId: string): Promise<DecompileStatus> {
  const r = await fetch(`/api/decompile/${encodeURIComponent(appId)}`, { method: "POST" });
  if (!r.ok) return { status: "error", error: `HTTP ${r.status}` };
  return (await r.json()) as DecompileStatus;
}

export async function fetchTree(appId: string): Promise<CodeTree | null> {
  const r = await fetch(`/api/code/${encodeURIComponent(appId)}/tree`);
  if (!r.ok) return null;
  const body = await r.json();
  return (body.tree as CodeTree) ?? null;
}

export async function fetchSource(appId: string, relPath: string): Promise<string | null> {
  const r = await fetch(
    `/api/code/${encodeURIComponent(appId)}/file?path=${encodeURIComponent(relPath)}`,
  );
  if (!r.ok) return null;
  const body = await r.json();
  return (body.text as string) ?? null;
}
