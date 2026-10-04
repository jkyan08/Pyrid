"use client";

/**
 * Entity page: progressive reveal with tabs (Summary / Map / Act / Gaps).
 * When the entity has no supported route, the honest-gap state is shown
 * prominently above the tabs: "no supported route", the search coverage,
 * and the next question.
 */

import { useEffect, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import type { EntityView, ExplainResponse, Graph } from "@/lib/types";
import { apiEntity, apiExplain, apiGraph, apiMechanismSearch, NotFoundError } from "@/lib/api";
import { SourceLabel, TypeBadge, TypeGlyph } from "@/components/badges";
import { displayLabel } from "@/lib/display";
import { themeOf } from "@/lib/type-theme";
import { CitationChip, CitationText } from "@/components/citation-text";
import { CoverageTable } from "@/components/coverage-table";
import { ActTab } from "@/components/act-tab";
import { GapsTab } from "@/components/gaps-tab";
import { GraphMap } from "@/components/graph-map";
import { DiscoveryTrail } from "@/components/discovery-trail";
import { SearchBox } from "@/components/search-box";
import AnimatedTabs from "@/components/smoothui/animated-tabs";
import ThinkingDots from "@/components/smoothui/thinking-dots";
import SmoothButton from "@/components/smoothui/smooth-button";

type Tab = "summary" | "map" | "act";

const ENTITY_PURPOSE: Record<string, { eyebrow: string; description: string }> = {
  Disease: { eyebrow: "Disease dossier", description: "Trace the mechanism, related conditions and research partners around this disease." },
  Gene: { eyebrow: "Gene explorer", description: "See how this gene connects to pathways, variants and disease mechanisms." },
  Variant: { eyebrow: "Variant evidence", description: "Follow this specific variant to its mechanism and clinical evidence." },
  Mechanism: { eyebrow: "Mechanism explorer", description: "Compare the diseases and interventions connected by the same biological direction." },
  Pathway: { eyebrow: "Pathway map", description: "Explore the genes and disease mechanisms that meet at this pathway." },
  Phenotype: { eyebrow: "Clinical feature", description: "Find conditions linked by this observed feature and inspect the supporting evidence." },
  PatientGroup: { eyebrow: "Patient community", description: "See the conditions and research assets connected to this community." },
  Registry: { eyebrow: "Research asset · registry", description: "Check linked conditions, reuse potential and eligibility questions." },
  Study: { eyebrow: "Research asset · study", description: "Review study links and what would need to change for another condition." },
  Drug: { eyebrow: "Intervention evidence", description: "Check target direction, disease links and the evidence behind each connection." },
  Investigator: { eyebrow: "Research collaborator", description: "Trace the studies, papers and conditions linked to this investigator." },
  Paper: { eyebrow: "Source publication", description: "Inspect the claims and connections grounded in this publication." },
  Claim: { eyebrow: "Evidence claim", description: "Follow the exact assertion to its source and connected entities." },
};

const ENTITY_FACETS: Record<string, { label: string; types: string[] }[]> = {
  Disease: [
    { label: "mechanisms", types: ["Mechanism"] },
    { label: "genes & pathways", types: ["Gene", "Pathway"] },
    { label: "communities & assets", types: ["PatientGroup", "Registry", "Study"] },
  ],
  Gene: [
    { label: "variants", types: ["Variant"] },
    { label: "pathways", types: ["Pathway"] },
    { label: "diseases", types: ["Disease"] },
  ],
  Mechanism: [
    { label: "diseases", types: ["Disease"] },
    { label: "genes & variants", types: ["Gene", "Variant"] },
    { label: "interventions", types: ["Drug"] },
  ],
  Registry: [
    { label: "covered diseases", types: ["Disease"] },
    { label: "communities", types: ["PatientGroup"] },
    { label: "studies", types: ["Study"] },
  ],
  Study: [
    { label: "studied diseases", types: ["Disease"] },
    { label: "interventions", types: ["Drug"] },
    { label: "investigators", types: ["Investigator"] },
  ],
  PatientGroup: [
    { label: "diseases", types: ["Disease"] },
    { label: "registries", types: ["Registry"] },
    { label: "studies", types: ["Study"] },
  ],
};

export default function EntityPage() {
  const params = useParams<{ id: string }>();
  const id = decodeURIComponent(params.id);
  // key={id} remounts the view per entity, so entity/error/tab reset without
  // synchronous setState inside the fetch effect.
  return <EntityPageView key={id} id={id} />;
}

function EntityPageView({ id }: { id: string }) {
  const [entity, setEntity] = useState<EntityView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("map");

  useEffect(() => {
    let cancelled = false;
    apiEntity(id)
      .then((v) => {
        if (!cancelled) setEntity(v);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof NotFoundError
            ? `No entity with id "${id}" exists in the current graph.`
            : String(e)
        );
      });
    return () => {
      cancelled = true;
    };
  }, [id]);

  if (error) {
    return <p className="py-10 text-center text-sm text-rust">{error}</p>;
  }
  if (!entity) {
    return (
      <p className="flex items-center justify-center gap-2 py-10 text-center text-sm text-muted-foreground">
        <ThinkingDots label="Loading entity" />
        Loading entity…
      </p>
    );
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "map", label: "Explore connections" },
    { key: "summary", label: "Evidence brief & open questions" },
    { key: "act", label: "Research action" },
  ];

  const theme = themeOf(entity.node.type);
  const purpose = ENTITY_PURPOSE[entity.node.type] ?? { eyebrow: `${theme.label} record`, description: "Explore this entity's connections and inspect their evidence." };
  const facets = ENTITY_FACETS[entity.node.type];
  const facetCounts = facets?.map((facet) => ({
    label: facet.label,
    count: entity.neighbors.filter(({ node }) => facet.types.includes(node.type)).length,
  })).filter((facet) => facet.count > 0);

  return (
    <div className="space-y-7">
      <header
        className={`relative grid gap-7 overflow-visible rounded-2xl border border-dust border-l-4 bg-white px-6 py-7 sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(280px,420px)] lg:items-center ${theme.accent}`}
      >
        <div className="flex min-w-0 items-start gap-5">
          <span className={`flex size-16 shrink-0 items-center justify-center rounded-2xl border border-dust/70 bg-dust/20 ${theme.text}`}>
            <TypeGlyph type={entity.node.type} size={36} />
          </span>
          <div className="min-w-0">
            <p className={`text-xs font-semibold uppercase tracking-[0.16em] ${theme.text}`}>{purpose.eyebrow}</p>
            <h1 className="mt-2 text-2xl font-semibold leading-tight tracking-[-0.035em] text-pine sm:text-4xl">
              {displayLabel(entity.node.label, entity.node.type)}
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">{purpose.description}</p>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <TypeBadge type={entity.node.type} />
              <span className="rounded-full border border-dust/70 bg-dust/20 px-2.5 py-1">{entity.neighbors.length} direct connections</span>
              {facetCounts?.map((facet) => (
                <span key={facet.label} className="rounded-full border border-dust/70 bg-dust/20 px-2.5 py-1">
                  <strong className="text-pine">{facet.count}</strong> direct {facet.label}
                </span>
              ))}
            </div>
          {((entity.node.synonyms?.length ?? 0) > 0 ||
            displayLabel(entity.node.label, entity.node.type) !== entity.node.label) && (
            <p className="mt-3 text-xs text-gray-500">
              Also known as:{" "}
              {[
                ...(displayLabel(entity.node.label, entity.node.type) !== entity.node.label
                  ? [entity.node.label]
                  : []),
                ...(entity.node.synonyms ?? []),
              ].join(", ")}
            </p>
          )}
          </div>
        </div>
        <div className="rounded-2xl border border-dust/70 bg-dust/10 p-4">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Explore another name</p>
          <SearchBox />
        </div>
      </header>

      <ExcludedNotice id={entity.node.id} />
      {entity.no_supported_route && <HonestGapBanner entity={entity} />}

      <div className="overflow-x-auto border-b border-dust">
        <AnimatedTabs
          tabs={tabs.map((t) => ({ id: t.key, label: t.label }))}
          activeTab={tab}
          onChange={(id) => setTab(id as Tab)}
          variant="underline"
          className="w-full min-w-max border-dust"
        />
      </div>

      {tab === "summary" && (
        <div className="max-w-4xl space-y-6">
          <div className="rounded-2xl border border-dust bg-white p-6 sm:p-8">
            <SummaryTab entity={entity} />
          </div>
          <section className="rounded-2xl border border-dust bg-white p-6 sm:p-8">
            <h2 className="text-xs font-semibold uppercase tracking-[0.15em] text-muted-foreground">
              Open questions
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              What was searched, what evidence is missing, and what would change the answer.
            </p>
            <div className="mt-5">
              <GapsTab entityId={entity.node.id} />
            </div>
          </section>
        </div>
      )}
      {tab === "map" && <MapTab centerId={entity.node.id} onAct={() => setTab("act")} />}
      {tab === "act" && <div className="max-w-5xl"><ActTab entity={entity} /></div>}
    </div>
  );
}

