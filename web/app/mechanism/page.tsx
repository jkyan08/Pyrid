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
      <header className="relative overflow-hidden rounded-3xl bg-[#192c30] px-6 py-10 text-white shadow-xl shadow-teal-900/10 sm:px-10 sm:py-12">
        <div className="absolute -right-12 -top-24 size-80 rounded-full border border-teal-300/20" aria-hidden="true" />
        <div className="absolute -right-2 -top-12 size-56 rounded-full border border-teal-300/20" aria-hidden="true" />
        <div className="relative max-w-3xl">
          <span className="mb-6 flex size-14 items-center justify-center rounded-2xl border border-amber-300/30 bg-amber-300/10 text-amber-200">
            <TypeGlyph type="Mechanism" size={31} />
          </span>
          <p className="text-xs font-bold uppercase tracking-[0.2em] text-amber-300">Direction first · disease second</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] sm:text-5xl">Find a mechanism match</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-teal-50/80">
            Choose an intervention direction to find diseases with matching biology. Candidate leads
            and excluded conditions are separated, with evidence level and exclusion reasons visible.
          </p>
        </div>
      </header>

      <div className="max-w-3xl">
      <p className="mb-3 text-xs font-bold uppercase tracking-[0.16em] text-slate-500">What direction would the intervention take?</p>
      <div
        className="grid grid-cols-2 gap-2 rounded-2xl border border-slate-200 bg-white p-2 shadow-sm"
        role="radiogroup"
        aria-label="Mechanism direction"
      >
        {(["dampen", "boost"] as Direction[]).map((d) => {
          const selected = direction === d;
          const selectedCls =
            d === "dampen"
              ? "bg-amber-50 text-amber-950 shadow-sm ring-1 ring-amber-300"
              : "bg-teal-50 text-teal-950 shadow-sm ring-1 ring-teal-300";
          return (
            <button
              key={d}
              role="radio"
              aria-checked={selected}
              onClick={() => setDirection(d)}
              className={`rounded-xl px-4 py-4 text-base font-semibold transition-colors sm:py-5 ${
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
    <div className="grid gap-7 lg:grid-cols-[minmax(0,1.5fr)_minmax(320px,.8fr)] lg:items-start">
      {error && <p className="text-sm text-red-700">{error}</p>}
      {!data && !error && (
        <p className="flex items-center gap-2 text-sm text-gray-400">
          <MotionLoader variant="dot-ring" size={16} label="Searching mechanisms" />
          Searching mechanisms…
        </p>
      )}

      {data && (
        <>
          <section className="rounded-2xl border border-teal-200 bg-teal-50/50 p-5 sm:p-6">
            <p className="text-xs font-bold uppercase tracking-[0.15em] text-teal-700">Potential leads</p>
            <h2 className="mb-4 mt-1 text-2xl font-semibold tracking-tight text-slate-950">
              Matching diseases <span className="text-teal-700">({data.results.length})</span>
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
                    className={`rounded-xl border border-slate-200 border-l-4 bg-white p-5 shadow-sm transition-transform hover:-translate-y-0.5 motion-reduce:transform-none ${theme.accent}`}
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

          <section className="rounded-2xl border border-rose-200 bg-rose-50/50 p-5 sm:p-6">
            <p className="text-xs font-bold uppercase tracking-[0.15em] text-rose-700">Direction mismatch</p>
            <h2 className="mb-2 mt-1 text-2xl font-semibold tracking-tight text-slate-950">
              Excluded <span className="text-rose-700">({data.excluded.length})</span>
            </h2>
            <p className="mb-4 text-sm leading-6 text-slate-600">These conditions are shown so a mismatch cannot be mistaken for a lead.</p>
            {data.excluded.length === 0 && (
              <p className="text-sm text-gray-500">Nothing was excluded for this direction.</p>
            )}
            {/* Demoted on purpose: muted gray card + red-tinted left border,
                so excluded items never read as leads. */}
            <ul className="space-y-2">
              {data.excluded.map((e) => (
                <li
                  key={e.node.id}
                  className="flex flex-col gap-2 rounded-xl border border-rose-200 bg-white/80 p-4"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="rounded-sm bg-red-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-red-700/80">
                      excluded
                    </span>
                    <Link
                      href={`/entity/${encodeURIComponent(e.node.id)}?excluded=${direction}`}
                      className="text-sm text-gray-600 underline decoration-gray-300 underline-offset-2 hover:text-gray-900"
                    >
                      {e.node.label}
                    </Link>
                  </span>
                  <span className="text-xs leading-5 text-rose-900">{e.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
