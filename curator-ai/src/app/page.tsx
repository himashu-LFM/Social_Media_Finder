import Link from "next/link";

export default function CoverPage() {
  const stars = Array.from({ length: 50 }, (_, i) => i + 1);

  const navLinks = [
    { label: "Discovery", href: "/discovery", icon: "dashboard" },
    { label: "Processing", href: "/processing", icon: "network_intel_node" },
    { label: "Results", href: "/results", icon: "table_chart" },
    { label: "Analysis", href: "/analysis", icon: "donut_large" },
  ];

  return (
    <div className="relative min-h-screen overflow-hidden bg-background">
      <div className="lf-stars lf-stars-cover" aria-hidden>
        {stars.map((n) => (
          <div key={n} className="lf-star" />
        ))}
      </div>

      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(circle_at_20%_15%,rgba(242,209,0,0.1),transparent_42%),radial-gradient(circle_at_75%_30%,rgba(56,189,248,0.08),transparent_45%),radial-gradient(circle_at_50%_100%,rgba(2,6,23,0.25),rgba(2,6,23,0.55))]"
      />

      <div className="relative z-10 mx-auto max-w-7xl px-4 pb-10 pt-5 md:px-8">
        <header className="lf-enter mb-10 flex items-center justify-between rounded-2xl border border-white/10 bg-slate-950/55 px-4 py-3 backdrop-blur-xl md:px-6">
          <Link href="/" className="flex items-center gap-3">
            <span className="inline-block h-8 w-8 rounded-full border border-primary/70 bg-primary/20 shadow-[0_0_20px_rgba(242,209,0,0.35)]" />
            <div className="text-lg font-extrabold tracking-wide text-white">
              LISTEN<span className="text-primary">FIRST</span>
            </div>
          </Link>
          <nav className="hidden items-center gap-2 text-sm lg:flex">
            {navLinks.map((item) => (
              <Link
                key={item.label}
                href={item.href}
                className="lf-btn-secondary inline-flex items-center gap-1.5 px-3 py-2 text-xs"
              >
                <span className="material-symbols-outlined text-base">{item.icon}</span>
                {item.label}
              </Link>
            ))}
          </nav>
          <div className="flex items-center gap-2">
            <Link href="/results" className="lf-btn-secondary hidden px-4 py-2 text-sm sm:inline-flex">
              Preview
            </Link>
            <Link href="/discovery" className="lf-btn-primary px-4 py-2 text-sm">
              Open Workspace
            </Link>
          </div>
        </header>

        <section className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <div className="lf-enter pt-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-bold uppercase tracking-widest text-primary">
              <span className="material-symbols-outlined text-sm">auto_awesome</span>
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
                className="lf-btn-primary inline-flex items-center gap-2 px-8 py-3 text-base"
              >
                <span className="material-symbols-outlined">rocket_launch</span>
                Open Discovery
              </Link>
              <Link
                href="/analysis"
                className="lf-btn-secondary inline-flex items-center gap-2 px-8 py-3 text-base"
              >
                <span className="material-symbols-outlined">donut_large</span>
                See Analysis
              </Link>
            </div>

            <div className="mt-8 grid max-w-xl grid-cols-1 gap-3 sm:grid-cols-3">
              <MetricCard label="Accuracy Focus" value="High Precision" icon="target" />
              <MetricCard label="Platforms" value="5 Networks" icon="hub" />
              <MetricCard label="Export" value="XLSX Ready" icon="description" />
            </div>
          </div>

          <div className="lf-enter lf-enter-delay-1 lf-card lf-gradient-border p-4 md:p-6">
            <div className="relative z-10">
              <div className="mb-4 flex items-center justify-between">
                <div className="inline-flex items-center gap-2 text-sm font-bold text-slate-100">
                  <span className="material-symbols-outlined text-primary">monitoring</span>
                  Overview Dashboard
                </div>
                <div className="rounded-lg border border-primary/25 bg-primary/10 px-2.5 py-1 text-xs font-semibold text-primary">
                  Live Preview
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <DashCard label="Profiles Processed" value="24,839" delta="+12.5%" icon="groups" />
                <DashCard label="Valid Profiles" value="18,392" delta="+8.2%" icon="verified" />
                <DashCard label="Platforms Covered" value="5" delta="All active" icon="language" />
                <DashCard label="Avg Confidence" value="94.7%" delta="+3.1%" icon="analytics" />
              </div>

              <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="rounded-xl border border-white/10 bg-slate-950/60 p-4">
                  <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                    <span className="material-symbols-outlined text-sm text-primary">route</span>
                    Pipeline Stages
                  </p>
                  <ul className="mt-3 space-y-2 text-sm text-slate-200">
                    {[
                      "Metadata-aware search expansion",
                      "Profile URL filtering",
                      "AI select + verify",
                      "Confidence scoring + export",
                    ].map((item) => (
                      <li key={item} className="flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                        {item}
                      </li>
                    ))}
                  </ul>
                </div>
                <div className="rounded-xl border border-white/10 bg-slate-950/60 p-4">
                  <p className="inline-flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                    <span className="material-symbols-outlined text-sm text-primary">pie_chart</span>
                    Confidence Distribution
                  </p>
                  <div className="mt-3 space-y-3 text-xs">
                    <BarRow label="90-100%" pct={68} tone="emerald" />
                    <BarRow label="75-89%" pct={22} tone="amber" />
                    <BarRow label="50-74%" pct={8} tone="rose" />
                    <BarRow label="0-49%" pct={2} tone="slate" />
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="lf-enter lf-enter-delay-2 mt-10 text-center text-xs tracking-[0.18em] text-slate-500">
          TRUSTED WORKFLOW FOR RESEARCH, TALENT OPS, AND SOCIAL ANALYST TEAMS
        </div>
      </div>
    </div>
  );
}

function MetricCard({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon: string;
}) {
  return (
    <div className="lf-card-hover rounded-xl border border-white/10 bg-slate-950/55 px-3 py-3">
      <span className="material-symbols-outlined text-base text-primary/80">{icon}</span>
      <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-bold text-slate-100">{value}</p>
    </div>
  );
}

function DashCard({
  label,
  value,
  delta,
  icon,
}: {
  label: string;
  value: string;
  delta: string;
  icon: string;
}) {
  return (
    <div className="lf-card-hover rounded-xl border border-white/10 bg-slate-900/75 p-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
        <span className="material-symbols-outlined text-base text-primary/70">{icon}</span>
      </div>
      <div className="mt-1 flex items-end justify-between gap-2">
        <p className="text-2xl font-black text-white">{value}</p>
        <span className="text-xs font-semibold text-emerald-300">{delta}</span>
      </div>
    </div>
  );
}

function BarRow({
  label,
  pct,
  tone,
}: {
  label: string;
  pct: number;
  tone: "emerald" | "amber" | "rose" | "slate";
}) {
  const barClass =
    tone === "emerald"
      ? "from-emerald-400 to-emerald-600"
      : tone === "amber"
        ? "from-amber-400 to-amber-600"
        : tone === "rose"
          ? "from-rose-400 to-rose-600"
          : "from-slate-400 to-slate-600";

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-slate-300">
        <span>{label}</span>
        <span>{pct}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-slate-800">
        <div
          className={`progress-shimmer h-full rounded-full bg-gradient-to-r ${barClass}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
