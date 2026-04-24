export type LlmInfo = {
  model: string;
  base_url: string;
  provider: string;
};

let _cache: LlmInfo | null = null;

export async function getLlmInfo(): Promise<LlmInfo> {
  if (_cache) return _cache;
  try {
    const r = await fetch("/api/llm/info");
    if (!r.ok) {
      return { model: "unknown", base_url: "", provider: "ollama" };
    }
    const data = (await r.json()) as LlmInfo;
    _cache = data;
    return data;
  } catch {
    return { model: "unknown", base_url: "", provider: "ollama" };
  }
}
