"use client";

/**
 * Evidence drawer: a slide-over openable from ANY edge or claim reference.
 * Edge ids (e_…) show the full edge evidence view; claim ids (c_…) show the
 * claim detail. Exposed through context so any component can call
 * `openEvidence(id)`.
 *
 * Built on the SmoothUI drawer (motion-staggered content over the base-ui
 * drawer primitive): slides in from the right, Esc / backdrop / swipe close
 * it, and animations respect prefers-reduced-motion.
 */

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";
import type { Claim, EdgeView } from "@/lib/types";
import { apiEdge, apiGraph } from "@/lib/api";
import { displayLabel } from "@/lib/display";
import { claimById } from "@/lib/graph";
import SmoothDrawer, { DrawerTitle } from "@/components/smoothui/drawer";
import ThinkingDots from "@/components/smoothui/thinking-dots";
import { themeOf } from "@/lib/type-theme";
import { EvidenceClassBadge, StatusChip, TypeGlyph } from "./badges";

interface EvidenceContextValue {
  openEvidence: (id: string) => void;
}

const EvidenceContext = createContext<EvidenceContextValue>({ openEvidence: () => {} });

export function useEvidence() {
  return useContext(EvidenceContext);
}

type DrawerState =
  | { kind: "closed" }
  | { kind: "loading"; id: string }
  | { kind: "edge"; view: EdgeView }
  | { kind: "claim"; claim: Claim }
  | { kind: "error"; id: string; message: string };

export function EvidenceProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DrawerState>({ kind: "closed" });
  // Kept separate from `state` so the content stays mounted while the
  // drawer's slide-out transition plays.
  const [open, setOpen] = useState(false);

  const openEvidence = useCallback((id: string) => {
    setOpen(true);
    setState({ kind: "loading", id });
    if (/^c_/.test(id)) {
      apiGraph()
        .then((graph) => {
          const claim = claimById(graph, id);
          if (claim) setState({ kind: "claim", claim });
          else setState({ kind: "error", id, message: `Claim ${id} is not in the loaded graph.` });
        })
        .catch((e) => setState({ kind: "error", id, message: String(e) }));
    } else {
      apiEdge(id)
        .then((view) => setState({ kind: "edge", view }))
        .catch((e) => setState({ kind: "error", id, message: String(e) }));
    }
  }, []);

  const close = useCallback(() => setOpen(false), []);

  return (
    <EvidenceContext.Provider value={{ openEvidence }}>
      {children}
      <SmoothDrawer
        open={open}
        onOpenChange={(next) => {
          if (!next) close();
        }}
        side="right"
        className="data-[swipe-axis=x]:[--drawer-content-width:100%] data-[swipe-axis=x]:sm:[--drawer-content-width:36rem]"
      >
        <div className="sticky top-0 z-10 -mx-4 mb-4 flex items-start justify-between gap-4 bg-popover px-4 pt-5 pb-3">
          <DrawerTitle className="font-semibold text-gray-500 text-sm uppercase tracking-wide">
            Evidence
          </DrawerTitle>
          <button
            onClick={close}
            className="rounded border border-dust px-2 py-1 text-xs text-muted-foreground hover:bg-dust/25"
          >
            Close (esc)
          </button>
        </div>
        {state.kind === "loading" && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <ThinkingDots label="Loading evidence" />
            Loading {state.id}…
          </p>
        )}
        {state.kind === "error" && <p className="text-sm text-rust">{state.message}</p>}
        {state.kind === "edge" && <EdgeEvidence view={state.view} />}
        {state.kind === "claim" && <ClaimCard claim={state.claim} standalone />}
      </SmoothDrawer>
    </EvidenceContext.Provider>
  );
}

