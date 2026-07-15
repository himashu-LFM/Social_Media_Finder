import { AppMobileNav } from "@/components/AppMobileNav";
import { AppPageHeader } from "@/components/AppPageHeader";
import { AppSidebar } from "@/components/AppSidebar";
import { DiscoveryWorkspace } from "@/components/DiscoveryWorkspace";

const outputColumns = [
  { label: "Talent Name", icon: "person" },
  { label: "Wikipedia URL", icon: "link" },
  { label: "Instagram", icon: "photo_camera" },
  { label: "Facebook", icon: "groups" },
  { label: "YouTube", icon: "smart_display" },
  { label: "TikTok", icon: "music_note" },
  { label: "X / Twitter", icon: "alternate_email" },
  { label: "+ Status (each)", icon: "verified" },
  { label: "+ Confidence (each)", icon: "percent" },
  { label: "+ Reason (each)", icon: "notes" },
];

const pipelineSteps = [
  { step: "01", text: "Build a rich Wikipedia/Wikidata ground-truth profile (no full page sent to the LLM).", icon: "menu_book" },
  { step: "02", text: "Apify Social Media Finder discovers Instagram, Facebook, YouTube, TikTok links.", icon: "hub" },
  { step: "03", text: "Serper extracts context for each link, and finds missing platforms (incl. X).", icon: "travel_explore" },
  { step: "04", text: "LLM verifies each profile: Verified / Wrong / Manual Review Needed.", icon: "neurology" },
  { step: "05", text: "Link + status + confidence + reason written to the XLSX export.", icon: "description" },
];

export default function DiscoveryPage() {
  return (
    <div className="relative flex min-h-screen flex-col bg-background md:flex-row">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_10%_0%,rgba(242,209,0,0.07),transparent_32%),radial-gradient(circle_at_90%_20%,rgba(56,189,248,0.06),transparent_28%)]"
      />

      <AppSidebar />

      <main className="relative z-10 min-h-screen flex-1 pb-24 md:ml-64 md:pb-0">
        <AppPageHeader
          title="Discovery"
          subtitle="Talent resolver"
          icon="dashboard"
          badge={
            <span className="absolute top-6 right-6 hidden rounded-full border border-primary/35 bg-primary/10 px-3 py-1 text-xs font-semibold text-primary sm:inline-flex">
              ListenFirst Workspace
            </span>
       
          }
        />

        <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
          <section className="lf-enter mb-10">
            <span className="inline-flex items-center gap-2 rounded-full border border-primary/25 bg-primary/10 px-3 py-1 text-xs font-bold uppercase tracking-widest text-primary">
              <span className="material-symbols-outlined text-sm">auto_awesome</span>
              AI social resolver
            </span>
            <h2 className="mt-4 text-4xl font-extrabold tracking-tight text-slate-50 md:text-5xl">
               <span className="block text-primary">Find Official Profiles</span>
            </h2>
            <p className="mt-4 max-w-2xl text-sm leading-relaxed text-slate-400 md:text-base">
              Upload a spreadsheet of talent names and Wikipedia URLs. ListenFirst pulls structured
              identity metadata, finds candidate profiles via Apify (Serper as fallback), verifies
              each with an LLM, and exports a status- and confidence-scored workbook.
            </p>
          </section>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            <div className="space-y-6 lg:col-span-2">
              <DiscoveryWorkspace />

              <div className="lf-enter lf-enter-delay-1 lf-card lf-card-hover p-6">
                <div className="mb-4 flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary">view_column</span>
                  <h3 className="text-lg font-bold text-slate-100">Expected Output Columns</h3>
                </div>
                <div className="flex flex-wrap gap-2">
                  {outputColumns.map((col) => (
                    <span key={col.label} className="lf-chip">
                      <span className="material-symbols-outlined text-sm text-primary/80">
                        {col.icon}
                      </span>
                      {col.label}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            <div className="space-y-6">
              <div className="lf-enter lf-enter-delay-2 lf-card lf-card-hover p-6">
                <div className="mb-4 flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary">account_tree</span>
                  <span className="text-xs font-bold uppercase tracking-widest text-primary">
                    Pipeline Logic
                  </span>
                </div>
                <ul className="space-y-3">
                  {pipelineSteps.map((item) => (
                    <li
                      key={item.step}
                      className="flex items-start gap-3 rounded-xl bg-slate-950/50 p-3 ring-1 ring-white/5"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xs font-extrabold text-primary ring-1 ring-primary/20">
                        {item.step}
                      </span>
                      <div>
                        <span className="material-symbols-outlined mb-1 text-base text-primary/80">
                          {item.icon}
                        </span>
                        <p className="text-sm leading-relaxed text-slate-300">{item.text}</p>
                      </div>
                    </li>
                  ))}
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
