/** Small, low-ink badge primitives shared across the app. */

import { evidenceLevelChipClass, themeOf, TYPE_THEMES } from "@/lib/type-theme";

/**
 * Subtle CSS-only entrance for chips (tw-animate-css, installed with
 * SmoothUI): fade + slight zoom, disabled under prefers-reduced-motion.
 */
const CHIP_ANIM = "animate-in fade-in zoom-in-95 duration-300 motion-reduce:animate-none";

/**
 * Hand-drawn type glyph from the type-theme source of truth. Inherits
 * currentColor, so wrap it in the theme's `text` class (or any ink class).
 */
export function TypeGlyph({
  type,
  size = 14,
  className,
}: {
  type: string;
  size?: number;
  className?: string;
}) {
  const t = themeOf(type);
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className}
      dangerouslySetInnerHTML={{ __html: t.glyph }}
    />
  );
}

export function TypeBadge({ type }: { type: string }) {
  const t = themeOf(type);
  // Unrecognized types keep their raw name (styled as Unknown), so nothing
  // in the graph is silently relabeled.
  const label = type in TYPE_THEMES ? t.label : type;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium ${CHIP_ANIM} ${t.badge}`}
    >
      <TypeGlyph type={type} size={11} />
      {label}
    </span>
  );
}

const EVIDENCE_COLORS: Record<string, string> = {
  observed: "bg-fern/10 text-hunter border-fern/30",
  extracted: "bg-sage/20 text-pine border-sage/50",
  inferred: "bg-gray-100 text-gray-600 border-gray-300",
};

export function EvidenceClassBadge({ evidenceClass }: { evidenceClass: string }) {
  const cls = EVIDENCE_COLORS[evidenceClass] ?? "bg-gray-50 text-gray-700 border-gray-200";
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${CHIP_ANIM} ${cls}`}
    >
      {evidenceClass}
    </span>
  );
}

/**
 * Evidence-level chip, color-scaled by study rank (observed/trial strongest
 * → in_vitro weakest → inferred gray). Used on mechanism candidate cards.
 */
export function EvidenceLevelChip({ level }: { level: string }) {
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-semibold ${CHIP_ANIM} ${evidenceLevelChipClass(level)}`}
      title={`evidence level: ${level}`}
    >
      {level.replace(/_/g, " ")}
    </span>
  );
}

const STATUS_COLORS: Record<string, string> = {
  supported: "bg-fern/10 text-hunter border-fern/30",
  promising: "bg-fern/10 text-hunter border-fern/30",
  contested: "bg-ochre/10 text-ochre border-ochre/30",
  small_n: "bg-ochre/15 text-ochre border-ochre/40",
  inferred_unsupported: "bg-rust/10 text-rust border-rust/30",
};

export function StatusChip({ label }: { label: string }) {
  const cls = STATUS_COLORS[label] ?? "bg-gray-50 text-gray-700 border-gray-200";
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${CHIP_ANIM} ${cls}`}
    >
      {label.replace(/_/g, " ")}
    </span>
  );
}

const VERDICT_LABELS: Record<string, { text: string; cls: string }> = {
  reusable_now: { text: "reusable now", cls: "bg-fern/10 text-hunter border-fern/30" },
  adaptable_needs_expert_review: {
    text: "adaptable, needs expert review",
    cls: "bg-ochre/10 text-ochre border-ochre/30",
  },
  not_transferable: { text: "not transferable", cls: "bg-rust/10 text-rust border-rust/30" },
};

/** Tolerates absent/unknown verdicts (renders nothing or the raw value). */
export function VerdictChip({ verdict }: { verdict?: string }) {
  if (!verdict) return null;
  const v = VERDICT_LABELS[verdict] ?? {
    text: verdict.replace(/_/g, " "),
    cls: "bg-gray-50 text-gray-700 border-gray-200",
  };
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-medium ${CHIP_ANIM} ${v.cls}`}
    >
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
