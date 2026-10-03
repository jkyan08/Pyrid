"use client";

/**
 * Renders a text line whose bracketed [e_… / c_…] citations become clickable
 * chips that open the evidence drawer (edge ids) or claim detail (claim ids).
 */

import { Fragment } from "react";
import { useEvidence } from "./evidence-drawer";

const CITATION_RE = /\[([ec]_[A-Za-z0-9_-]+(?:\s*,\s*[ec]_[A-Za-z0-9_-]+)*)\]/g;

export function CitationChip({ id }: { id: string }) {
  const { openEvidence } = useEvidence();
  return (
    <button
      onClick={() => openEvidence(id)}
      className="mx-0.5 inline-block rounded border border-gray-300 bg-gray-50 px-1.5 py-0 align-baseline font-mono text-[11px] text-gray-600 hover:border-sky-300 hover:bg-sky-50 hover:text-sky-800"
      title={`Open evidence for ${id}`}
    >
      {id}
    </button>
  );
}

export function CitationText({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  for (const m of text.matchAll(CITATION_RE)) {
    const idx = m.index ?? 0;
    if (idx > last) parts.push(<Fragment key={key++}>{text.slice(last, idx)}</Fragment>);
    for (const raw of m[1].split(",")) {
      const id = raw.trim();
      parts.push(<CitationChip key={key++} id={id} />);
    }
    last = idx + m[0].length;
  }
  if (last < text.length) parts.push(<Fragment key={key++}>{text.slice(last)}</Fragment>);
  return <span>{parts}</span>;
}
