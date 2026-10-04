/**
 * ONE source of truth for how every node type looks, everywhere a type
 * appears: badges, search rows, entity headers, Act-tab asset cards, the
 * evidence drawer and the map legend.
 *
 * Pure data (no JSX) so it is unit-testable in node. Each entry carries:
 *  - label: human-readable name
 *  - badge/text/accent/band: light-ink Tailwind classes (literal strings so
 *    Tailwind v4 can see them)
 *  - hex: a mid-tone color for graph outlines + legend marks (readable on
 *    the dark pine map canvas)
 *  - shape: the Canvas node shape (fill stays CLUSTER-colored; shape and
 *    border encode the TYPE, per README §10 "colour = cluster only")
 *  - glyph: hand-drawn inline-SVG inner markup for a 24×24 viewBox
 *    (stroke="currentColor" is applied by the <TypeGlyph> wrapper)
 *
 * Hues are desaturated earth tones harmonized with the atlas palette
 * (Dust/Sage/Fern/Hunter/Pine) while staying distinct and
 * colorblind-reasonable: disease=clay, gene=moss, variant=sage-violet,
 * mechanism=ochre, drug=deep teal, phenotype=stone blue, pathway=slate,
 * patient group=dry sage, registry=hunter, investigator=plum,
 * study=teal-cyan, paper=stone, claim=zinc.
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
  /** Mid-tone hex for graph node outlines and legend marks. */
  hex: string;
  /** Canvas node shape (type is encoded as shape + border, never fill). */
  shape: string;
  /** Inline SVG inner markup, 24×24 viewBox, stroked with currentColor. */
  glyph: string;
}

