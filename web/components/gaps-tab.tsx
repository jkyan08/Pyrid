"use client";

/**
 * Gaps tab: GET /gaps/{id} — coverage table (incl. zero-hit rows), missing
 * evidence, what would change the answer, and an honest-gap banner.
 */

import { useEffect, useState } from "react";
import type { GapsView } from "@/lib/types";
import { apiGaps } from "@/lib/api";
import { CitationText } from "./citation-text";
import { CoverageTable } from "./coverage-table";

export function GapsTab({ entityId }: { entityId: string }) {
  const [gaps, setGaps] = useState<GapsView | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGaps(entityId)
      .then((g) => {
        if (!cancelled) setGaps(g);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [entityId]);

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!gaps) return <p className="text-sm text-gray-400">Loading gaps…</p>;

  return (
    <div className="space-y-8">
      {gaps.honest_gap && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm font-semibold text-red-800">Honest gap</p>
          <p className="mt-1 text-sm text-red-700">
            No supported route exists for this entity in the current graph. The coverage below
            shows exactly what was searched; absence of evidence is reported, never papered over.
          </p>
        </div>
      )}

      <section>
        <h3 className="mb-3 text-sm font-semibold text-gray-900">Search coverage</h3>
        <CoverageTable coverage={gaps.coverage} />
      </section>

      <section>
        <h3 className="mb-3 text-sm font-semibold text-gray-900">Missing evidence</h3>
        {gaps.missing_evidence.length === 0 ? (
          <p className="text-sm text-gray-500">No missing-evidence items were derived.</p>
        ) : (
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-gray-700">
            {gaps.missing_evidence.map((m, i) => (
              <li key={i}>
                <CitationText text={m} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3 className="mb-3 text-sm font-semibold text-gray-900">What would change the answer</h3>
        {gaps.what_would_change.length === 0 ? (
          <p className="text-sm text-gray-500">Nothing recorded.</p>
        ) : (
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-gray-700">
            {gaps.what_would_change.map((m, i) => (
              <li key={i}>
                <CitationText text={m} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
