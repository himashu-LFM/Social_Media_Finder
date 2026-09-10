import Link from "next/link";

export function ResultsAnalysisButton({ jobId }: { jobId?: string }) {
  const href = jobId ? `/analysis?job=${encodeURIComponent(jobId)}` : "/analysis";
  return (
    <Link href={href} className="sc-btn">
      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
        donut_large
      </span>
      Analysis
    </Link>
  );
}
