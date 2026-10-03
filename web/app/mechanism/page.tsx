"use client";

/**
 * Mechanism search (Priya's journey): dampen/boost direction toggle,
 * results ranked by evidence level with their communities, and an
 * "Excluded" section listing each excluded disease WITH its reason.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import type { MechanismSearchResponse } from "@/lib/types";
import { apiMechanismSearch } from "@/lib/api";
import { StatusChip, TypeBadge } from "@/components/badges";
import MotionLoader from "@/components/smoothui/motion-loader";

type Direction = "dampen" | "boost";

export default function MechanismPage() {
  const [direction, setDirection] = useState<Direction>("dampen");

  return (
    <div className="space-y-8">
      <header className="space-y-2">
        <h1 className="text-xl font-semibold tracking-tight text-gray-900">Mechanism search</h1>
        <p className="text-sm leading-relaxed text-gray-500">
          Start from an intervention direction instead of a diagnosis: which diseases are driven
          by a mechanism this direction could address? Diseases where the direction is wrong are
          excluded — with the reason shown, not hidden.
        </p>
      </header>

      <div className="flex items-center gap-2" role="radiogroup" aria-label="Mechanism direction">
        {(["dampen", "boost"] as Direction[]).map((d) => (
          <button
            key={d}
            role="radio"
            aria-checked={direction === d}
            onClick={() => setDirection(d)}
            className={`rounded-full border px-4 py-1.5 text-sm font-medium ${
              direction === d
                ? "border-gray-900 bg-gray-900 text-white"
                : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
            }`}
          >
            {d} signaling
          </button>
        ))}
      </div>

      {/* key={direction} remounts the results block, so its fetch state
          resets without synchronous setState inside the effect */}
      <MechanismResults key={direction} direction={direction} />
    </div>
  );
}

function MechanismResults({ direction }: { direction: Direction }) {
  const [data, setData] = useState<MechanismSearchResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiMechanismSearch(direction)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [direction]);

  return (
    <div className="space-y-8">
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!data && !error && (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <MotionLoader variant="dot-ring" size={16} label="Searching mechanisms" />
          Searching mechanisms…
        </p>
      )}

      {data && (
        <>
          <section>
            <h2 className="mb-3 text-sm font-semibold text-gray-900">
              Candidates ({data.results.length})
            </h2>
            {data.results.length === 0 && (
              <p className="text-sm text-gray-500">
                No disease in the current graph is driven by a mechanism matching this direction.
              </p>
            )}
            <ul className="space-y-4">
              {data.results.map((r) => (
                <li key={r.node.id} className="rounded-lg border border-gray-200 p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/entity/${encodeURIComponent(r.node.id)}`}
                      className="text-sm font-medium text-gray-900 underline decoration-gray-300 underline-offset-2 hover:decoration-sky-400"
                    >
                      {r.node.label}
                    </Link>
                    <TypeBadge type={r.node.type} />
                    <StatusChip label={r.evidence_level} />
                  </div>
                  <p className="mt-1 text-xs text-gray-500">
                    Mechanism:{" "}
                    <Link
                      href={`/entity/${encodeURIComponent(r.mechanism.id)}`}
                      className="underline decoration-gray-300 underline-offset-2 hover:text-sky-700"
                    >
                      {r.mechanism.label}
                    </Link>
                  </p>
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                      Communities:
                    </span>
                    {r.communities.length === 0 && (
                      <span className="text-xs text-gray-400">
                        none linked in the current graph
                      </span>
                    )}
                    {r.communities.map((c) => (
                      <Link
                        key={c.id}
                        href={`/entity/${encodeURIComponent(c.id)}`}
                        className="rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-[11px] text-gray-700 hover:border-sky-300 hover:bg-sky-50"
                      >
                        {c.label}
                      </Link>
                    ))}
                  </div>
                  <p className="mt-2 text-[11px] text-gray-400">ranked by: {r.ranked_by}</p>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="mb-3 text-sm font-semibold text-gray-900">
              Excluded ({data.excluded.length})
            </h2>
            {data.excluded.length === 0 && (
              <p className="text-sm text-gray-500">Nothing was excluded for this direction.</p>
            )}
            <ul className="space-y-2">
              {data.excluded.map((e) => (
                <li
                  key={e.node.id}
                  className="flex flex-col gap-1 rounded-lg border border-gray-100 bg-gray-50/60 p-3 sm:flex-row sm:items-baseline sm:gap-3"
                >
                  <Link
                    href={`/entity/${encodeURIComponent(e.node.id)}`}
                    className="text-sm text-gray-700 underline decoration-gray-300 underline-offset-2 hover:text-gray-900"
                  >
                    {e.node.label}
                  </Link>
                  <span className="text-xs text-gray-500">{e.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
