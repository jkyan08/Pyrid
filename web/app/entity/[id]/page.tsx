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
import { themeOf } from "@/lib/type-theme";
import { CitationChip, CitationText } from "@/components/citation-text";
import { CoverageTable } from "@/components/coverage-table";
import { ActTab } from "@/components/act-tab";
import { GapsTab } from "@/components/gaps-tab";
import { GraphMap } from "@/components/graph-map";
import { DiscoveryTrail } from "@/components/discovery-trail";
import { SearchBox } from "@/components/search-box";
import AnimatedTabs from "@/components/smoothui/animated-tabs";
import MotionLoader from "@/components/smoothui/motion-loader";
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
    return <p className="py-10 text-center text-sm text-red-700">{error}</p>;
  }
  if (!entity) {
    return (
      <p className="flex items-center justify-center gap-2 py-10 text-center text-sm text-gray-400">
        <MotionLoader variant="orbit" size={18} label="Loading entity" />
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
        className={`relative grid gap-7 overflow-visible rounded-3xl border border-l-[6px] px-6 py-7 shadow-[0_12px_35px_rgba(15,23,42,0.04)] sm:px-8 lg:grid-cols-[minmax(0,1fr)_minmax(280px,420px)] lg:items-center ${theme.band} ${theme.accent}`}
      >
        <div className="flex min-w-0 items-start gap-5">
          <span className={`flex size-16 shrink-0 items-center justify-center rounded-2xl border border-white/80 bg-white/90 shadow-sm ${theme.text}`}>
            <TypeGlyph type={entity.node.type} size={36} />
          </span>
          <div className="min-w-0">
            <p className={`text-xs font-bold uppercase tracking-[0.16em] ${theme.text}`}>{purpose.eyebrow}</p>
            <h1 className="mt-2 text-2xl font-semibold leading-tight tracking-[-0.035em] text-slate-950 sm:text-4xl">
              {entity.node.label}
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">{purpose.description}</p>
            <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-slate-600">
              <TypeBadge type={entity.node.type} />
              <span className="rounded-full border border-white bg-white/75 px-2.5 py-1">{entity.neighbors.length} direct connections</span>
              {facetCounts?.map((facet) => (
                <span key={facet.label} className="rounded-full border border-white bg-white/75 px-2.5 py-1">
                  <strong className="text-slate-900">{facet.count}</strong> direct {facet.label}
                </span>
              ))}
            </div>
          {(entity.node.synonyms?.length ?? 0) > 0 && (
            <p className="mt-3 text-xs text-gray-500">
              Also known as: {entity.node.synonyms!.join(", ")}
            </p>
          )}
          </div>
        </div>
        <div className="rounded-2xl border border-white/80 bg-white/80 p-4 shadow-sm">
          <p className="mb-2 text-[11px] font-bold uppercase tracking-[0.14em] text-slate-500">Explore another name</p>
          <SearchBox />
        </div>
      </header>

      <ExcludedNotice id={entity.node.id} />
      {entity.no_supported_route && <HonestGapBanner entity={entity} />}

      <div className="overflow-x-auto border-b border-slate-200">
        <AnimatedTabs
          tabs={tabs.map((t) => ({ id: t.key, label: t.label }))}
          activeTab={tab}
          onChange={(id) => setTab(id as Tab)}
          variant="underline"
          className="w-full min-w-max border-gray-200"
        />
      </div>

      {tab === "summary" && (
        <div className="max-w-4xl space-y-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-8">
            <SummaryTab entity={entity} />
          </div>
          <section className="rounded-2xl border border-slate-200 bg-white p-6 sm:p-8">
            <h2 className="text-xs font-bold uppercase tracking-[0.15em] text-slate-500">
              Open questions
            </h2>
            <p className="mt-1 text-sm text-slate-500">
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
    <div className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-rose-950" role="status">
      <p className="text-xs font-bold uppercase tracking-[0.15em] text-rose-700">Excluded from {notice.direction} candidates</p>
      <p className="mt-1 text-sm font-medium">{notice.reason}</p>
      <p className="mt-1 text-xs text-rose-800">This is an exclusion for the selected intervention direction, not a research lead.</p>
    </div>
  );
}

/** Prominent honest-gap state (ground-truth test 6). */
function HonestGapBanner({ entity }: { entity: EntityView }) {
  return (
    <div className="rounded-lg border border-red-200 bg-red-50 p-5">
      <p className="text-sm font-semibold text-red-800">No supported route</p>
      {entity.next_question && (
        <p className="mt-2 text-sm leading-relaxed text-red-700">{entity.next_question}</p>
      )}
      <div className="mt-4 rounded-md border border-red-100 bg-white p-3">
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
        loading={busy}
        variant="outline"
        className="text-gray-800 hover:bg-gray-50"
      >
        {busy ? "Explaining…" : "Explain this entity"}
      </SmoothButton>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      {explanation && (
        <div className="mt-4 rounded-lg border border-gray-200 bg-gray-50/60 p-4">
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

  if (error) return <p className="text-sm text-red-700">{error}</p>;
  if (!graph)
    return (
      <p className="flex items-center gap-2 text-sm text-gray-400">
        <MotionLoader variant="orbit" size={18} label="Loading map" />
        Loading map…
      </p>
    );
  return <><GraphMap graph={graph} centerId={centerId} /><DiscoveryTrail graph={graph} centerId={centerId} onAct={onAct} /></>;
}