function EdgeEvidence({ view }: { view: EdgeView }) {
  const { edge } = view;
  const basis = edge.confidence_basis ?? {};
  return (
    <div className="space-y-6">
      <div>
        {/* Endpoint type glyphs + a relation chip, so the kind of connection
            is readable at a glance before the text is. */}
        <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-base font-medium text-gray-900">
          <TypeGlyph
            type={view.source_node?.type ?? "Unknown"}
            size={16}
            className={`shrink-0 ${themeOf(view.source_node?.type).text}`}
          />
          <span>{view.source_node ? displayLabel(view.source_node.label, view.source_node.type) : edge.source}</span>
          <span className="mx-0.5 inline-flex items-center gap-1 rounded-full border border-gray-200 bg-gray-50 px-2 py-0.5 text-xs font-normal text-gray-600">
            {edge.relation.replace(/_/g, " ")}
            <span aria-hidden="true">→</span>
          </span>
          <TypeGlyph
            type={view.target_node?.type ?? "Unknown"}
            size={16}
            className={`shrink-0 ${themeOf(view.target_node?.type).text}`}
          />
          <span>{view.target_node ? displayLabel(view.target_node.label, view.target_node.type) : edge.target}</span>
        </p>
        <p className="mt-1 font-mono text-[11px] text-gray-400">{edge.id}</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <EvidenceClassBadge evidenceClass={edge.evidence_class} />
        {view.labels.map((l) => (
          <StatusChip key={l} label={l} />
        ))}
        {edge.attributes?.origin ? <StatusChip label={String(edge.attributes.origin)} /> : null}
      </div>

      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
          Confidence
        </h3>
        <p className="text-sm text-gray-800">
          {typeof edge.confidence === "number" ? edge.confidence.toFixed(2) : "not stated"}
        </p>
        {Object.keys(basis).length > 0 && (
          <dl className="mt-2 space-y-1">
            {Object.entries(basis).map(([k, v]) => (
              <div key={k} className="flex gap-2 text-xs text-gray-600">
                <dt className="shrink-0 font-medium text-gray-500">{k}:</dt>
                <dd>{typeof v === "string" ? v : JSON.stringify(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>

      {(edge.sources?.length ?? 0) > 0 && (
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Sources
          </h3>
          <ul className="space-y-1 text-sm">
            {edge.sources!.map((s, i) => (
              <li key={i} className="text-gray-700">
                <SourceRef kind={s.kind} id={s.id} />
                {s.date ? <span className="ml-2 text-xs text-gray-400">{s.date}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-hunter">
            Supporting ({view.supporting_claims.length})
          </h3>
          {view.supporting_claims.length === 0 && (
            <p className="text-xs text-gray-400">No supporting claims on this edge.</p>
          )}
          <div className="space-y-3">
            {view.supporting_claims.map((c) => (
              <ClaimCard key={c.id} claim={c} />
            ))}
          </div>
        </div>
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-rust">
            Contradicting ({view.contradicting_claims.length})
          </h3>
          {view.contradicting_claims.length === 0 && (
            <p className="text-xs text-gray-400">No contradicting claims on this edge.</p>
          )}
          <div className="space-y-3">
            {view.contradicting_claims.map((c) => (
              <ClaimCard key={c.id} claim={c} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** pmid link only when the pmid looks numeric (the mock uses "MOCK"). */
function PmidRef({ pmid }: { pmid: string }) {
  if (/^\d+$/.test(pmid)) {
    return (
      <a
        href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-hunter underline decoration-sage underline-offset-2 hover:text-fern"
      >
        PMID {pmid}
      </a>
    );
  }
  return <span className="text-gray-500">PMID: {pmid}</span>;
}

function SourceRef({ kind, id }: { kind: string; id: string }) {
  if (kind === "url" && /^https?:\/\//.test(id)) {
    return (
      <a
        href={id}
        target="_blank"
        rel="noopener noreferrer"
        className="break-all text-hunter underline decoration-sage underline-offset-2 hover:text-fern"
      >
        {id}
      </a>
    );
  }
  if (kind === "pmid") return <PmidRef pmid={id} />;
  return (
    <span className="break-all text-gray-700">
      <span className="text-xs uppercase text-gray-400">{kind}</span> {id}
    </span>
  );
}

function ClaimCard({ claim, standalone = false }: { claim: Claim; standalone?: boolean }) {
  return (
    <div className="rounded-md border border-dust bg-dust/15 p-3">
      {standalone && (
        <p className="mb-2 font-mono text-[11px] text-gray-400">
          {claim.id}
          {claim.polarity ? ` · ${claim.polarity}` : ""}
        </p>
      )}
      {claim.quote ? (
        <blockquote className="text-sm italic text-gray-800">“{claim.quote}”</blockquote>
      ) : (
        <p className="text-xs text-gray-400">No quote recorded.</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-gray-600">
        {claim.study_type && <span>{claim.study_type.replace(/_/g, " ")}</span>}
        <span>
          n={claim.n_patients === null || claim.n_patients === undefined ? "—" : claim.n_patients}
        </span>
        {claim.pmid ? <PmidRef pmid={claim.pmid} /> : null}
      </div>
      {standalone && (claim.subject || claim.predicate || claim.object) && (
        <p className="mt-2 break-all font-mono text-[11px] text-gray-500">
          {claim.subject} → {claim.predicate} → {claim.object}
          {claim.variant ? ` (variant: ${claim.variant})` : ""}
        </p>
      )}
    </div>
  );
}
