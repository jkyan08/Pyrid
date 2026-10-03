"use client";

/**
 * Act tab (the brief's "patient action view"): assets with verdict chips,
 * "what differs" and needs-expert-review flags; investigators with a public
 * contact route ONLY when node.props.contact exists; a Next-step block with
 * a Draft button (output clearly labeled "Draft only — never sent"); and a
 * 10x card that renders ONLY when the graph provides sourced content for it.
 */

import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";
import type { DraftResponse, EntityView } from "@/lib/types";
import { apiDraft } from "@/lib/api";
import { CitationChip } from "./citation-text";
import { useEvidence } from "./evidence-drawer";
import { themeOf } from "@/lib/type-theme";
import { SourceLabel, TypeBadge, TypeGlyph, VerdictChip } from "./badges";
import SmoothButton from "@/components/smoothui/smooth-button";

/** Shared card entrance: subtle rise+fade, off under prefers-reduced-motion. */
function CardLi({
  children,
  index,
  className,
}: {
  children: React.ReactNode;
  index: number;
  className?: string;
}) {
  const shouldReduceMotion = useReducedMotion();
  return (
    <motion.li
      className={className}
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={
        shouldReduceMotion
          ? { duration: 0 }
          : { type: "spring", bounce: 0.1, duration: 0.35, delay: Math.min(index * 0.05, 0.3) }
      }
    >
      {children}
    </motion.li>
  );
}

export function ActTab({ entity }: { entity: EntityView }) {
  return (
    <div className="space-y-10">
      <AssetsSection entity={entity} />
      <InvestigatorsSection entity={entity} />
      <NextStepSection entity={entity} />
      <TenXCard entity={entity} />
    </div>
  );
}

