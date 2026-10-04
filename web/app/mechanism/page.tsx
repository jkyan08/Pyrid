"use client";

/**
 * Mechanism search (Priya's journey): dampen/boost direction toggle,
 * results ranked by evidence level with their communities, and an
 * "Excluded" section listing each excluded disease WITH its reason.
 *
 * This page has its own visual identity, distinct from entity pages: a
 * flat pine mechanism header with a big mechanism glyph, and the
 * direction toggle as a LARGE segmented control (ochre = dampen,
 * fern = boost). Ranked candidates are cards with a type accent and an
 * evidence-level chip color-scaled by study rank; excluded items are
 * visually demoted (muted card, rust-tinted framing) so they clearly
 * read as "not a lead" — but the reason is shown, never hidden.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import type { MechanismSearchResponse } from "@/lib/types";
import { apiMechanismSearch } from "@/lib/api";
import { themeOf } from "@/lib/type-theme";
import { EvidenceLevelChip, TypeBadge, TypeGlyph } from "@/components/badges";
import ThinkingDots from "@/components/smoothui/thinking-dots";

type Direction = "dampen" | "boost";

export default function MechanismPage() {
  const [direction, setDirection] = useState<Direction>("dampen");

  return (
    <div className="space-y-8">
      <header className="rounded-3xl bg-pine px-6 py-10 text-white sm:px-10 sm:py-12">
        <div className="max-w-3xl">
          <span className="mb-6 flex size-14 items-center justify-center rounded-2xl border border-sage/40 bg-sage/10 text-sage">
            <TypeGlyph type="Mechanism" size={31} />
          </span>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sage">Direction first · disease second</p>
          <h1 className="mt-3 text-3xl font-semibold tracking-[-0.04em] sm:text-5xl">Find a mechanism match</h1>
          <p className="mt-4 max-w-2xl text-base leading-7 text-dust">
            Choose an intervention direction to find diseases with matching biology. Candidate leads
            and excluded conditions are separated, with evidence level and exclusion reasons visible.
          </p>
        </div>
      </header>

      <div className="max-w-3xl">
      <p className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-muted-foreground">What direction would the intervention take?</p>
      <div
        className="grid grid-cols-2 gap-2 rounded-2xl border border-dust bg-white p-2"
        role="radiogroup"
        aria-label="Mechanism direction"
      >
        {(["dampen", "boost"] as Direction[]).map((d) => {
          const selected = direction === d;
          const selectedCls =
            d === "dampen"
              ? "bg-ochre/10 text-pine ring-1 ring-ochre/40"
              : "bg-fern/10 text-pine ring-1 ring-fern/40";
          return (
            <button
              key={d}
              role="radio"
              aria-checked={selected}
              onClick={() => setDirection(d)}
              className={`rounded-xl px-4 py-4 text-base font-semibold transition-colors sm:py-5 ${
                selected ? selectedCls : "text-muted-foreground hover:text-foreground"
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
      {error && <p className="text-sm text-rust">{error}</p>}
      {!data && !error && (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <ThinkingDots label="Searching mechanisms" />
          Searching mechanisms…
        </p>
      )}

      {data && (
        <>
          <section className="rounded-2xl border border-dust bg-white p-5 sm:p-6">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-hunter">Potential leads</p>
            <h2 className="mb-4 mt-1 text-2xl font-semibold tracking-tight text-pine">
              Matching diseases <span className="text-hunter">({data.results.length})</span>
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
                    className={`rounded-xl border border-dust border-l-4 bg-white p-5 transition-transform hover:-translate-y-0.5 motion-reduce:transform-none ${theme.accent}`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-gray-400">#{i + 1}</span>
                      <TypeGlyph type={r.node.type} size={16} className={theme.text} />
                      <Link
                        href={`/entity/${encodeURIComponent(r.node.id)}`}
                        className="text-sm font-medium text-foreground underline decoration-dust underline-offset-2 hover:decoration-fern"
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
                        className="underline decoration-dust underline-offset-2 hover:text-hunter"
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
                          className="rounded-full border border-dust bg-dust/15 px-2 py-0.5 text-[11px] text-foreground hover:border-fern/50 hover:bg-fern/10"
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

          <section className="rounded-2xl border border-rust/25 bg-rust/5 p-5 sm:p-6">
            <p className="text-xs font-semibold uppercase tracking-[0.15em] text-rust">Direction mismatch</p>
            <h2 className="mb-2 mt-1 text-2xl font-semibold tracking-tight text-pine">
              Excluded <span className="text-rust">({data.excluded.length})</span>
            </h2>
            <p className="mb-4 text-sm leading-6 text-muted-foreground">These conditions are shown so a mismatch cannot be mistaken for a lead.</p>
            {data.excluded.length === 0 && (
              <p className="text-sm text-gray-500">Nothing was excluded for this direction.</p>
            )}
            {/* Demoted on purpose: muted card + rust-tinted framing,
                so excluded items never read as leads. */}
            <ul className="space-y-2">
              {data.excluded.map((e) => (
                <li
                  key={e.node.id}
                  className="flex flex-col gap-2 rounded-xl border border-rust/20 bg-white/80 p-4"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="rounded-sm bg-rust/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rust/90">
                      excluded
                    </span>
                    <Link
                      href={`/entity/${encodeURIComponent(e.node.id)}?excluded=${direction}`}
                      className="text-sm text-muted-foreground underline decoration-dust underline-offset-2 hover:text-foreground"
                    >
                      {e.node.label}
                    </Link>
                  </span>
                  <span className="text-xs leading-5 text-rust">{e.reason}</span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}
