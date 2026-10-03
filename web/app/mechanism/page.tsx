"use client";

/**
 * Mechanism search (Priya's journey): dampen/boost direction toggle,
 * results ranked by evidence level with their communities, and an
 * "Excluded" section listing each excluded disease WITH its reason.
 *
 * This page has its own visual identity, distinct from entity pages: an
 * amber→teal mechanism header band with a big mechanism glyph, and the
 * direction toggle as a LARGE segmented control (amber = dampen,
 * teal = boost). Ranked candidates are elevated cards with a type accent
 * and an evidence-level chip color-scaled by study rank; excluded items
 * are visually demoted (muted gray card, red-tinted left border) so they
 * clearly read as "not a lead" — but the reason is shown, never hidden.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import type { MechanismSearchResponse } from "@/lib/types";
import { apiMechanismSearch } from "@/lib/api";
import { themeOf } from "@/lib/type-theme";
import { EvidenceLevelChip, TypeBadge, TypeGlyph } from "@/components/badges";
import MotionLoader from "@/components/smoothui/motion-loader";

type Direction = "dampen" | "boost";

export default function MechanismPage() {
  const [direction, setDirection] = useState<Direction>("dampen");

  return (
    <div className="space-y-8">
      <header className="flex items-start gap-4 rounded-xl border border-amber-200/70 border-l-4 border-l-amber-400 bg-gradient-to-r from-amber-50/80 via-white to-teal-50/70 px-5 py-4">
        <span className="mt-0.5 flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white/90 text-amber-600 shadow-sm">
          <TypeGlyph type="Mechanism" size={28} />
        </span>
        <div className="space-y-1.5">
          <h1 className="text-xl font-semibold tracking-tight text-gray-900 sm:text-2xl">
            Mechanism search
          </h1>
          <p className="text-sm leading-relaxed text-gray-500">
            Start from an intervention direction instead of a diagnosis: which diseases are driven
            by a mechanism this direction could address? Diseases where the direction is wrong are
            excluded — with the reason shown, not hidden.
          </p>
        </div>
      </header>

      {/* Large segmented direction control: amber = dampen, teal = boost. */}
      <div
        className="grid grid-cols-2 gap-1 rounded-xl border border-gray-200 bg-gray-100 p-1"
        role="radiogroup"
        aria-label="Mechanism direction"
      >
        {(["dampen", "boost"] as Direction[]).map((d) => {
          const selected = direction === d;
          const selectedCls =
            d === "dampen"
              ? "bg-white text-amber-900 shadow-sm ring-1 ring-amber-300"
              : "bg-white text-teal-900 shadow-sm ring-1 ring-teal-300";
          return (
            <button
              key={d}
              role="radio"
              aria-checked={selected}
              onClick={() => setDirection(d)}
              className={`rounded-lg px-4 py-3 text-base font-semibold transition-colors sm:py-3.5 ${
                selected ? selectedCls : "text-gray-500 hover:text-gray-800"
              }`}
            >
              <span aria-hidden="true" className="mr-1.5">
                {d === "dampen" ? "↓" : "↑"}
              </span>
              {d} signaling
            </button>
          );
        })}
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
              {data.results.map((r, i) => {
                const theme = themeOf(r.node.type);
                return (
                  <li
                    key={r.node.id}
                    className={`rounded-xl border border-gray-200 border-l-4 bg-white p-4 shadow-sm ${theme.accent}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-gray-400">#{i + 1}</span>
                      <TypeGlyph type={r.node.type} size={16} className={theme.text} />
                      <Link
                        href={`/entity/${encodeURIComponent(r.node.id)}`}
                        className="text-sm font-medium text-gray-900 underline decoration-gray-300 underline-offset-2 hover:decoration-sky-400"
                      >
                        {r.node.label}
                      </Link>
                      <TypeBadge type={r.node.type} />
                      <EvidenceLevelChip level={r.evidence_level} />
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
                );
              })}
            </ul>
          </section>

          <section>
            <h2 className="mb-3 text-sm font-semibold text-gray-900">
              Excluded ({data.excluded.length})
            </h2>
            {data.excluded.length === 0 && (
              <p className="text-sm text-gray-500">Nothing was excluded for this direction.</p>
            )}
            {/* Demoted on purpose: muted gray card + red-tinted left border,
                so excluded items never read as leads. */}
            <ul className="space-y-2">
              {data.excluded.map((e) => (
                <li
                  key={e.node.id}
                  className="flex flex-col gap-1 rounded-lg border border-gray-200/70 border-l-4 border-l-red-300 bg-gray-50/80 p-3 sm:flex-row sm:items-baseline sm:gap-3"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="rounded-sm bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-700/80">
                      excluded
                    </span>
                    <Link
                      href={`/entity/${encodeURIComponent(e.node.id)}`}
                      className="text-sm text-gray-600 underline decoration-gray-300 underline-offset-2 hover:text-gray-900"
                    >
                      {e.node.label}
                    </Link>
                  </span>
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
