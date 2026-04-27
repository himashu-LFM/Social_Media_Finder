import Link from "next/link";

export default function CoverPage() {
  const stars = Array.from({ length: 50 }, (_, i) => i + 1);

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#030816]">
      <div className="lf-stars lf-stars-cover" aria-hidden>
        {stars.map((n) => (
          <div key={n} className="lf-star" />
        ))}
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(circle_at_20%_15%,rgba(30,58,138,0.18),transparent_52%),radial-gradient(circle_at_75%_30%,rgba(56,189,248,0.12),transparent_45%),radial-gradient(circle_at_50%_100%,rgba(2,6,23,0.25),rgba(2,6,23,0.55))]"
      />

      <div className="relative z-10 mx-auto max-w-7xl px-4 pb-10 pt-5 md:px-8">
        <header className="mb-10 flex items-center justify-between rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 backdrop-blur-md md:px-6">
          <div className="flex items-center gap-3">
            <span className="inline-block h-8 w-8 rounded-full border border-primary/70 bg-primary/20" />
            <div className="text-lg font-extrabold tracking-wide text-white">
              LISTEN<span className="text-primary">FIRST</span>
            </div>
          </div>
          <nav className="hidden items-center gap-6 text-sm text-slate-300 lg:flex">
            <span className="cursor-default">Discovery</span>
            <span className="cursor-default">Processing</span>
            <span className="cursor-default">Results</span>
            <span className="cursor-default">Analysis</span>
          </nav>
          <div className="flex items-center gap-2">
            <Link
              href="/results"
              className="rounded-xl border border-white/15 bg-slate-900/60 px-4 py-2 text-sm font-semibold text-slate-100"
            >
              Preview
            </Link>
            <Link
              href="/discovery"
              className="rounded-xl bg-primary px-4 py-2 text-sm font-bold text-slate-900 shadow-[0_8px_24px_rgba(242,209,0,0.35)]"
            >
              Open Workspace
            </Link>
          </div>
        </header>

        <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <div className="pt-2">
            <span className="inline-flex rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-bold uppercase tracking-widest text-primary">
              AI-powered profile intelligence
            </span>
            <h1 className="mt-5 text-4xl font-black leading-tight text-white md:text-6xl">
              Social Intelligence
              <br />
              <span className="text-primary">Workspace</span>
            </h1>
            <p className="mt-5 max-w-xl text-base leading-relaxed text-slate-300">
              Upload talent lists, run deep AI discovery, validate profile quality, and export
              production-ready social links with confidence scoring.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/discovery"
                className="inline-flex items-center justify-center rounded-2xl bg-primary px-8 py-3 text-base font-black text-slate-900 shadow-[0_10px_36px_rgba(242,209,0,0.35)] transition hover:scale-[1.02]"
              >
                Open
              </Link>
              <Link
                href="/analysis"
                className="inline-flex items-center justify-center rounded-2xl border border-white/20 bg-slate-950/40 px-8 py-3 text-base font-semibold text-slate-100"
              >
                See Analysis
              </Link>
            </div>

            <div className="mt-8 grid max-w-xl grid-cols-1 gap-3 sm:grid-cols-3">
              <MetricCard label="Accuracy Focus" value="High Precision" />
              <MetricCard label="Platforms" value="5 Networks" />
              <MetricCard label="Export" value="XLSX Ready" />
            </div>
          </div>

          <div className="rounded-3xl border border-white/10 bg-slate-950/65 p-4 shadow-2xl ring-1 ring-white/10 backdrop-blur-md md:p-6">
            <div className="mb-4 flex items-center justify-between">
              <div className="text-sm font-bold text-slate-100">Overview Dashboard</div>
              <div className="rounded-lg border border-white/10 bg-slate-900/80 px-2.5 py-1 text-xs text-slate-400">
                Live Preview
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <DashCard label="Profiles Processed" value="24,839" delta="+12.5%" />
              <DashCard label="Valid Profiles" value="18,392" delta="+8.2%" />
              <DashCard label="Platforms Covered" value="5" delta="All active" />
              <DashCard label="Avg Confidence" value="94.7%" delta="+3.1%" />
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
              <div className="rounded-xl border border-white/10 bg-slate-900/70 p-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Pipeline Stages
                </p>
                <ul className="mt-3 space-y-2 text-sm text-slate-200">
                  <li>1. Metadata-aware search expansion</li>
                  <li>2. Profile URL filtering</li>
                  <li>3. AI select + verify</li>
                  <li>4. Confidence scoring + export</li>
                </ul>
              </div>
              <div className="rounded-xl border border-white/10 bg-slate-900/70 p-4">
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Confidence Distribution
                </p>
                <div className="mt-3 space-y-3 text-xs">
                  <BarRow label="90-100%" pct={68} />
                  <BarRow label="75-89%" pct={22} />
                  <BarRow label="50-74%" pct={8} />
                  <BarRow label="0-49%" pct={2} />
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="mt-10 text-center text-xs tracking-[0.18em] text-slate-500">
          TRUSTED WORKFLOW FOR RESEARCH, TALENT OPS, AND SOCIAL ANALYST TEAMS
        </div>
      </div>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-950/55 px-3 py-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-bold text-slate-100">{value}</p>
    </div>
  );
}

function DashCard({
  label,
  value,
  delta,
}: {
  label: string;
  value: string;
  delta: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-slate-900/75 p-3">
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-2">
        <p className="text-2xl font-black text-white">{value}</p>
        <span className="text-xs font-semibold text-emerald-300">{delta}</span>
      </div>
    </div>
  );
}

function BarRow({ label, pct }: { label: string; pct: number }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-slate-300">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
