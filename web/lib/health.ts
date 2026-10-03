/**
 * GET /health decision logic (README hard rule 10): the placeholder-data
 * ribbon shows ONLY when the API answered and reported mode === "mock".
 * An unreachable /health is a silent no-op — the static-fallback banner
 * already covers that case.
 */

export function shouldShowMockRibbon(health: unknown): boolean {
  return (
    typeof health === "object" &&
    health !== null &&
    (health as { mode?: unknown }).mode === "mock"
  );
}
