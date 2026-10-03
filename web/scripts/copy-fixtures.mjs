/**
 * Copies the graph + explanations for static-fallback mode into web/public/.
 * Loader order mirrors api/CONTRACT.md:
 *   graph:        data/build/graph.json → data/build/graph.mock.json → api/fixtures/graph.mock.json
 *   explanations: data/build/explanations.json → api/fixtures/explanations.mock.json
 * Runs as predev/prebuild. Missing sources are tolerated (the API may still
 * be reachable); existing copies in public/ are then left as-is.
 */
import { copyFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = join(here, "..");
const repoRoot = join(webRoot, "..");
const publicDir = join(webRoot, "public");
mkdirSync(publicDir, { recursive: true });

function copyFirst(candidates, destName) {
  for (const rel of candidates) {
    const src = join(repoRoot, rel);
    if (existsSync(src)) {
      copyFileSync(src, join(publicDir, destName));
      console.log(`[copy-fixtures] ${rel} -> public/${destName}`);
      return true;
    }
  }
  console.warn(`[copy-fixtures] no source found for ${destName} (tried: ${candidates.join(", ")})`);
  return false;
}

copyFirst(
  ["data/build/graph.json", "data/build/graph.mock.json", "api/fixtures/graph.mock.json"],
  "graph.mock.json"
);
copyFirst(
  ["data/build/explanations.json", "api/fixtures/explanations.mock.json"],
  "explanations.mock.json"
);
