/** Small, low-ink badge primitives shared across the app. */

const TYPE_COLORS: Record<string, string> = {
  Gene: "bg-sky-50 text-sky-800 border-sky-200",
  Variant: "bg-indigo-50 text-indigo-800 border-indigo-200",
  Mechanism: "bg-violet-50 text-violet-800 border-violet-200",
  Disease: "bg-rose-50 text-rose-800 border-rose-200",
  Phenotype: "bg-amber-50 text-amber-800 border-amber-200",
  Pathway: "bg-teal-50 text-teal-800 border-teal-200",
  Drug: "bg-emerald-50 text-emerald-800 border-emerald-200",
  Paper: "bg-stone-50 text-stone-700 border-stone-200",
  Claim: "bg-stone-50 text-stone-700 border-stone-200",
  Investigator: "bg-cyan-50 text-cyan-800 border-cyan-200",
  PatientGroup: "bg-fuchsia-50 text-fuchsia-800 border-fuchsia-200",
  Registry: "bg-lime-50 text-lime-800 border-lime-200",
  Study: "bg-orange-50 text-orange-800 border-orange-200",
};

export function TypeBadge({ type }: { type: string }) {
  const cls = TYPE_COLORS[type] ?? "bg-gray-50 text-gray-700 border-gray-200";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {type}
    </span>
  );
}

const EVIDENCE_COLORS: Record<string, string> = {
  observed: "bg-emerald-50 text-emerald-800 border-emerald-200",
  extracted: "bg-sky-50 text-sky-800 border-sky-200",
  inferred: "bg-gray-100 text-gray-600 border-gray-300",
};

export function EvidenceClassBadge({ evidenceClass }: { evidenceClass: string }) {
  const cls = EVIDENCE_COLORS[evidenceClass] ?? "bg-gray-50 text-gray-700 border-gray-200";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {evidenceClass}
    </span>
  );
}

const STATUS_COLORS: Record<string, string> = {
  supported: "bg-emerald-50 text-emerald-800 border-emerald-200",
  promising: "bg-emerald-50 text-emerald-800 border-emerald-200",
  contested: "bg-amber-50 text-amber-800 border-amber-200",
  small_n: "bg-orange-50 text-orange-800 border-orange-200",
  inferred_unsupported: "bg-red-50 text-red-700 border-red-200",
};

export function StatusChip({ label }: { label: string }) {
  const cls = STATUS_COLORS[label] ?? "bg-gray-50 text-gray-700 border-gray-200";
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${cls}`}>
      {label.replace(/_/g, " ")}
    </span>
  );
}

const VERDICT_LABELS: Record<string, { text: string; cls: string }> = {
  reusable_now: { text: "reusable now", cls: "bg-emerald-50 text-emerald-800 border-emerald-200" },
  adaptable_needs_expert_review: {
    text: "adaptable, needs expert review",
    cls: "bg-amber-50 text-amber-800 border-amber-200",
  },
  not_transferable: { text: "not transferable", cls: "bg-red-50 text-red-700 border-red-200" },
};

/** Tolerates absent/unknown verdicts (renders nothing or the raw value). */
export function VerdictChip({ verdict }: { verdict?: string }) {
  if (!verdict) return null;
  const v = VERDICT_LABELS[verdict] ?? {
    text: verdict.replace(/_/g, " "),
    cls: "bg-gray-50 text-gray-700 border-gray-200",
  };
  return (
    <span className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${v.cls}`}>
      {v.text}
    </span>
  );
}

/** "cached only" label shown when an LLM response's source is not "live". */
export function SourceLabel({ source }: { source: string }) {
  if (source === "live") return null;
  return (
    <span
      className="inline-block rounded-full border border-gray-300 bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600"
      title={`source: ${source}`}
    >
      cached only · {source}
    </span>
  );
}
