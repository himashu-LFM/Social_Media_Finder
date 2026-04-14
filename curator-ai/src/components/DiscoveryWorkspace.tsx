"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import {
  getPythonApiUrl,
  parseNamesFromText,
  saveProcessingNames,
  setPythonJobId,
} from "@/lib/processing-job";

const defaultNames = `Adam Grissom
Akai Fleming
BJ Powell
ESPN`;

export function DiscoveryWorkspace() {
  const router = useRouter();
  const [text, setText] = useState(defaultNames);
  const [ignoreSingle, setIgnoreSingle] = useState(true);
  const [hint, setHint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function runDiscovery() {
    const names = parseNamesFromText(text, ignoreSingle);
    if (names.length === 0) {
      setHint(
        "Add at least one name line, or turn off “ignore single-name” if each line is a single word.",
      );
      return;
    }
    setHint(null);

    const base = getPythonApiUrl();
    if (base) {
      setLoading(true);
      try {
        const res = await fetch(`${base}/api/jobs`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ names }),
        });
        const payload = (await res.json().catch(() => ({}))) as {
          detail?: string | { msg?: string }[];
          job_id?: string;
        };
        if (!res.ok) {
          const msg =
            typeof payload.detail === "string"
              ? payload.detail
              : Array.isArray(payload.detail)
                ? JSON.stringify(payload.detail)
                : `Request failed (${res.status})`;
          setHint(msg);
          setLoading(false);
          return;
        }
        if (!payload.job_id) {
          setHint("Invalid API response (missing job_id).");
          setLoading(false);
          return;
        }
        saveProcessingNames(names);
        setPythonJobId(payload.job_id);
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        setHint(
          `Cannot reach ${base} (${detail}). From C:\\Testing run: uvicorn api_server:app --host 127.0.0.1 --port 8787 — add NEXT_PUBLIC_PYTHON_API_URL=http://127.0.0.1:8787 to curator-ai/.env.local and restart npm run dev.`,
        );
        setLoading(false);
        return;
      }
      setLoading(false);
    } else {
      setPythonJobId(null);
      saveProcessingNames(names);
    }

    router.push("/processing");
  }

  return (
    <>
      <div className="rounded-2xl bg-surface p-8 ring-1 ring-white/5 shadow-[0_20px_40px_-10px_rgba(0,0,0,0.35)]">
        <label
          className="mb-4 block text-sm font-bold uppercase tracking-widest text-primary"
          htmlFor="brand-names"
        >
          Target Entities
        </label>
        <textarea
          id="brand-names"
          rows={10}
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="w-full resize-none rounded-xl border border-white/5 bg-surface-high/90 p-6 font-medium text-slate-100 placeholder:text-slate-500 focus:border-transparent focus:outline-none focus:ring-2 focus:ring-primary/30"
        />
        <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3 rounded-xl bg-surface-high/80 px-4 py-2.5 ring-1 ring-white/5">
            <span className="text-xs font-semibold text-slate-400">
              Ignore single-name entries
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={ignoreSingle}
              onClick={() => setIgnoreSingle((v) => !v)}
              className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/50 ${
                ignoreSingle ? "bg-primary" : "bg-slate-600"
              }`}
            >
              <span
                aria-hidden
                className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ${
                  ignoreSingle ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
          <button
            type="button"
            disabled={loading}
            onClick={() => void runDiscovery()}
            className="flex items-center gap-2 rounded-xl bg-gradient-to-r from-primary-dim to-primary px-8 py-3 font-bold text-white shadow-lg shadow-indigo-500/20 transition hover:scale-[1.02] active:scale-95 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <span>{loading ? "Starting…" : "Run Discovery"}</span>
            <span className="material-symbols-outlined text-xl">arrow_forward</span>
          </button>
        </div>
      </div>

      {hint && (
        <p className="mt-4 text-sm text-amber-400" role="status">
          {hint}
        </p>
      )}
      {/* <p className="mt-4 text-xs text-slate-500">
        With <code className="text-slate-400">NEXT_PUBLIC_PYTHON_API_URL</code> set, discovery runs on
        the FastAPI backend in <code className="text-slate-400">C:\Testing</code> (heavy work stays in
        Python). Otherwise Processing uses a short UI preview only. Then open Results for the exported
        sheet.
      </p> */}
    </>
  );
}
