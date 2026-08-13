"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { useToast } from "@/components/ToastProvider";
import { getPythonApiUrl, saveProcessingNames, setPythonJobId } from "@/lib/processing-job";

const excelColumns = [
  {
    name: "Talent Name",
    required: true,
    example: "Jake Thompson",
    note: "One person per row. Also accepts: Talent, Name, Title (or first column).",
  },
  {
    name: "Wikipedia URL",
    required: false,
    example: "https://en.wikipedia.org/wiki/Jake_Thompson",
    note: "Recommended. Structured identity metadata (profession, nationality, aliases, known works) is extracted from it to verify each profile.",
  },
] as const;

type DiscoveryMode = "wiki" | "non_wiki";

export function DiscoveryWorkspace() {
  const router = useRouter();
  const { pushToast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [mode, setMode] = useState<DiscoveryMode>("wiki");
  const [customQuery, setCustomQuery] = useState("");
  const [includeProfession, setIncludeProfession] = useState(true);

  const base = getPythonApiUrl();

  function openPicker() {
    inputRef.current?.click();
  }

  async function uploadFile(file: File) {
    if (!base) return;

    setLoading(true);
    setStatus(null);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("mode", mode);
    if (mode === "non_wiki") {
      fd.append("custom_query", customQuery.trim());
      fd.append("include_profession", includeProfession ? "true" : "false");
    }

    try {
      const res = await fetch(`${base}/api/upload`, {
        method: "POST",
        body: fd,
      });
      const payload = (await res.json().catch(() => ({}))) as {
        detail?: string | unknown;
        job_id?: string;
        names?: string[];
      };

      if (!res.ok) {
        let msg =
          typeof payload.detail === "string"
            ? payload.detail
            : `Upload failed (${res.status})`;
        if (res.status === 404) {
          msg =
            "Python API returned 404 — the running uvicorn process is outdated. In C:\\Testing stop the server (Ctrl+C), then start: uvicorn api_server:app --host 127.0.0.1 --port 8787 --reload";
        }
        if (res.status === 405) {
          msg =
            "405 Method Not Allowed — restart uvicorn from C:\\Testing with the latest api_server.py (use --reload).";
        }
        setStatus(msg);
        pushToast("Upload failed.", "error");
        setLoading(false);
        return;
      }

      if (!payload.job_id || !payload.names?.length) {
        setStatus("Invalid response from server.");
        pushToast("Invalid upload response.", "error");
        setLoading(false);
        return;
      }

      saveProcessingNames(payload.names);
      setPythonJobId(payload.job_id);
      setLoading(false);
      pushToast("File uploaded.", "success");
      router.push("/processing");
    } catch (err) {
      const detail = err instanceof Error ? err.message : String(err);
      setStatus(
        `Cannot reach ${base}: ${detail}. Run uvicorn from C:\\Testing (port 8787), set NEXT_PUBLIC_PYTHON_API_URL in .env.local, restart next dev.`,
      );
      pushToast("Cannot reach Python API.", "error");
      setLoading(false);
    }
  }

  async function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !base) return;
    await uploadFile(file);
  }

  return (
    <>
      <div
        className={`lf-enter lf-card lf-gradient-border relative overflow-hidden p-6 sm:p-8 transition ${
          dragOver ? "border-primary/35 bg-primary/5" : ""
        }`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void uploadFile(file);
        }}
      >
        <div className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-primary/10 blur-3xl" />
        <div className="absolute -left-12 -bottom-12 h-48 w-48 rounded-full bg-sky-400/10 blur-3xl" />
        <div className="relative z-10">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="inline-flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-primary">
              <span className="material-symbols-outlined text-lg">table_view</span>
              Excel input
            </div>
            <span className="rounded-full border border-white/10 bg-slate-950/70 px-3 py-1 text-xs font-bold text-slate-300">
              .xlsx / .csv
            </span>
          </div>

          <p className="mb-5 max-w-3xl text-sm leading-relaxed text-slate-400">
            Upload an Excel/CSV file. We’ll read the talent rows, resolve official profiles across
            platforms, and export a confidence-scored workbook.
          </p>

          {/* ── Mode selector: Wiki (verified pipeline) vs Non-Wiki (custom SerpApi query) ── */}
          <div className="mb-6">
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Discovery mode
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <button
                type="button"
                onClick={() => setMode("wiki")}
                aria-pressed={mode === "wiki"}
                className={`rounded-xl border p-4 text-left transition ${
                  mode === "wiki"
                    ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30"
                    : "border-white/8 bg-slate-950/50 hover:border-white/20"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary">menu_book</span>
                  <span className="text-sm font-bold text-slate-100">With Wikipedia</span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">
                  Full pipeline: Wikipedia/Wikidata ground truth → discover → LLM-verified
                  Verified / Wrong / Manual Review labels.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setMode("non_wiki")}
                aria-pressed={mode === "non_wiki"}
                className={`rounded-xl border p-4 text-left transition ${
                  mode === "non_wiki"
                    ? "border-primary/50 bg-primary/10 ring-1 ring-primary/30"
                    : "border-white/8 bg-slate-950/50 hover:border-white/20"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="material-symbols-outlined text-primary">travel_explore</span>
                  <span className="text-sm font-bold text-slate-100">Without Wikipedia</span>
                </div>
                <p className="mt-1.5 text-[11px] leading-relaxed text-slate-400">
                  No ground truth. Type a custom Google AI Mode query; every found link is
                  returned tagged Manual Review (no LLM).
                </p>
              </button>
            </div>

            {mode === "non_wiki" && (
              <div className="mt-3 rounded-xl border border-primary/20 bg-slate-950/60 p-4 ring-1 ring-white/5">
                <label
                  htmlFor="custom-query"
                  className="mb-1.5 block text-[11px] font-semibold uppercase tracking-wider text-slate-400"
                >
                  Custom query prompt
                </label>
                <input
                  id="custom-query"
                  type="text"
                  value={customQuery}
                  onChange={(e) => setCustomQuery(e.target.value)}
                  placeholder="e.g. social media handles"
                  className="w-full rounded-lg border border-white/10 bg-slate-900/80 px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-primary/50"
                />

                <label className="mt-3 flex cursor-pointer items-center gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={includeProfession}
                    onChange={(e) => setIncludeProfession(e.target.checked)}
                    className="h-4 w-4 accent-[var(--color-primary,#f2d100)]"
                  />
                  Include profession from Excel in the query
                </label>

                <p className="mt-3 text-[11px] leading-relaxed text-slate-500">
                  Query sent per talent:
                </p>
                <code className="mt-1 block break-words rounded-lg bg-slate-900/80 px-3 py-2 font-mono text-[11px] text-primary/90 ring-1 ring-white/5">
                  &lt;Name&gt;{includeProfession ? " <Profession>" : ""}
                  {customQuery.trim() ? ` ${customQuery.trim()}` : ""}
                </code>
                <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
                  <span className="font-semibold text-slate-400">Name</span> and{" "}
                  <span className="font-semibold text-slate-400">Profession</span> are pulled from
                  your Excel; only the prompt is yours to type.
                </p>
              </div>
            )}
          </div>

          <div className="mb-6 rounded-xl border border-white/8 bg-slate-950/50 p-4 ring-1 ring-white/5 sm:p-5">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="material-symbols-outlined text-base text-primary">info</span>
              <h4 className="text-sm font-bold text-slate-100">Spreadsheet format</h4>
              <span className="text-xs text-slate-500">Row 1 = headers · one talent per row</span>
            </div>

            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Example row
            </p>
            <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {excelColumns.map((col) => (
                <div
                  key={col.name}
                  className={`rounded-lg border border-white/6 bg-slate-950/80 px-3 py-2.5 ${
                    col.name === "Talent Name" || col.name === "Wikipedia URL"
                      ? "sm:col-span-2"
                      : ""
                  }`}
                >
                  <div className="mb-1.5 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <code className="font-mono text-[11px] font-semibold text-primary/90">
                      {col.name}
                    </code>
                    <span
                      className={`rounded-full px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${
                        col.required
                          ? "bg-primary/15 text-primary"
                          : "bg-slate-800 text-slate-500"
                      }`}
                    >
                      {col.required ? "required" : "optional"}
                    </span>
                  </div>
                  <p className="mb-1 break-words text-xs font-medium text-slate-200">
                    {col.example}
                  </p>
                  <p className="text-[11px] leading-relaxed text-slate-500">{col.note}</p>
                </div>
              ))}
            </div>

            <p className="mt-3 text-xs leading-relaxed text-slate-500">
              <span className="font-semibold text-slate-400">Tip:</span> a{" "}
              <code className="text-slate-400">Wikipedia URL</code> greatly improves accuracy — its
              structured metadata anchors identity so each candidate profile is verified against the
              right person. Without it, the pipeline falls back to a best-effort name search.
            </p>

            <div className="mt-4 flex flex-wrap gap-2 border-t border-white/6 pt-4">
              <span className="lf-chip text-[11px]">.xlsx</span>
              <span className="lf-chip text-[11px]">.xls</span>
              <span className="lf-chip text-[11px]">.csv</span>
              <span className="lf-chip text-[11px]">Max 25 MB</span>
              <span className="lf-chip text-[11px]">Empty rows skipped</span>
            </div>
          </div>

          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.xls,.csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv"
            className="hidden"
            onChange={(e) => void onFileChange(e)}
          />

          {!base ? (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
              <span className="material-symbols-outlined text-base">info</span>
              <p>
                Set <code className="text-amber-50/90">NEXT_PUBLIC_PYTHON_API_URL</code> in{" "}
                <code className="text-amber-50/90">.env.local</code> and start the FastAPI server to
                enable upload.
              </p>
            </div>
          ) : (
            <>
              <button
                type="button"
                disabled={loading}
                onClick={openPicker}
                className="lf-btn-secondary group flex w-full items-center justify-center gap-2 px-4 py-4 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                <span className="material-symbols-outlined text-lg transition-transform group-hover:-translate-y-0.5">
                  {loading ? "hourglass_top" : "cloud_upload"}
                </span>
                {loading ? "Uploading…" : "Choose Excel / CSV or drop file here"}
              </button>
              {status && (
                <p className="mt-3 flex items-start gap-2 text-sm text-rose-300" role="alert">
                  <span className="material-symbols-outlined text-base">error</span>
                  {status}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </>
  );
}
