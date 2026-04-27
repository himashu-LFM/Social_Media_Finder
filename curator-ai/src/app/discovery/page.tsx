import { AppMobileNav } from "@/components/AppMobileNav";
import { AppSidebar } from "@/components/AppSidebar";
import { DiscoveryFileUpload } from "@/components/DiscoveryFileUpload";
import { DiscoveryWorkspace } from "@/components/DiscoveryWorkspace";

const outputColumns = [
  "Talent Name",
  "title_category",
  "title_sub_category",
  "Facebook",
  "Instagram",
  "X",
  "TikTok",
  "YouTube",
  "Confidence",
  "Source",
];

export default function DiscoveryPage() {
  return (
    <div className="flex min-h-screen flex-col bg-background md:flex-row">
      <AppSidebar />

      <main className="min-h-screen flex-1 pb-24 md:ml-64 md:pb-0">
        <header className="sticky top-0 z-30 flex w-full items-center justify-between border-b border-white/5 bg-background/80 px-6 py-4 backdrop-blur-md">
          <h1 className="text-lg font-bold text-slate-100">Discovery</h1>
          <span className="rounded-full border border-primary/40 bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">
            ListenFirst Product Workspace
          </span>
        </header>

        <div className="mx-auto max-w-6xl px-6 py-8">
          <section className="mb-10">
            <h2 className="mb-3 text-4xl font-extrabold tracking-tight text-slate-50 md:text-5xl">
              ListenFirst Social Resolver
            </h2>
          </section>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="space-y-6 lg:col-span-2">
              <DiscoveryWorkspace />

              <div className="rounded-2xl bg-surface p-6 ring-1 ring-white/5">
                <h3 className="mb-4 text-lg font-bold text-slate-100">Expected Output Columns</h3>
                <div className="flex flex-wrap gap-2">
                  {outputColumns.map((col) => (
                    <span
                      key={col}
                      className="rounded-full bg-slate-800/80 px-3 py-1 text-xs font-semibold text-slate-300 ring-1 ring-white/10"
                    >
                      {col}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-6">
              <DiscoveryFileUpload />

              <div className="rounded-2xl bg-slate-950 p-6 ring-1 ring-white/10">
                <div className="mb-3 flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary">auto_awesome</span>
                  <span className="text-xs font-bold uppercase tracking-widest text-primary">
                    Pipeline Logic
                  </span>
                </div>
                <ul className="space-y-2 text-sm text-slate-300">
                  <li>1. Serper query expansion with metadata.</li>
                  <li>2. Platform profile URL filtering (no posts/reels/videos).</li>
                  <li>3. AI selection + confidence thresholding.</li>
                  <li>4. Optional bio/link-hub enrichment for missing platforms.</li>
                  <li>5. XLSX export with confidence highlighting.</li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </main>

      <AppMobileNav />
    </div>
  );
}