function AssetsSection({ entity }: { entity: EntityView }) {
  const { openEvidence } = useEvidence();
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-900">Existing assets</h3>
      {entity.assets.length === 0 && (
        <p className="text-sm text-gray-500">
          No registry, study or patient group is linked to this entity in the current graph.
        </p>
      )}
      <ul className="space-y-4">
        {entity.assets.map(({ edge, node }, index) => {
          const attrs = edge.attributes ?? {};
          const differs = Array.isArray(attrs.differs) ? (attrs.differs as string[]) : [];
          const needsReview = Array.isArray(attrs.needs_review)
            ? (attrs.needs_review as string[])
            : [];
          const url = typeof node.props?.url === "string" ? (node.props.url as string) : null;
          const theme = themeOf(node.type);
          return (
            <CardLi
              key={edge.id}
              index={index}
              className={`rounded-lg border border-gray-200 border-l-4 p-4 ${theme.accent}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <TypeGlyph type={node.type} size={15} className={theme.text} />
                <span className="text-sm font-medium text-gray-900">{node.label}</span>
                <TypeBadge type={node.type} />
                <VerdictChip verdict={typeof attrs.verdict === "string" ? attrs.verdict : undefined} />
                <button
                  onClick={() => openEvidence(edge.id)}
                  className="ml-auto font-mono text-[11px] text-gray-400 underline decoration-gray-300 underline-offset-2 hover:text-sky-700"
                  title="Open the evidence for this connection"
                >
                  {edge.id}
                </button>
              </div>
              {url && (
                <p className="mt-1 text-xs">
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-sky-700 underline decoration-sky-300 underline-offset-2"
                  >
                    {url}
                  </a>
                </p>
              )}
              {differs.length > 0 && (
                <div className="mt-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    What differs
                  </p>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-gray-700">
                    {differs.map((d, i) => (
                      <li key={i}>{d}</li>
                    ))}
                  </ul>
                </div>
              )}
              {needsReview.length > 0 && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Needs expert review:
                  </p>
                  {needsReview.map((r) => (
                    <span
                      key={r}
                      className="rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-900"
                    >
                      {r} question
                    </span>
                  ))}
                </div>
              )}
            </CardLi>
          );
        })}
      </ul>
    </section>
  );
}

function InvestigatorsSection({ entity }: { entity: EntityView }) {
  const { openEvidence } = useEvidence();
  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-900">Investigators</h3>
      {entity.investigators.length === 0 && (
        <p className="text-sm text-gray-500">
          No investigator is linked to this entity in the current graph.
        </p>
      )}
      <ul className="space-y-3">
        {entity.investigators.map(({ edge, node }, index) => {
          const contact =
            typeof node.props?.contact === "string" ? (node.props.contact as string) : null;
          const affiliation =
            typeof node.props?.affiliation === "string" ? (node.props.affiliation as string) : null;
          const theme = themeOf(node.type);
          return (
            <CardLi
              key={edge.id}
              index={index}
              className={`rounded-lg border border-gray-200 border-l-4 p-4 ${theme.accent}`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <TypeGlyph type={node.type} size={15} className={theme.text} />
                <span className="text-sm font-medium text-gray-900">{node.label}</span>
                {affiliation && <span className="text-xs text-gray-500">{affiliation}</span>}
                <button
                  onClick={() => openEvidence(edge.id)}
                  className="ml-auto font-mono text-[11px] text-gray-400 underline decoration-gray-300 underline-offset-2 hover:text-sky-700"
                >
                  {edge.id}
                </button>
              </div>
              {contact ? (
                <p className="mt-1 text-xs">
                  Public contact route:{" "}
                  <a
                    href={contact}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="break-all text-sky-700 underline decoration-sky-300 underline-offset-2"
                  >
                    {contact}
                  </a>
                </p>
              ) : (
                <p className="mt-1 text-xs text-gray-400">
                  No public contact route on record — none is guessed.
                </p>
              )}
            </CardLi>
          );
        })}
      </ul>
    </section>
  );
}

function NextStepSection({ entity }: { entity: EntityView }) {
  const [draft, setDraft] = useState<DraftResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const makeDraft = () => {
    setBusy(true);
    setError(null);
    apiDraft(entity.node.id)
      .then(setDraft)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <section>
      <h3 className="mb-3 text-sm font-semibold text-gray-900">Next step</h3>
      <p className="text-sm leading-relaxed text-gray-600">
        Draft a sourced outreach proposal from the assets and investigators above — something a
        patient-group leader could act on this week. The draft cites its evidence and is never
        sent by this tool.
      </p>
      <SmoothButton
        onClick={makeDraft}
        loading={busy}
        variant="outline"
        className="mt-3 text-gray-800 hover:bg-gray-50"
      >
        {busy ? "Drafting…" : "Draft outreach proposal"}
      </SmoothButton>
      {error && <p className="mt-2 text-sm text-red-700">{error}</p>}
      {draft && (
        <div className="mt-4 rounded-lg border border-gray-300 bg-gray-50/70 p-4">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-red-200 bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-800">
              Draft only — never sent
            </span>
            <SourceLabel source={draft.source} />
          </div>
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-gray-800">
            {draft.text}
          </pre>
          {draft.citations.length > 0 && (
            <p className="mt-3 text-xs text-gray-500">
              Citations:{" "}
              {draft.citations.map((c) => (
                <CitationChip key={c} id={c} />
              ))}
            </p>
          )}
          <p className="mt-3 text-xs text-gray-400">{draft.disclaimer}</p>
        </div>
      )}
    </section>
  );
}

/**
 * 10x card placeholder: renders ONLY if the graph provides sourced content
 * for it (node.props.tenx_card with a non-empty sources list). No invented
 * numbers — absent data means the card is hidden entirely.
 */
function TenXCard({ entity }: { entity: EntityView }) {
  const card = entity.node.props?.tenx_card as
    | {
        milestone?: string;
        existing_timeline?: string;
        our_route?: string;
        assumptions?: string[];
        sources?: { kind?: string; id?: string }[];
      }
    | undefined;
  if (!card || typeof card !== "object" || !Array.isArray(card.sources) || card.sources.length === 0) {
    return null;
  }
  return (
    <section className="rounded-lg border border-gray-200 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-900">10× route</h3>
      <dl className="space-y-2 text-sm text-gray-700">
        {card.milestone && (
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Milestone</dt>
            <dd>{card.milestone}</dd>
          </div>
        )}
        {card.existing_timeline && (
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Existing timeline
            </dt>
            <dd>{card.existing_timeline}</dd>
          </div>
        )}
        {card.our_route && (
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Our route</dt>
            <dd>{card.our_route}</dd>
          </div>
        )}
        {Array.isArray(card.assumptions) && card.assumptions.length > 0 && (
          <div>
            <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Assumptions
            </dt>
            <dd>
              <ul className="list-disc pl-5">
                {card.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        <div>
          <dt className="text-xs font-semibold uppercase tracking-wide text-gray-500">Sources</dt>
          <dd className="break-all text-xs text-gray-500">
            {card.sources.map((s, i) => (
              <span key={i} className="mr-3">
                {s.kind ? `${s.kind}: ` : ""}
                {s.id ?? ""}
              </span>
            ))}
          </dd>
        </div>
      </dl>
    </section>
  );
}
