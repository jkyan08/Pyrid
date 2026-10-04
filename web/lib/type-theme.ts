/**
 * ONE source of truth for how every node type looks, everywhere a type
 * appears: badges, search rows, entity headers, Act-tab asset cards, the
 * evidence drawer and the map legend.
 *
 * Pure data (no JSX) so it is unit-testable in node. Each entry carries:
 *  - label: human-readable name
 *  - badge/text/accent/band: light-ink Tailwind classes (literal strings so
 *    Tailwind v4 can see them)
 *  - hex: a saturated color for graph outlines + legend marks
 *  - shape: the Canvas node shape (fill stays CLUSTER-colored; shape and
 *    border encode the TYPE, per README §10 "colour = cluster only")
 *  - glyph: hand-drawn inline-SVG inner markup for a 24×24 viewBox
 *    (stroke="currentColor" is applied by the <TypeGlyph> wrapper)
 *
 * Hues are distinct and colorblind-reasonable: disease=rose, gene=indigo,
 * variant=violet, mechanism=amber, drug=teal, phenotype=sky, pathway=slate,
 * group/registry=emerald, investigator=fuchsia, study=cyan, paper=stone.
 */

import { STUDY_RANK } from "./graph";

export interface TypeTheme {
  /** Human label shown in badges and legends. */
  label: string;
  /** Chip classes: background + ink + border. */
  badge: string;
  /** Ink-only class for glyphs/text in this type's hue. */
  text: string;
  /** Left-accent border class (use with border-l-4). */
  accent: string;
  /** Tinted header-band classes: background tint + border. */
  band: string;
  /** Saturated hex for graph node outlines and legend marks. */
  hex: string;
  /** Canvas node shape (type is encoded as shape + border, never fill). */
  shape: string;
  /** Inline SVG inner markup, 24×24 viewBox, stroked with currentColor. */
  glyph: string;
}

