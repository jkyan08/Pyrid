"use client";

import Link from "next/link";
import type { Graph } from "@/lib/types";
import { discoveryRoutes } from "@/lib/graph";
import { themeOf } from "@/lib/type-theme";
import { TypeGlyph } from "./badges";
import { useEvidence } from "./evidence-drawer";
import { displayLabel } from "@/lib/display";

const ROUTE_COPY: Record<string, string> = {
  Pathway: "Disrupted biology",
  Gene: "Connected gene",
  AnotherGene: "Another gene in the pathway",
  Disease: "Related condition",
  PatientGroup: "Patient community",
  Registry: "Existing registry",
  Study: "Study design",
};

export function DiscoveryTrail({ graph, centerId, onAct }: {
  graph: Graph;
  centerId: string;
  onAct: () => void;
}) {
  const { openEvidence } = useEvidence();
  const routes = discoveryRoutes(graph, centerId);

  return (
    <section className="mt-8" aria-labelledby="trail-heading">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-hunter">From the graph</p>
          <h2 id="trail-heading" className="mt-1 text-2xl font-semibold tracking-tight text-pine">Follow the evidence trail</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
            Each route is a chain of graph edges. Open an edge to inspect its explanation, sources and any contradictory claims.
          </p>
        </div>
        <button onClick={onAct} className="rounded-full bg-hunter px-5 py-2.5 text-sm font-semibold text-white transition-[transform,background-color] hover:-translate-y-0.5 hover:bg-fern motion-reduce:transform-none">
          Check reusable research <span aria-hidden="true">↗</span>
        </button>
      </div>
      {routes.length === 0 ? (
        <div className="rounded-2xl border border-ochre/30 bg-ochre/10 p-6 text-sm text-ochre">
          No connected pathway, gene, related disease or research asset was found in the current graph. Check the Evidence brief&apos;s Open questions section for the search coverage and a next question to test.
        </div>
      ) : (
        <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
          {routes.map((route) => {
            const theme = themeOf(route.node.type);
            const unsupported = route.steps.some(({ edge }) => edge.status !== "supported");
            return (
              <article key={route.type} className={`rounded-2xl border border-dust border-l-4 bg-white p-5 ${theme.accent}`}>
                <div className="flex items-start gap-3">
                  <span className={`flex size-10 shrink-0 items-center justify-center rounded-xl ${theme.band} ${theme.text}`}>
                    <TypeGlyph type={route.node.type} size={22} />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.13em] text-muted-foreground">{ROUTE_COPY[route.type]}</p>
                    <Link href={`/entity/${encodeURIComponent(route.node.id)}`} className="mt-0.5 block font-semibold leading-snug text-pine underline decoration-dust underline-offset-4 hover:decoration-fern">
                      {displayLabel(route.node.label, route.node.type)}
                    </Link>
                  </div>
                </div>
                <ol className="mt-4 space-y-2 border-t border-dust/60 pt-3">
                  {route.steps.map(({ from, edge, to }) => (
                    <li key={edge.id} className="text-xs leading-5 text-muted-foreground">
                      <span className="font-medium text-foreground">{displayLabel(from.label, from.type)}</span>{" "}
                      <span className="text-muted-foreground/70">→</span>{" "}
                      <span className="font-medium text-foreground">{displayLabel(to.label, to.type)}</span>
                      <button onClick={() => openEvidence(edge.id)} className="mt-1 block text-left font-semibold text-hunter underline decoration-sage underline-offset-2 hover:text-fern">
                        {edge.relation.replaceAll("_", " ")} · {edge.sources?.length ? `${edge.sources.length} source${edge.sources.length === 1 ? "" : "s"}` : "source missing"} <span aria-hidden="true">↗</span>
                      </button>
                    </li>
                  ))}
                </ol>
                {unsupported && <p className="mt-3 rounded-lg bg-ochre/10 px-3 py-2 text-xs font-medium text-ochre">Includes an inferred or unsupported connection. Review before using this route.</p>}
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