function ExcludedNotice({ id }: { id: string }) {
  const [notice, setNotice] = useState<{ direction: string; reason: string } | null>(null);
  const direction = useSearchParams().get("excluded");
  useEffect(() => {
    if (direction !== "dampen" && direction !== "boost") return;
    let cancelled = false;
    apiMechanismSearch(direction).then((data) => {
      const match = data.excluded.find((item) => item.node.id === id);
      if (!cancelled && match) setNotice({ direction, reason: match.reason });
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [id, direction]);
  if (!notice || notice.direction !== direction) return null;
  return (
    <div className="rounded-2xl border border-rust/25 bg-rust/5 px-5 py-4 text-rust" role="status">
      <p className="text-xs font-semibold uppercase tracking-[0.15em] text-rust">Excluded from {notice.direction} candidates</p>
      <p className="mt-1 text-sm font-medium">{notice.reason}</p>
      <p className="mt-1 text-xs text-rust/80">This is an exclusion for the selected intervention direction, not a research lead.</p>
    </div>
  );
}

/** Prominent honest-gap state (ground-truth test 6). */
function HonestGapBanner({ entity }: { entity: EntityView }) {
  return (
    <div className="rounded-lg border border-rust/30 bg-rust/5 p-5">
      <p className="text-sm font-semibold text-rust">No supported route</p>
      {entity.next_question && (
        <p className="mt-2 text-sm leading-relaxed text-rust/90">{entity.next_question}</p>
      )}
      <div className="mt-4 rounded-md border border-rust/15 bg-white p-3">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
          What was searched
        </p>
        <CoverageTable coverage={entity.coverage} />
      </div>
    </div>
  );
}

function SummaryTab({ entity }: { entity: EntityView }) {
  return (
    <div className="space-y-8">
      <ul className="space-y-4">
        {entity.summary.map((line, i) => (
          <li key={i} className="text-[15px] leading-relaxed text-gray-800">
            <CitationText text={line} />
          </li>
        ))}
      </ul>
      <ExplainPanel entityId={entity.node.id} />
    </div>
  );
}

/** Explain affordance: POST /explain {ids:[entityId]}, cited plain language. */
function ExplainPanel({ entityId }: { entityId: string }) {
  const [explanation, setExplanation] = useState<ExplainResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const explain = () => {
    setBusy(true);
    setError(null);
    apiExplain([entityId])
      .then(setExplanation)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <div>
      <SmoothButton
        onClick={explain}
        disabled={busy}
        variant="outline"
        className="text-foreground hover:bg-dust/30"
      >
        {busy ? (
          <span className="inline-flex items-center gap-2">
            <ThinkingDots label="Explaining" />
            Explaining…
          </span>
        ) : (
          "Explain this entity"
        )}
      </SmoothButton>
      {error && <p className="mt-2 text-sm text-rust">{error}</p>}
      {explanation && (
        <div className="mt-4 rounded-lg border border-dust bg-dust/15 p-4">
          <div className="mb-2">
            <SourceLabel source={explanation.source} />
          </div>
          <p className="text-sm leading-relaxed text-gray-800">
            <CitationText text={explanation.text} />
          </p>
          {explanation.citations.length > 0 && (
            <p className="mt-3 text-xs text-gray-500">
              Citations:{" "}
              {explanation.citations.map((c) => (
                <CitationChip key={c} id={c} />
              ))}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** Map tab lazy-loads the whole graph (live /graph or the static bundle). */
function MapTab({ centerId, onAct }: { centerId: string; onAct: () => void }) {
  const [graph, setGraph] = useState<Graph | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGraph()
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (error) return <p className="text-sm text-rust">{error}</p>;
  if (!graph)
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <ThinkingDots label="Loading map" />
        Loading map…
      </p>
    );
  return <><GraphMap graph={graph} centerId={centerId} /><DiscoveryTrail graph={graph} centerId={centerId} onAct={onAct} /></>;
}
