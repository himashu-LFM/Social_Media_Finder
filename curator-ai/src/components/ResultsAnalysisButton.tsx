import Link from "next/link";

export function ResultsAnalysisButton({ jobId }: { jobId?: string }) {
  const href = jobId ? `/analysis?job=${encodeURIComponent(jobId)}` : "/analysis";
  return (
    <Link
      href={href}
      className="lf-btn-secondary inline-flex items-center gap-2 px-4 py-2.5 text-sm"
    >
      <span className="material-symbols-outlined text-base text-primary">donut_large</span>
      See Analysis
    </Link>
  );
}
