import Link from "next/link";

export function ResultsAnalysisButton() {
  return (
    <Link
      href="/analysis"
      className="lf-btn-secondary inline-flex items-center gap-2 px-4 py-2.5 text-sm"
    >
      <span className="material-symbols-outlined text-base text-primary">donut_large</span>
      See Analysis
    </Link>
  );
}
