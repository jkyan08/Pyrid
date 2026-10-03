"use client";

/**
 * The ONE global search box: disease, gene, variant, free-text symptom,
 * patient group or mechanism. Debounced GET /search; each result shows the
 * label, a type badge and the resolution line.
 *
 * Styled with SmoothUI: AnimatedInput (floating label) for the box, a
 * MotionLoader while searching, and a staggered motion entrance for result
 * rows (disabled under prefers-reduced-motion). Result rows carry the
 * type-theme left accent + glyph so a Drug hit reads differently from a
 * Disease hit at a glance. `hero` renders the larger home-page variant.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, useReducedMotion } from "motion/react";
import type { SearchResult } from "@/lib/types";
import { apiSearch } from "@/lib/api";
import { themeOf } from "@/lib/type-theme";
import AnimatedInput from "@/components/smoothui/animated-input";
import MotionLoader from "@/components/smoothui/motion-loader";
import { TypeBadge, TypeGlyph } from "./badges";

export function SearchBox({
  autoFocus = false,
  hero = false,
}: {
  autoFocus?: boolean;
  hero?: boolean;
}) {
  const [q, setQ] = useState("");
  // Results are keyed by the query they answer; "busy" and the visible list
  // are derived during render instead of set inside the effect.
  const [resultsFor, setResultsFor] = useState<{
    query: string;
    results: SearchResult[];
  } | null>(null);
  const router = useRouter();
  const seq = useRef(0);
  const shouldReduceMotion = useReducedMotion();

  const query = q.trim();
  const active = query.length >= 2;

  useEffect(() => {
    if (!active) return;
    const mySeq = ++seq.current;
    const t = setTimeout(() => {
      apiSearch(query)
        .then((res) => {
          if (seq.current === mySeq) setResultsFor({ query, results: res.results });
        })
        .catch(() => {
          if (seq.current === mySeq) setResultsFor({ query, results: [] });
        });
    }, 300);
    return () => clearTimeout(t);
  }, [query, active]);

  const results = active && resultsFor?.query === query ? resultsFor.results : null;
  const busy = active && resultsFor?.query !== query;

  return (
    <div>
      <AnimatedInput
        value={q}
        onChange={setQ}
        autoFocus={autoFocus}
        label="Search the atlas"
        placeholder="disease, gene, variant, symptom, patient group or mechanism…"
        inputClassName={
          hero
            ? "rounded-xl px-5 py-4 text-lg shadow-md shadow-sky-100/60 placeholder:text-gray-400"
            : "rounded-lg px-4 py-3 text-base shadow-sm placeholder:text-gray-400"
        }
      />
      {busy && (
        <p className="mt-3 flex items-center gap-2 text-xs text-gray-400">
          <MotionLoader variant="dot-ring" size={16} label="Searching" />
          Searching…
        </p>
      )}
      {results !== null && !busy && (
        <ul
          className={`mt-4 divide-y divide-gray-100 overflow-hidden border border-gray-200 ${
            hero ? "rounded-xl shadow-sm" : "rounded-lg"
          }`}
        >
          {results.length === 0 && (
            <li className={`px-5 text-sm text-gray-500 ${hero ? "py-4" : "py-3"}`}>
              No matches in the current graph. A missing entity is itself a data point — nothing
              is invented to fill the gap.
            </li>
          )}
          {results.map((r, i) => {
            const theme = themeOf(r.type);
            return (
              <motion.li
                key={r.id}
                initial={
                  shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 6 }
                }
                animate={{ opacity: 1, y: 0 }}
                transition={
                  shouldReduceMotion
                    ? { duration: 0 }
                    : {
                        type: "spring",
                        bounce: 0.1,
                        duration: 0.3,
                        delay: Math.min(i * 0.04, 0.3),
                      }
                }
              >
                <button
                  onClick={() => router.push(`/entity/${encodeURIComponent(r.id)}`)}
                  className={`flex w-full items-start gap-3 border-l-4 px-4 text-left hover:bg-gray-50 ${theme.accent} ${
                    hero ? "py-4" : "py-3"
                  }`}
                >
                  <TypeGlyph
                    type={r.type}
                    size={hero ? 20 : 16}
                    className={`mt-0.5 shrink-0 ${theme.text}`}
                  />
                  <span className="flex min-w-0 flex-col items-start gap-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span
                        className={`font-medium text-gray-900 ${hero ? "text-base" : "text-sm"}`}
                      >
                        {r.label}
                      </span>
                      <TypeBadge type={r.type} />
                    </span>
                    <span className={`text-gray-500 ${hero ? "text-sm" : "text-xs"}`}>
                      {r.resolution}
                    </span>
                  </span>
                </button>
              </motion.li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