export const TYPE_THEMES: Record<string, TypeTheme> = {
  Disease: {
    label: "Disease",
    badge: "bg-[#f6ece7] text-[#8f4f3b] border-[#e3c9bd]",
    text: "text-[#8f4f3b]",
    accent: "border-l-[#c98a70]",
    band: "bg-[#f6ece7]/70 border-[#e3c9bd]",
    hex: "#c98a70",
    shape: "ellipse",
    glyph: '<circle cx="12" cy="12" r="8.2"/><path d="M12 8.3v7.4M8.3 12h7.4"/>',
  },
  Gene: {
    label: "Gene",
    badge: "bg-[#eff3e2] text-[#55702c] border-[#d4dfb6]",
    text: "text-[#55702c]",
    accent: "border-l-[#a9bd77]",
    band: "bg-[#eff3e2]/70 border-[#d4dfb6]",
    hex: "#a9c06c",
    shape: "round-rectangle",
    glyph:
      '<path d="M8.5 3.5c0 5.5 7 5.5 7 8.5s-7 3-7 8.5"/><path d="M15.5 3.5c0 5.5-7 5.5-7 8.5s7 3 7 8.5"/><path d="M9.3 6.5h5.4M9.3 17.5h5.4"/>',
  },
  Variant: {
    label: "Variant",
    badge: "bg-[#efedf5] text-[#625687] border-[#d5d0e4]",
    text: "text-[#625687]",
    accent: "border-l-[#a89dc4]",
    band: "bg-[#efedf5]/70 border-[#d5d0e4]",
    hex: "#9c90bd",
    shape: "diamond",
    glyph:
      '<path d="M12 3.5 20.5 12 12 20.5 3.5 12Z"/><circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none"/>',
  },
  Mechanism: {
    label: "Mechanism",
    badge: "bg-[#f7f0dd] text-[#83652a] border-[#e5d6ab]",
    text: "text-[#83652a]",
    accent: "border-l-[#d2b269]",
    band: "bg-[#f7f0dd]/70 border-[#e5d6ab]",
    hex: "#cfab5e",
    shape: "hexagon",
    glyph: '<path d="M13.2 3 6 13.4h4.8L10.8 21 18 10.6h-4.8L13.2 3Z"/>',
  },
  Phenotype: {
    label: "Phenotype",
    badge: "bg-[#eaf0f6] text-[#4a6584] border-[#ccd9e6]",
    text: "text-[#4a6584]",
    accent: "border-l-[#8fa9c4]",
    band: "bg-[#eaf0f6]/70 border-[#ccd9e6]",
    hex: "#87a3c4",
    shape: "round-pentagon",
    glyph:
      '<path d="M3.5 12C5.5 7.9 8.6 5.9 12 5.9s6.5 2 8.5 6.1c-2 4.1-5.1 6.1-8.5 6.1s-6.5-2-8.5-6.1Z"/><circle cx="12" cy="12" r="2.4"/>',
  },
  Pathway: {
    label: "Pathway",
    badge: "bg-[#eef0f2] text-[#5a6472] border-[#d3d7dd]",
    text: "text-[#5a6472]",
    accent: "border-l-[#98a2b0]",
    band: "bg-[#eef0f2] border-[#d3d7dd]",
    hex: "#8b96a5",
    shape: "rhomboid",
    glyph: '<path d="M4 18.5h3.5c5 0 3.5-10 8.5-10h3"/><path d="m16 5.5 3 3-3 3"/>',
  },
  Drug: {
    label: "Drug",
    badge: "bg-[#e7f2ee] text-[#2e6e60] border-[#c0dcd2]",
    text: "text-[#2e6e60]",
    accent: "border-l-[#6aa996]",
    band: "bg-[#e7f2ee]/70 border-[#c0dcd2]",
    hex: "#5aa08c",
    shape: "round-triangle",
    glyph:
      '<g transform="rotate(-35 12 12)"><rect x="3.5" y="9" width="17" height="6.2" rx="3.1"/><path d="M12 9v6.2"/></g>',
  },
  Paper: {
    label: "Paper",
    badge: "bg-[#f4f1ea] text-[#6f6752] border-[#ded7c6]",
    text: "text-[#6f6752]",
    accent: "border-l-[#b0a68e]",
    band: "bg-[#f4f1ea] border-[#ded7c6]",
    hex: "#a79c84",
    shape: "rectangle",
    glyph:
      '<path d="M7 3.5h7l4 4V20.5H7Z"/><path d="M14 3.5V8h4"/><path d="M9.6 12.2h4.8M9.6 15.6h4.8"/>',
  },
  Claim: {
    label: "Claim",
    badge: "bg-[#f1f1f3] text-[#63636b] border-[#d7d7dc]",
    text: "text-[#63636b]",
    accent: "border-l-[#9d9da6]",
    band: "bg-[#f1f1f3] border-[#d7d7dc]",
    hex: "#94949e",
    shape: "cut-rectangle",
    glyph:
      '<path d="M4 7A2.5 2.5 0 0 1 6.5 4.5h11A2.5 2.5 0 0 1 20 7v6.5a2.5 2.5 0 0 1-2.5 2.5H12l-4.2 3.8V16H6.5A2.5 2.5 0 0 1 4 13.5Z"/>',
  },
  Investigator: {
    label: "Investigator",
    badge: "bg-[#f5edf2] text-[#7e5169] border-[#e1cdd9]",
    text: "text-[#7e5169]",
    accent: "border-l-[#b88aa3]",
    band: "bg-[#f5edf2]/70 border-[#e1cdd9]",
    hex: "#b07f9a",
    shape: "star",
    glyph:
      '<circle cx="12" cy="8" r="3.3"/><path d="M5.5 20c.8-3.9 3.4-5.9 6.5-5.9s5.7 2 6.5 5.9"/>',
  },
  PatientGroup: {
    label: "Patient group",
    badge: "bg-[#f1f3e9] text-[#59683c] border-[#d8dec5]",
    text: "text-[#59683c]",
    accent: "border-l-[#a3b18a]",
    band: "bg-[#f1f3e9]/70 border-[#d8dec5]",
    hex: "#a3b18a",
    shape: "barrel",
    glyph:
      '<circle cx="9" cy="9" r="2.7"/><circle cx="16.3" cy="10.3" r="2.1"/><path d="M4.3 19.5c.6-3.3 2.4-5.1 4.7-5.1s4.1 1.8 4.7 5.1"/><path d="M15.2 18.6c.4-2.3 1.6-3.6 3.3-3.6 1.3 0 2.3.7 3 2.2"/>',
  },
  Registry: {
    label: "Registry",
    badge: "bg-[#eaf0e9] text-[#3a5a40] border-[#c9d6c8]",
    text: "text-[#3a5a40]",
    accent: "border-l-[#588157]",
    band: "bg-[#eaf0e9]/60 border-[#c9d6c8]",
    hex: "#6a9674",
    shape: "barrel",
    glyph:
      '<ellipse cx="12" cy="5.8" rx="7" ry="2.6"/><path d="M5 5.8v12.4c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V5.8"/><path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6"/>',
  },
  Study: {
    label: "Study",
    badge: "bg-[#e8f2f3] text-[#38646e] border-[#c2dce0]",
    text: "text-[#38646e]",
    accent: "border-l-[#77aeb7]",
    band: "bg-[#e8f2f3]/70 border-[#c2dce0]",
    hex: "#58989f",
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
 * strongest (fern green) → in_vitro/review weakest (muted stone) →
 * inferred (gray). Unknown levels fall back to the gray end.
 */
export function evidenceLevelChipClass(level: string): string {
  const rank = STUDY_RANK[level] ?? 0;
  if (rank >= 5) return "bg-fern/15 text-hunter border-fern/40";
  if (rank === 4) return "bg-fern/10 text-hunter border-fern/30";
  if (rank === 3) return "bg-sage/25 text-pine border-sage/60";
  if (rank === 2) return "bg-ochre/10 text-ochre border-ochre/30";
  if (rank === 1) return "bg-stone-100 text-stone-600 border-stone-300";
  return "bg-gray-100 text-gray-500 border-gray-300";
}
