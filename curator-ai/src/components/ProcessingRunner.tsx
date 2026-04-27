"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useToast } from "@/components/ToastProvider";
import {
  getPythonApiUrl,
  markProcessingRunFinished,
  readProcessingNames,
  readPythonJobId,
} from "@/lib/processing-job";

type RowStatus = "queued" | "processing" | "done";

type RunSource = "idle" | "python" | "demo";

const PIPELINE_STEPS = [
  "Serper search",
  "Profile URL filter",
  "AI selection",
  "Confidence + source",
] as const;

function delay(ms: number) {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

type JobPollPayload = {
  status: string;
  names: { name: string; status: RowStatus }[];
  error?: string | null;
};

export function ProcessingRunner() {
  const { pushToast } = useToast();
  const [mounted, setMounted] = useState(false);
  const [names, setNames] = useState<string[] | null>(null);
  const [statuses, setStatuses] = useState<RowStatus[]>([]);
  const [source, setSource] = useState<RunSource>("idle");
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [backendError, setBackendError] = useState<string | null>(null);
  const cancelledRef = useRef(false);
  const completionMarkedRef = useRef(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const doneCount = useMemo(
    () => statuses.filter((s) => s === "done").length,
    [statuses],
  );
  const total = names?.length ?? 0;
  const allDone = total > 0 && doneCount === total;
  const currentNameIndex = statuses.findIndex((s) => s === "processing");

  useEffect(() => {
    setMounted(true);
    const list = readProcessingNames();
    const api = getPythonApiUrl();
    const jid = readPythonJobId();
    if (list && list.length > 0) {
      setNames(list);
      setStatuses(list.map(() => "queued"));
      if (api && jid) {
        setSource("python");
      } else {
        setSource("demo");
      }
    } else {
      setNames([]);
      setSource("idle");
    }
  }, []);

  useEffect(() => {
    if (source !== "demo" || !names || names.length === 0) return;

    cancelledRef.current = false;
    completionMarkedRef.current = false;
    const list = names;

    async function runPipeline() {
      for (let idx = 0; idx < list.length; idx++) {
        if (cancelledRef.current) return;
        setStatuses(() =>
          list.map((_, i) => {
            if (i < idx) return "done";
            if (i === idx) return "processing";
            return "queued";
          }),
        );
        const perNameMs = 1600 + Math.floor(Math.random() * 900);
        await delay(perNameMs);
      }
      if (cancelledRef.current) return;
      setStatuses(list.map(() => "done"));
      if (!completionMarkedRef.current) {
        completionMarkedRef.current = true;
        markProcessingRunFinished();
      }
    }

    void runPipeline();

    return () => {
      cancelledRef.current = true;
    };
  }, [source, names]);

  useEffect(() => {
    if (source !== "python" || !names || names.length === 0) return;

    const api = getPythonApiUrl();
    const jid = readPythonJobId();
    if (!api || !jid) return;

    setBackendError(null);

    async function pollOnce() {
      try {
        const res = await fetch(`${api}/api/jobs/${jid}`);
        if (res.status === 404) {
          setBackendError(
            "Job not found (Python API may have restarted). Run Discovery again.",
          );
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
          }
          return;
        }
        if (!res.ok) {
          setBackendError(`Job status error (${res.status})`);
          return;
        }
        const data = (await res.json()) as JobPollPayload;
        const next = data.names.map((n) => n.status);
        setStatuses(next);

        if (data.status === "completed") {
          if (!completionMarkedRef.current) {
            completionMarkedRef.current = true;
            markProcessingRunFinished();
            pushToast("Processing completed.", "success");
          }
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
          }
        }
        if (data.status === "failed") {
          setBackendError(data.error || "Pipeline failed.");
          pushToast("Pipeline failed.", "error");
          if (pollTimerRef.current) {
            clearInterval(pollTimerRef.current);
            pollTimerRef.current = null;
          }
        }
      } catch {
        setBackendError("Cannot reach the Python API.");
        pushToast("Python API disconnected.", "error");
        if (pollTimerRef.current) {
          clearInterval(pollTimerRef.current);
          pollTimerRef.current = null;
        }
      }
    }

    void pollOnce();
    pollTimerRef.current = setInterval(() => void pollOnce(), 1200);

    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [source, names, pushToast]);

  useEffect(() => {
    if (allDone || total === 0) return;
    const id = setInterval(() => {
      setCurrentStepIndex((i) => (i + 1) % PIPELINE_STEPS.length);
    }, 500);
    return () => clearInterval(id);
  }, [allDone, total]);

  if (!mounted) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center px-6">
        <div
          className="h-10 w-10 animate-spin rounded-full border-2 border-primary border-t-transparent"
          aria-hidden
        />
      </div>
    );
  }

  if (names === null) {
    return null;
  }

  if (names.length === 0) {
    return (
      <div className="mx-auto w-full max-w-2xl rounded-2xl border border-white/10 bg-slate-900/80 p-10 text-center ring-1 ring-white/5">
        <span className="material-symbols-outlined mb-4 text-5xl text-slate-500">folder_open</span>
        <h2 className="text-xl font-bold text-slate-100">No names to process</h2>
        <p className="mt-3 text-sm leading-relaxed text-slate-400">
          Go to Discovery, enter talent names (or upload a file when that is wired), then choose{" "}
          <strong className="text-slate-300">Run Discovery</strong>. You will land here while each
          name is searched and scored in the background.
        </p>
        <Link
          href="/"
          className="mt-8 inline-flex cursor-pointer items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-bold text-white shadow-lg shadow-indigo-500/20 transition hover:shadow-indigo-500/40"
        >
          <span className="material-symbols-outlined text-lg">arrow_back</span>
          Back to Discovery
        </Link>
      </div>
    );
  }

  const pct = total ? Math.round((doneCount / total) * 100) : 0;
  const activeName =
    currentNameIndex >= 0 ? names[currentNameIndex] : names[names.length - 1];

  return (
    <div className="mx-auto w-full max-w-4xl space-y-8 px-6 py-10">
      {backendError && (
        <div
          className="rounded-xl border border-rose-500/40 bg-rose-950/40 px-4 py-3 text-sm text-rose-200"
          role="alert"
        >
          {backendError}
        </div>
      )}

      <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-slate-900/80 p-8 ring-1 ring-white/5 md:p-10">
        <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
        <div className="absolute -bottom-24 -left-24 h-64 w-64 rounded-full bg-violet-500/10 blur-3xl" />

        <div className="relative z-10">
          <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-xs font-bold uppercase tracking-widest text-primary">
                {source === "python" ? "Python backend" : "Preview mode"}
              </p>
              <h2 className="mt-1 text-2xl font-extrabold tracking-tight text-slate-50 md:text-3xl">
                Name-by-name discovery
              </h2>
              <p className="mt-2 max-w-xl text-sm text-slate-400">
                {source === "python" ? (
                  <>
                    Status comes from the FastAPI service in{" "}
                    <code className="text-slate-500">C:\Testing</code> — Serper, URL filtering, and
                    scoring run in Python (not in Node).
                  </>
                ) : (
                  <>
                    Connect <code className="text-slate-500">NEXT_PUBLIC_PYTHON_API_URL</code> and
                    start <code className="text-slate-500">uvicorn</code> for live jobs. This preview
                    animates progress locally only.
                  </>
                )}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2">
              {allDone ? (
                <Link
                  href="/results"
                  className="inline-flex cursor-pointer items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-white shadow-lg shadow-indigo-500/25 transition hover:shadow-indigo-500/45"
                >
                  View Results
                  <span className="material-symbols-outlined text-lg">table_chart</span>
                </Link>
              ) : (
                <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-xs font-semibold text-amber-200">
                  {doneCount} / {total} complete
                </span>
              )}
            </div>
          </div>

          <div className="mb-6">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="font-semibold text-slate-300">
                {allDone ? (
                  "All names processed"
                ) : (
                  <>
                    Working on: <span className="text-primary">{activeName}</span>
                  </>
                )}
              </span>
              <span className="font-bold text-slate-400">{pct}%</span>
            </div>
            <div className="relative h-3 w-full overflow-hidden rounded-full bg-slate-800">
              <div
                className="relative h-full rounded-full bg-gradient-to-r from-primary-dim to-primary transition-all duration-500 ease-out"
                style={{ width: `${pct}%` }}
              >
                <div className="progress-shimmer absolute inset-0 rounded-full" />
              </div>
            </div>
          </div>

          <div className="mb-8 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {PIPELINE_STEPS.map((label, i) => (
              <div
                key={label}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 ring-1 ring-white/10 ${
                  !allDone && i === currentStepIndex % PIPELINE_STEPS.length
                    ? "bg-primary/15 ring-primary/30"
                    : "bg-slate-950/60"
                }`}
              >
                <span
                  className={`material-symbols-outlined text-sm ${
                    allDone
                      ? "text-emerald-400"
                      : i === currentStepIndex % PIPELINE_STEPS.length
                        ? "text-primary"
                        : "text-slate-500"
                  }`}
                >
                  {allDone
                    ? "check_circle"
                    : i === currentStepIndex % PIPELINE_STEPS.length
                      ? "progress_activity"
                      : "radio_button_unchecked"}
                </span>
                <span className="text-xs font-semibold text-slate-300">{label}</span>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-white/10 bg-slate-950/50">
            <div className="border-b border-white/10 px-4 py-3">
              <span className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Talent queue
              </span>
            </div>
            <ul className="max-h-[min(420px,50vh)] divide-y divide-white/10 overflow-y-auto">
              {names.map((name, i) => {
                const s = statuses[i] ?? "queued";
                return (
                  <li
                    key={`${name}-${i}`}
                    className="flex items-center justify-between gap-3 px-4 py-3"
                  >
                    <span className="min-w-0 flex-1 truncate font-medium text-slate-200">
                      {name}
                    </span>
                    <StatusBadge status={s} />
                  </li>
                );
              })}
            </ul>
          </div>
        </div>
      </div>

      <p className="text-center text-xs uppercase tracking-[0.15em] text-slate-500">
        Export file: Talent_Social_Lookup_*.xlsx in C:\Testing — Results page reads the newest
        workbook
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: RowStatus }) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-bold text-emerald-300 ring-1 ring-emerald-500/30">
        <span className="material-symbols-outlined text-sm">check</span>
        Done
      </span>
    );
  }
  if (status === "processing") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2.5 py-1 text-xs font-bold text-primary ring-1 ring-primary/30">
        <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
        Processing
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-800/80 px-2.5 py-1 text-xs font-semibold text-slate-500 ring-1 ring-white/10">
      Queued
    </span>
  );
}
