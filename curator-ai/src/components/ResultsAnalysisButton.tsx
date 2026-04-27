import Link from "next/link";

export function ResultsAnalysisButton() {
  return (
    <Link
      href="/analysis"
      className="inline-flex items-center gap-2 rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-100 ring-1 ring-white/10 transition hover:bg-slate-700"
    >
      <span className="material-symbols-outlined text-base">donut_large</span>
      See Analysis
    </Link>
  );
}

