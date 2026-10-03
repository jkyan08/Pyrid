"use client";

/**
 * Entity page: progressive reveal with tabs (Summary / Map / Act / Gaps).
 * When the entity has no supported route, the honest-gap state is shown
 * prominently above the tabs: "no supported route", the search coverage,
 * and the next question.
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type { EntityView, ExplainResponse, Graph } from "@/lib/types";
import { apiEntity, apiExplain, apiGraph, NotFoundError } from "@/lib/api";
import { SourceLabel, TypeBadge, TypeGlyph } from "@/components/badges";
import { themeOf } from "@/lib/type-theme";
import { CitationChip, CitationText } from "@/components/citation-text";
import { CoverageTable } from "@/components/coverage-table";
import { ActTab } from "@/components/act-tab";
import { GapsTab } from "@/components/gaps-tab";
import { GraphMap } from "@/components/graph-map";
import AnimatedTabs from "@/components/smoothui/animated-tabs";
import MotionLoader from "@/components/smoothui/motion-loader";
import SmoothButton from "@/components/smoothui/smooth-button";

type Tab = "summary" | "map" | "act" | "gaps";

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
  const [tab, setTab] = useState<Tab>("summary");

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
    { key: "summary", label: "Summary" },
    { key: "map", label: "Map" },
    { key: "act", label: "Act" },
    { key: "gaps", label: "Gaps" },
  ];

  const theme = themeOf(entity.node.type);

  return (
    <div className="space-y-6">
      {/* Type-tinted header band: a Drug page is instantly distinct from a
          Disease page (accent border, tint and a big hand-drawn glyph). */}
      <header
        className={`flex items-start gap-4 rounded-xl border border-l-4 px-5 py-4 ${theme.band} ${theme.accent}`}
      >
        <span
          className={`mt-0.5 flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-white/80 bg-white/90 shadow-sm ${theme.text}`}
        >
          <TypeGlyph type={entity.node.type} size={28} />
        </span>
        <div className="min-w-0 space-y-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-xl font-semibold tracking-tight text-gray-900 sm:text-2xl">
              {entity.node.label}
            </h1>
            <TypeBadge type={entity.node.type} />
          </div>
          {(entity.node.synonyms?.length ?? 0) > 0 && (
            <p className="text-xs text-gray-500">
              Also known as: {entity.node.synonyms!.join(", ")}
            </p>
          )}
          {entity.cluster_ids.length > 0 && (
            <p className="text-xs text-gray-400">
              Cluster: {entity.cluster_ids.join(", ")}
            </p>
          )}
        </div>
      </header>

      {entity.no_supported_route && <HonestGapBanner entity={entity} />}

      <div className="overflow-x-auto">
        <AnimatedTabs
          tabs={tabs.map((t) => ({ id: t.key, label: t.label }))}
          activeTab={tab}
          onChange={(id) => setTab(id as Tab)}
          variant="underline"
          className="w-full min-w-max border-gray-200"
        />
      </div>

      {tab === "summary" && <SummaryTab entity={entity} />}
      {tab === "map" && (
        /* The map breaks out of the text column: centered, up to max-w-6xl
           (min with the viewport so no horizontal scroll appears). */
        <div className="relative left-1/2 w-[min(72rem,calc(100vw-2rem))] -translate-x-1/2">
          <MapTab centerId={entity.node.id} />
        </div>
      )}
      {tab === "act" && <ActTab entity={entity} />}
      {tab === "gaps" && <GapsTab entityId={entity.node.id} />}
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
function MapTab({ centerId }: { centerId: string }) {
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
  return <GraphMap graph={graph} centerId={centerId} />;
}
