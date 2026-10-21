
export function readHash(): Record<string, string> {
  const raw = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(raw);
  const out: Record<string, string> = {};
  params.forEach((v, k) => {
    out[k] = v;
  });
  return out;
}

export function writeHash(patch: Record<string, string | null | undefined>): void {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
  for (const [k, v] of Object.entries(patch)) {
    if (v === null || v === undefined || v === "") params.delete(k);
    else params.set(k, v);
  }
  const s = params.toString();
  const next = s ? `#${s}` : "";
  if (next === window.location.hash) return;
  const url = `${window.location.pathname}${window.location.search}${next}`;
  window.history.replaceState(null, "", url);
}

export function onHashChange(handler: () => void): () => void {
  window.addEventListener("hashchange", handler);
  return () => window.removeEventListener("hashchange", handler);
}
