/**
 * Human-readable display labels. The graph keeps canonical ids/labels (real
 * ClinVar HGVS strings, lowercase MONDO names) — the UI renders them readably
 * and keeps the canonical form available as secondary text/tooltips.
 */

const AA3_TO_1: Record<string, string> = {
  Ala: "A", Arg: "R", Asn: "N", Asp: "D", Cys: "C", Gln: "Q", Glu: "E",
  Gly: "G", His: "H", Ile: "I", Leu: "L", Lys: "K", Met: "M", Phe: "F",
  Pro: "P", Ser: "S", Thr: "T", Trp: "W", Tyr: "Y", Val: "V", Ter: "*",
};

function proteinShort(p: string): string {
  // p.Arg274Trp -> R274W ; p.Val339fs -> V339fs ; p.Glu235Ter -> E235*
  return p.replace(/[A-Z][a-z]{2}/g, (m) => AA3_TO_1[m] ?? m);
}

/**
 * "NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)" -> "STAT1 R274W";
 * no protein change -> "STAT1 c.820C>T"; unparseable -> unchanged.
 */
export function shortVariantLabel(label: string): string {
  const gene = label.match(/^[A-Z]{2}_[\d.]+\(([A-Za-z0-9-]+)\):/);
  if (!gene) return label;
  const protein = label.match(/\(p\.([A-Za-z0-9_=*>+-]+)\)/);
  if (protein) return `${gene[1]} ${proteinShort(protein[1])}`;
  const coding = label.match(/:([cgmn]\.[^ ]+)/);
  if (coding) return `${gene[1]} ${coding[1]}`;
  return label;
}

const HGVS_RE = /[A-Z]{2}_[\d.]+\([A-Za-z0-9-]+\):[^\s\],]+(?: \(p\.[^)]+\))?/g;

/** Replace inline HGVS mentions in prose with their short variant form. */
export function humanizeVariantMentions(text: string): string {
  return text.replace(HGVS_RE, (m) => shortVariantLabel(m));
}

/** Type-aware readable label; canonical label stays in the data. */
export function displayLabel(label: string, type?: string): string {
  if (type === "Variant") return shortVariantLabel(label);
  if (type === "Disease" && label.length > 0) {
    return label.charAt(0).toUpperCase() + label.slice(1);
  }
  return label;
}
