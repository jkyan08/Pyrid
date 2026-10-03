"use client";

/**
 * The ONE global search box: disease, gene, variant, free-text symptom,
 * patient group or mechanism. Debounced GET /search; each result shows the
 * label, a type badge and the resolution line.
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { SearchResult } from "@/lib/types";
import { apiSearch } from "@/lib/api";
import { TypeBadge } from "./badges";

export function SearchBox({ autoFocus = false }: { autoFocus?: boolean }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const router = useRouter();
  const seq = useRef(0);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults(null);
      setBusy(false);
      return;
    }
    setBusy(true);
    const mySeq = ++seq.current;
    const t = setTimeout(() => {
      apiSearch(query)
        .then((res) => {
          if (seq.current !== mySeq) return;
          setResults(res.results);
        })
        .catch(() => {
          if (seq.current !== mySeq) return;
          setResults([]);
        })
        .finally(() => {
          if (seq.current === mySeq) setBusy(false);
        });
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div>
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus={autoFocus}
        placeholder="Search a disease, gene, variant, symptom, patient group or mechanism…"
        className="w-full rounded-lg border border-gray-300 px-4 py-3 text-base shadow-sm outline-none placeholder:text-gray-400 focus:border-sky-400 focus:ring-2 focus:ring-sky-100"
        aria-label="Search the atlas"
      />
      {busy && <p className="mt-3 text-xs text-gray-400">Searching…</p>}
      {results !== null && !busy && (
        <ul className="mt-4 divide-y divide-gray-100 rounded-lg border border-gray-200">
          {results.length === 0 && (
            <li className="px-4 py-3 text-sm text-gray-500">
              No matches in the current graph. A missing entity is itself a data point — nothing
              is invented to fill the gap.
            </li>
          )}
          {results.map((r) => (
            <li key={r.id}>
              <button
                onClick={() => router.push(`/entity/${encodeURIComponent(r.id)}`)}
                className="flex w-full flex-col items-start gap-1 px-4 py-3 text-left hover:bg-gray-50"
              >
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-medium text-gray-900">{r.label}</span>
                  <TypeBadge type={r.type} />
                </span>
                <span className="text-xs text-gray-500">{r.resolution}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
