"use client";

import { useEffect, useState, type ReactNode } from "react";
import { API_BASE, subscribeApiMode, type ApiMode } from "@/lib/api";
import { shouldShowMockRibbon } from "@/lib/health";
import { EvidenceProvider } from "./evidence-drawer";

/** Thin banner shown whenever the app is running from the static bundle. */
function FallbackBanner() {
  const [mode, setMode] = useState<ApiMode>("unknown");
  useEffect(() => subscribeApiMode(setMode), []);
  if (mode !== "fallback") return null;
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-center text-xs text-amber-900">
      Static-fallback mode: the API is unreachable, so views are computed from a bundled graph
      snapshot. Explanations and drafts are cached only.
    </div>
  );
}

/**
 * Slim amber ribbon (README hard rule 10): shown only when GET /health
 * answered with mode === "mock". Unreachable /health is a silent no-op —
 * the static-fallback banner above covers that case. Coexists with it.
 */
function MockDataRibbon() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/health`)
      .then((r) => (r.ok ? r.json() : null))
      .then((health) => {
        if (!cancelled && shouldShowMockRibbon(health)) setShow(true);
      })
      .catch(() => {
        /* silent no-op: fallback banner covers the unreachable case */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  if (!show) return null;
  return (
    <div className="border-b border-amber-200 bg-amber-50 px-4 py-1.5 text-center text-xs text-amber-900">
      Placeholder (mock) data — not sourced
    </div>
  );
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <EvidenceProvider>
      <FallbackBanner />
      <MockDataRibbon />
      {children}
    </EvidenceProvider>
  );
}
