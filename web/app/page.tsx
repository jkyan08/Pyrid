import Link from "next/link";
import { SearchBox } from "@/components/search-box";

export default function HomePage() {
  return (
    <div className="grid min-h-[calc(100vh-13rem)] items-center gap-12 py-8 lg:grid-cols-[minmax(0,1.25fr)_minmax(300px,.75fr)] lg:gap-20">
      <section className="max-w-4xl">
        <p className="mb-5 text-xs font-bold uppercase tracking-[0.2em] text-sky-700">Explore the evidence</p>
        <h1 className="max-w-3xl text-4xl font-semibold leading-[1.06] tracking-[-0.045em] text-slate-950 sm:text-5xl lg:text-6xl">
          Start with a disease. <span className="text-sky-700">Find a research route.</span>
        </h1>
        <p className="mt-6 max-w-2xl text-base leading-7 text-slate-600 sm:text-lg">
          Follow a name through mechanisms, genes, related diseases and patient groups. Open any
          connection to see why it exists and where its evidence came from.
        </p>
        <div className="mt-10 rounded-2xl border border-slate-200 bg-white p-3 shadow-[0_18px_60px_rgba(15,23,42,0.08)] sm:p-5">
          <SearchBox autoFocus hero />
        </div>
        <p className="mt-5 text-sm text-slate-500">
          Starting from a biological direction?{" "}
          <Link href="/mechanism" className="font-semibold text-amber-800 underline decoration-amber-300 underline-offset-4 hover:text-amber-950">
            Search mechanisms instead <span aria-hidden="true">↗</span>
          </Link>
        </p>
      </section>

      <aside className="relative overflow-hidden rounded-[1.75rem] border border-slate-200 bg-slate-950 p-7 text-white shadow-2xl shadow-slate-200/80 sm:p-9">
        <div className="absolute -right-16 -top-20 size-64 rounded-full border border-sky-300/20" aria-hidden="true" />
        <div className="absolute -right-4 -top-8 size-44 rounded-full border border-sky-300/20" aria-hidden="true" />
        <p className="text-xs font-bold uppercase tracking-[0.2em] text-sky-300">What the atlas helps you do</p>
        <ol className="relative mt-8 space-y-8">
          {[
            ["01", "Trace the biology", "Move from a disease to a disrupted pathway, another gene and a related condition."],
            ["02", "Check what can be shared", "Inspect registries, studies, differences and questions needing expert review."],
            ["03", "Leave with a next step", "Build a sourced outreach draft, or see the evidence gap and the next question to test."],
          ].map(([number, title, detail]) => (
            <li key={number} className="flex gap-4">
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-sky-400/50 bg-sky-400/10 font-mono text-xs text-sky-200">{number}</span>
              <div>
                <h2 className="font-semibold tracking-tight">{title}</h2>
                <p className="mt-1 text-sm leading-6 text-slate-300">{detail}</p>
              </div>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
