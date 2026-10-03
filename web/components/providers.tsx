"use client";

import { useEffect, useState, type ReactNode } from "react";
import { subscribeApiMode, type ApiMode } from "@/lib/api";
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

export function Providers({ children }: { children: ReactNode }) {
  return (
    <EvidenceProvider>
      <FallbackBanner />
      {children}
    </EvidenceProvider>
  );
}
