import type { Metadata } from "next";
import Link from "next/link";
import { AppMobileNav } from "@/components/AppMobileNav";
import { AppSidebar } from "@/components/AppSidebar";
import { ProcessingRunner } from "@/components/ProcessingRunner";

export const metadata: Metadata = {
  title: "Processing | ListenFirst",
  description: "Per-name discovery progress for social profile resolution.",
};

export default function ProcessingPage() {
  const stars = Array.from({ length: 50 }, (_, i) => i + 1);

  return (
    <div className="relative flex min-h-screen flex-col bg-background md:flex-row">
      <div className="lf-stars" aria-hidden>
        {stars.map((n) => (
          <div key={n} className="lf-star" />
        ))}
      </div>

      <AppSidebar />

      <main className="relative z-10 flex min-h-screen flex-1 flex-col pb-24 md:ml-64 md:pb-0">
        <header className="sticky top-0 z-30 flex w-full items-center justify-between border-b border-white/5 bg-background/85 px-6 py-4 backdrop-blur-xl">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/25 shadow-lg shadow-primary/10">
              <span className="material-symbols-outlined text-primary">sync</span>
            </div>
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-primary/80">
                Live pipeline
              </p>
              <h1 className="text-lg font-bold text-slate-100">Processing</h1>
            </div>
          </div>
          <Link
            href="/results"
            className="proc-btn-glow group inline-flex cursor-pointer items-center gap-2 rounded-xl bg-slate-800/90 px-4 py-2.5 text-sm font-semibold text-slate-100 ring-1 ring-white/10"
          >
            <span className="material-symbols-outlined text-base text-primary transition-transform group-hover:scale-110">
              table_chart
            </span>
            View Results
            <span className="material-symbols-outlined text-base opacity-60 transition-transform group-hover:translate-x-0.5">
              arrow_forward
            </span>
          </Link>
        </header>

        <div className="flex flex-1 flex-col">
          <ProcessingRunner />
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}
