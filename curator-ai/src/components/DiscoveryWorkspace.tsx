"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useToast } from "@/components/ToastProvider";
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
  const { pushToast } = useToast();
  const [text, setText] = useState(defaultNames);
  const [ignoreSingle, setIgnoreSingle] = useState(true);
  const [hint, setHint] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const nameCount = useMemo(
    () => parseNamesFromText(text, ignoreSingle).length,
    [text, ignoreSingle],
  );

  async function runDiscovery() {
    const names = parseNamesFromText(text, ignoreSingle);
    if (names.length === 0) {
      pushToast("Add at least one valid name.", "error");
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
          pushToast("Discovery start failed.", "error");
          setLoading(false);
          return;
        }
        if (!payload.job_id) {
          setHint("Invalid API response (missing job_id).");
          pushToast("Invalid API response.", "error");
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
        pushToast("Python API unreachable.", "error");
        setLoading(false);
        return;
      }
      setLoading(false);
    } else {
      setPythonJobId(null);
      saveProcessingNames(names);
    }

    pushToast("Discovery started.", "success");
    router.push("/processing");
  }

  return (
    <>
      <div className="lf-enter lf-card lf-gradient-border p-6 sm:p-8">
        <div className="relative z-10">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <label
              className="inline-flex items-center gap-2 text-sm font-bold uppercase tracking-widest text-primary"
              htmlFor="brand-names"
            >
              <span className="material-symbols-outlined text-lg">groups</span>
              Target Entities
            </label>
            <span className="rounded-full border border-white/10 bg-slate-950/70 px-3 py-1 text-xs font-bold text-slate-300">
              {nameCount} name{nameCount === 1 ? "" : "s"} ready
            </span>
          </div>
          <textarea
            id="brand-names"
            rows={10}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="One talent name per line…"
            className="w-full resize-none rounded-xl border border-white/8 bg-slate-950/70 p-5 font-medium text-slate-100 placeholder:text-slate-500 transition focus:border-primary/30 focus:outline-none focus:ring-2 focus:ring-primary/25"
          />
          <div className="mt-6 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3 rounded-xl bg-slate-950/60 px-4 py-2.5 ring-1 ring-white/8">
              <span className="material-symbols-outlined text-base text-slate-500">tune</span>
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
              className="lf-btn-primary inline-flex items-center gap-2 px-8 py-3 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <span className="material-symbols-outlined text-xl">
                {loading ? "hourglass_top" : "rocket_launch"}
              </span>
              <span>{loading ? "Starting…" : "Run Discovery"}</span>
            </button>
          </div>
        </div>
      </div>

      {hint && (
        <div
          className="lf-enter mt-4 flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100"
          role="status"
        >
          <span className="material-symbols-outlined text-base">warning</span>
          {hint}
        </div>
      )}
    </>
  );
}
