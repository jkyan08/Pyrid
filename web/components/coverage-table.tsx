"use client";

import type { CoverageEntry } from "@/lib/types";

/** Search-coverage table: source / query / date / hits, zero-hit rows included. */
export function CoverageTable({ coverage }: { coverage: CoverageEntry | null }) {
  if (!coverage || coverage.searched.length === 0) {
    return (
      <p className="text-sm text-gray-500">
        No search coverage was recorded for this entity.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[480px] text-left text-sm">
        <thead>
          <tr className="border-b border-gray-200 text-xs uppercase tracking-wide text-gray-400">
            <th className="py-2 pr-4 font-medium">Source</th>
            <th className="py-2 pr-4 font-medium">Query</th>
            <th className="py-2 pr-4 font-medium">Date</th>
            <th className="py-2 font-medium">Hits</th>
          </tr>
        </thead>
        <tbody>
          {coverage.searched.map((s, i) => (
            <tr key={i} className="border-b border-gray-100">
              <td className="py-2 pr-4 text-gray-700">{s.source}</td>
              <td className="py-2 pr-4 text-gray-700">“{s.query}”</td>
              <td className="py-2 pr-4 text-gray-500">{s.date ?? "—"}</td>
              <td className={`py-2 ${s.hits === 0 ? "font-medium text-rust" : "text-gray-700"}`}>
                {s.hits}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
