"use client";

/**
 * Renders a text line whose bracketed [e_… / c_…] citations become clickable
 * chips that open the evidence drawer (edge ids) or claim detail (claim ids).
 */

import { Fragment, useEffect, useState } from "react";
import { useEvidence } from "./evidence-drawer";
import { apiGraphCached } from "@/lib/api";
import { humanizeVariantMentions } from "@/lib/display";
import { citationLabel } from "@/lib/graph";

const CITATION_RE = /\[([ec]_[A-Za-z0-9_-]+(?:\s*,\s*[ec]_[A-Za-z0-9_-]+)*)\]/g;

export function CitationChip({ id }: { id: string }) {
  const { openEvidence } = useEvidence();
  // Resolve the raw e_/c_ id to a human label ("treats → STAT1 gain of
  // function", "PMID 28139313") once the shared graph load settles.
  const [label, setLabel] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    apiGraphCached()
      .then((g) => {
        if (!cancelled) setLabel(citationLabel(g, id));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [id]);
  return (
    <button
      onClick={() => openEvidence(id)}
      className={`mx-0.5 inline-block rounded border border-gray-300 bg-gray-50 px-1.5 py-0 align-baseline text-[11px] text-gray-600 transition-colors hover:border-sky-300 hover:bg-sky-50 hover:text-sky-800 animate-in fade-in zoom-in-95 duration-300 motion-reduce:animate-none ${label ? "" : "font-mono"}`}
      title={`Open evidence (${id})`}
    >
      {label ?? id}
    </button>
  );
}

export function CitationText({ text: rawText }: { text: string }) {
  // Prose from the API may embed canonical HGVS strings; render them short.
  const text = humanizeVariantMentions(rawText);
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