export const TYPE_THEMES: Record<string, TypeTheme> = {
  Disease: {
    label: "Disease",
    badge: "bg-rose-50 text-rose-800 border-rose-200",
    text: "text-rose-700",
    accent: "border-l-rose-400",
    band: "bg-rose-50/70 border-rose-200",
    hex: "#f43f5e",
    shape: "ellipse",
    glyph: '<circle cx="12" cy="12" r="8.2"/><path d="M12 8.3v7.4M8.3 12h7.4"/>',
  },
  Gene: {
    label: "Gene",
    badge: "bg-indigo-50 text-indigo-800 border-indigo-200",
    text: "text-indigo-700",
    accent: "border-l-indigo-400",
    band: "bg-indigo-50/70 border-indigo-200",
    hex: "#6366f1",
    shape: "round-rectangle",
    glyph:
      '<path d="M8.5 3.5c0 5.5 7 5.5 7 8.5s-7 3-7 8.5"/><path d="M15.5 3.5c0 5.5-7 5.5-7 8.5s7 3 7 8.5"/><path d="M9.3 6.5h5.4M9.3 17.5h5.4"/>',
  },
  Variant: {
    label: "Variant",
    badge: "bg-violet-50 text-violet-800 border-violet-200",
    text: "text-violet-700",
    accent: "border-l-violet-400",
    band: "bg-violet-50/70 border-violet-200",
    hex: "#8b5cf6",
    shape: "diamond",
    glyph:
      '<path d="M12 3.5 20.5 12 12 20.5 3.5 12Z"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  },
  Mechanism: {
    label: "Mechanism",
    badge: "bg-amber-50 text-amber-800 border-amber-200",
    text: "text-amber-700",
    accent: "border-l-amber-400",
    band: "bg-amber-50/70 border-amber-200",
    hex: "#f59e0b",
    shape: "hexagon",
    glyph: '<path d="M13.2 3 6 13.4h4.8L10.8 21 18 10.6h-4.8L13.2 3Z"/>',
  },
  Phenotype: {
    label: "Phenotype",
    badge: "bg-sky-50 text-sky-800 border-sky-200",
    text: "text-sky-700",
    accent: "border-l-sky-400",
    band: "bg-sky-50/70 border-sky-200",
    hex: "#0ea5e9",
    shape: "round-pentagon",
    glyph:
      '<path d="M3.5 12C5.5 7.9 8.6 5.9 12 5.9s6.5 2 8.5 6.1c-2 4.1-5.1 6.1-8.5 6.1s-6.5-2-8.5-6.1Z"/><circle cx="12" cy="12" r="2.4"/>',
  },
  Pathway: {
    label: "Pathway",
    badge: "bg-slate-100 text-slate-700 border-slate-300",
    text: "text-slate-600",
    accent: "border-l-slate-400",
    band: "bg-slate-50 border-slate-200",
    hex: "#64748b",
    shape: "rhomboid",
    glyph: '<path d="M4 18.5h3.5c5 0 3.5-10 8.5-10h3"/><path d="m16 5.5 3 3-3 3"/>',
  },
  Drug: {
    label: "Drug",
    badge: "bg-teal-50 text-teal-800 border-teal-200",
    text: "text-teal-700",
    accent: "border-l-teal-400",
    band: "bg-teal-50/70 border-teal-200",
    hex: "#14b8a6",
    shape: "round-triangle",
    glyph:
      '<g transform="rotate(-35 12 12)"><rect x="3.5" y="9" width="17" height="6.2" rx="3.1"/><path d="M12 9v6.2"/></g>',
  },
  Paper: {
    label: "Paper",
    badge: "bg-stone-100 text-stone-700 border-stone-300",
    text: "text-stone-600",
    accent: "border-l-stone-400",
    band: "bg-stone-50 border-stone-200",
    hex: "#78716c",
    shape: "rectangle",
    glyph:
      '<path d="M7 3.5h7l4 4V20.5H7Z"/><path d="M14 3.5V8h4"/><path d="M9.6 12.2h4.8M9.6 15.6h4.8"/>',
  },
  Claim: {
    label: "Claim",
    badge: "bg-zinc-100 text-zinc-700 border-zinc-300",
    text: "text-zinc-600",
    accent: "border-l-zinc-400",
    band: "bg-zinc-50 border-zinc-200",
    hex: "#a1a1aa",
    shape: "cut-rectangle",
    glyph:
      '<path d="M4 7A2.5 2.5 0 0 1 6.5 4.5h11A2.5 2.5 0 0 1 20 7v6.5a2.5 2.5 0 0 1-2.5 2.5H12l-4.2 3.8V16H6.5A2.5 2.5 0 0 1 4 13.5Z"/>',
  },
  Investigator: {
    label: "Investigator",
    badge: "bg-fuchsia-50 text-fuchsia-800 border-fuchsia-200",
    text: "text-fuchsia-700",
    accent: "border-l-fuchsia-400",
    band: "bg-fuchsia-50/70 border-fuchsia-200",
    hex: "#d946ef",
    shape: "star",
    glyph:
      '<circle cx="12" cy="8" r="3.3"/><path d="M5.5 20c.8-3.9 3.4-5.9 6.5-5.9s5.7 2 6.5 5.9"/>',
  },
  PatientGroup: {
    label: "Patient group",
    badge: "bg-emerald-50 text-emerald-800 border-emerald-200",
    text: "text-emerald-700",
    accent: "border-l-emerald-400",
    band: "bg-emerald-50/70 border-emerald-200",
    hex: "#10b981",
    shape: "barrel",
    glyph:
      '<circle cx="9" cy="9" r="2.7"/><circle cx="16.3" cy="10.3" r="2.1"/><path d="M4.3 19.5c.6-3.3 2.4-5.1 4.7-5.1s4.1 1.8 4.7 5.1"/><path d="M15.2 18.6c.4-2.3 1.6-3.6 3.3-3.6 1.3 0 2.3.7 3 2.2"/>',
  },
  Registry: {
    label: "Registry",
    badge: "bg-emerald-50 text-emerald-900 border-emerald-300",
    text: "text-emerald-800",
    accent: "border-l-emerald-600",
    band: "bg-emerald-50/50 border-emerald-300",
    hex: "#047857",
    shape: "barrel",
    glyph:
      '<ellipse cx="12" cy="5.8" rx="7" ry="2.6"/><path d="M5 5.8v12.4c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V5.8"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>',
  },
  Study: {
    label: "Study",
    badge: "bg-cyan-50 text-cyan-800 border-cyan-200",
    text: "text-cyan-700",
    accent: "border-l-cyan-400",
    band: "bg-cyan-50/70 border-cyan-200",
    hex: "#06b6d4",
    shape: "tag",
    glyph:
      '<path d="M9.8 3.5h4.4M10.6 3.5v5.1l-4.9 8.6a2.4 2.4 0 0 0 2.1 3.6h8.4a2.4 2.4 0 0 0 2.1-3.6l-4.9-8.6V3.5"/><path d="M8.3 14.5h7.4"/>',
  },
  Unknown: {
    label: "Unknown",
    badge: "bg-gray-50 text-gray-700 border-gray-200",
    text: "text-gray-500",
    accent: "border-l-gray-300",
    band: "bg-gray-50 border-gray-200",
    hex: "#9ca3af",
    shape: "ellipse",
    glyph:
      '<circle cx="12" cy="12" r="8.4"/><path d="M9.7 9.6a2.3 2.3 0 1 1 3.6 1.9c-.9.6-1.3 1.1-1.3 2v.3"/><circle cx="12" cy="16.7" r="0.9" fill="currentColor" stroke="none"/>',
  },
};

/** Theme lookup with the Unknown fallback for any unrecognized type. */
export function themeOf(type: string | undefined | null): TypeTheme {
  return (type && TYPE_THEMES[type]) || TYPE_THEMES.Unknown;
}

/* --------------------- evidence-level chip scale --------------------- */

/**
 * Evidence-level chips are color-scaled by STUDY_RANK: observed/trial
 * strongest (deep emerald) → in_vitro/review weakest (muted stone) →
 * inferred (gray). Unknown levels fall back to the gray end.
 */
export function evidenceLevelChipClass(level: string): string {
  const rank = STUDY_RANK[level] ?? 0;
  if (rank >= 5) return "bg-emerald-100 text-emerald-900 border-emerald-300";
  if (rank === 4) return "bg-emerald-50 text-emerald-800 border-emerald-200";
  if (rank === 3) return "bg-lime-50 text-lime-800 border-lime-200";
  if (rank === 2) return "bg-amber-50 text-amber-800 border-amber-200";
  if (rank === 1) return "bg-stone-100 text-stone-600 border-stone-300";
  return "bg-gray-100 text-gray-500 border-gray-300";
}
