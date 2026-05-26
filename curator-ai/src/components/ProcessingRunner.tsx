"use client";

import type { CSSProperties } from "react";
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

type RowPlatformProgress = {
  currentPlatform: string | null;
  completedPlatforms: string[];
};

type RunSource = "idle" | "python" | "demo";

const PIPELINE_STEPS = [
  { label: "Searching social platforms", icon: "travel_explore", detail: "Serper + platform queries" },
  { label: "Filtering profile URLs", icon: "filter_alt", detail: "Posts and reels removed" },
  { label: "AI identity check", icon: "neurology", detail: "Match scoring per name" },
  { label: "Confidence + export", icon: "fact_check", detail: "Workbook assembly" },
] as const;

const SOCIAL_PLATFORM_STEPS = [
  { label: "Facebook", short: "FB", icon: "groups", color: "from-blue-500/20 to-blue-600/5" },
  { label: "Instagram", short: "IG", icon: "photo_camera", color: "from-pink-500/20 to-purple-600/5" },
  { label: "X", short: "X", icon: "alternate_email", color: "from-slate-400/20 to-slate-600/5" },
  { label: "TikTok", short: "TT", icon: "music_note", color: "from-cyan-400/20 to-teal-600/5" },
  { label: "YouTube", short: "YT", icon: "smart_display", color: "from-red-500/20 to-red-700/5" },
] as const;

const LIVE_MESSAGES = [
  "The backend is still working. You can leave this page open while it checks each profile.",
  "Longer runs are normal when names need extra verification or multiple platform searches.",
  "The queue updates as soon as Python starts a new row or finishes one.",
  "Results will appear automatically once the export workbook is ready.",
] as const;

function delay(ms: number) {
  return new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });
}

type JobPollPayload = {
  status: string;
  names: {
    name: string;
    status: RowStatus;
    current_platform?: string | null;
    completed_platforms?: string[];
  }[];
  error?: string | null;
};

const EMPTY_PLATFORM_PROGRESS: RowPlatformProgress = {
  currentPlatform: null,
  completedPlatforms: [],
};

function platformProgressFromEntry(entry: JobPollPayload["names"][number]): RowPlatformProgress {
  return {
    currentPlatform: entry.current_platform ?? null,
    completedPlatforms: entry.completed_platforms ?? [],
  };
}

function getPlatformState(
  platformLabel: string,
  rowStatus: RowStatus,
  progress: RowPlatformProgress,
): "done" | "active" | "queued" {
  if (rowStatus === "done") return "done";
  if (rowStatus === "queued") return "queued";

  const { currentPlatform, completedPlatforms } = progress;
  if (completedPlatforms.includes(platformLabel)) return "done";
  if (currentPlatform === platformLabel) return "active";

  const order = SOCIAL_PLATFORM_STEPS.map((step) => step.label);
  const nextIndex = order.findIndex((label) => !completedPlatforms.includes(label));
  if (nextIndex >= 0 && order[nextIndex] === platformLabel && !currentPlatform) {
    return "active";
  }

  return "queued";
}

const RING_RADIUS = 88;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

export function ProcessingRunner() {
  const { pushToast } = useToast();
  const [mounted, setMounted] = useState(false);
  const [names, setNames] = useState<string[] | null>(null);
  const [statuses, setStatuses] = useState<RowStatus[]>([]);
  const [rowPlatformProgress, setRowPlatformProgress] = useState<RowPlatformProgress[]>([]);
  const [source, setSource] = useState<RunSource>("idle");
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [backendError, setBackendError] = useState<string | null>(null);
  const cancelledRef = useRef(false);
  const completionMarkedRef = useRef(false);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const previousDoneCountRef = useRef(0);
  const previousActiveIndexRef = useRef(-1);

  const doneCount = useMemo(
    () => statuses.filter((s) => s === "done").length,
    [statuses],
  );
  const total = names?.length ?? 0;
  const allDone = total > 0 && doneCount === total;
  const currentNameIndex = statuses.findIndex((s) => s === "processing");
  const remainingCount = Math.max(0, total - doneCount);

  useEffect(() => {
    setMounted(true);
    const list = readProcessingNames();
    const api = getPythonApiUrl();
    const jid = readPythonJobId();
    if (list && list.length > 0) {
      setNames(list);
      setStatuses(list.map(() => "queued"));
      setRowPlatformProgress(list.map(() => ({ ...EMPTY_PLATFORM_PROGRESS })));
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
        setRowPlatformProgress(() =>
          list.map((_, i) => {
            if (i < idx) {
              return {
                currentPlatform: null,
                completedPlatforms: SOCIAL_PLATFORM_STEPS.map((step) => step.label),
              };
            }
            if (i === idx) {
              return { ...EMPTY_PLATFORM_PROGRESS };
            }
            return { ...EMPTY_PLATFORM_PROGRESS };
          }),
        );

        for (const platform of SOCIAL_PLATFORM_STEPS) {
          if (cancelledRef.current) return;
          setRowPlatformProgress((prev) =>
            prev.map((entry, i) =>
              i === idx
                ? { ...entry, currentPlatform: platform.label }
                : entry,
            ),
          );
          await delay(650 + Math.floor(Math.random() * 450));
          if (cancelledRef.current) return;
          setRowPlatformProgress((prev) =>
            prev.map((entry, i) =>
              i === idx
                ? {
                    currentPlatform: null,
                    completedPlatforms: entry.completedPlatforms.includes(platform.label)
                      ? entry.completedPlatforms
                      : [...entry.completedPlatforms, platform.label],
                  }
                : entry,
            ),
          );
        }
      }
      if (cancelledRef.current) return;
      setStatuses(list.map(() => "done"));
      setRowPlatformProgress(
        list.map(() => ({
          currentPlatform: null,
          completedPlatforms: SOCIAL_PLATFORM_STEPS.map((step) => step.label),
        })),
      );
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
        const nextPlatformProgress = data.names.map((entry) => platformProgressFromEntry(entry));
        const nextDoneCount = next.filter((s) => s === "done").length;
        const nextActiveIndex = next.findIndex((s) => s === "processing");
        if (
          nextDoneCount !== previousDoneCountRef.current ||
          nextActiveIndex !== previousActiveIndexRef.current
        ) {
          previousDoneCountRef.current = nextDoneCount;
          previousActiveIndexRef.current = nextActiveIndex;
        }
        setStatuses(next);
        setRowPlatformProgress(nextPlatformProgress);

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
      setCurrentStepIndex((i) => i + 1);
    }, 500);
    return () => clearInterval(id);
  }, [allDone, total]);

  if (!mounted) {
    return (
      <div className="flex min-h-[50vh] flex-col items-center justify-center gap-5 px-6">
        <div className="relative flex h-20 w-20 items-center justify-center">
          <div className="absolute inset-0 animate-spin rounded-full border-2 border-primary/20 border-t-primary" />
          <div
            className="absolute inset-2 animate-spin rounded-full border-2 border-primary/10 border-b-primary/60"
            style={{ animationDirection: "reverse", animationDuration: "1.4s" }}
          />
          <span className="material-symbols-outlined text-2xl text-primary ai-pulse">radar</span>
        </div>
        <p className="text-sm font-semibold text-slate-400">Loading pipeline status…</p>
      </div>
    );
  }

  if (names === null) {
    return null;
  }

  if (names.length === 0) {
    return (
      <div className="proc-enter mx-auto w-full max-w-2xl px-4 py-10">
        <div className="relative overflow-hidden rounded-[2rem] border border-white/10 bg-slate-900/80 p-10 text-center shadow-2xl shadow-black/40 ring-1 ring-white/5">
          <div className="absolute -left-16 -top-16 h-48 w-48 rounded-full bg-primary/10 blur-3xl" />
          <div className="absolute -bottom-20 -right-10 h-56 w-56 rounded-full bg-violet-500/10 blur-3xl" />
          <div className="relative z-10">
            <div className="proc-float mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-2xl bg-slate-950/70 ring-1 ring-primary/20 shadow-xl shadow-primary/10">
              <span className="material-symbols-outlined text-5xl text-slate-500">folder_open</span>
            </div>
            <h2 className="text-2xl font-extrabold text-slate-100">No names to process</h2>
            <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-slate-400">
              Go to Discovery, enter talent names (or upload a file when that is wired), then choose{" "}
              <strong className="text-primary">Run Discovery</strong>. You will land here while each
              name is searched and scored in the background.
            </p>
            <Link
              href="/discovery"
              className="proc-btn-glow mt-10 inline-flex cursor-pointer items-center gap-2 rounded-xl bg-primary px-7 py-3.5 text-sm font-bold text-slate-950 shadow-lg shadow-primary/25"
            >
              <span className="material-symbols-outlined text-lg">arrow_back</span>
              Back to Discovery
            </Link>
          </div>
        </div>
      </div>
    );
  }

  const pct = total ? Math.round((doneCount / total) * 100) : 0;
  const ringOffset = RING_CIRCUMFERENCE - (pct / 100) * RING_CIRCUMFERENCE;
  const activeName =
    currentNameIndex >= 0 ? names[currentNameIndex] : names[names.length - 1];
  const liveMessage = LIVE_MESSAGES[currentStepIndex % LIVE_MESSAGES.length];
  const isWaitingForFirstRow = !allDone && doneCount === 0 && currentNameIndex < 0;
  const statusLabel = allDone ? "Completed" : backendError ? "Needs attention" : "Live processing";
  const statusTone = backendError
    ? "border-rose-400/30 bg-rose-500/10 text-rose-200"
    : allDone
      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
      : "border-primary/30 bg-primary/10 text-primary";
  const statusDot = backendError ? "bg-rose-300" : allDone ? "bg-emerald-300" : "bg-primary";
  const activeStep = currentStepIndex % PIPELINE_STEPS.length;

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 px-4 py-6 sm:px-6 lg:py-8">
      {backendError && (
        <div
          className="proc-enter flex items-start gap-3 rounded-2xl border border-rose-500/40 bg-rose-950/50 px-4 py-3 text-sm text-rose-100 shadow-xl shadow-rose-950/20 ring-1 ring-white/5"
          role="alert"
        >
          <span className="material-symbols-outlined mt-0.5 animate-pulse text-rose-300">error</span>
          <div>
            <p className="font-bold">Processing connection needs attention</p>
            <p className="mt-1 text-rose-200/85">{backendError}</p>
          </div>
        </div>
      )}

      <section className="grid gap-5 xl:grid-cols-[1.25fr_0.95fr]">
        <div
          className={`proc-enter proc-enter-delay-1 relative overflow-hidden rounded-[2rem] border border-primary/25 bg-[radial-gradient(circle_at_20%_20%,rgba(242,209,0,0.16),transparent_28%),linear-gradient(135deg,rgba(15,23,42,0.96),rgba(2,6,23,0.92))] p-5 shadow-2xl shadow-black/40 ring-1 ring-white/10 sm:p-6 lg:p-8 ${allDone ? "proc-glow-card" : ""}`}
        >
          <div className="proc-border-animated absolute inset-x-8 top-0 h-px opacity-60" />
          <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
          <div className="absolute -bottom-24 left-1/3 h-56 w-56 rounded-full bg-sky-500/10 blur-3xl" />

          {allDone && (
            <>
              <span className="proc-spark absolute right-8 top-8 material-symbols-outlined text-primary/70">
                auto_awesome
              </span>
              <span
                className="proc-spark absolute right-16 top-20 material-symbols-outlined text-emerald-300/80"
                style={{ animationDelay: "0.6s" }}
              >
                celebration
              </span>
            </>
          )}

          <div className="relative z-10 flex flex-col gap-7 lg:flex-row lg:items-center">
            <div className="relative mx-auto flex h-48 w-48 shrink-0 items-center justify-center sm:h-52 sm:w-52 lg:mx-0">
              <svg
                className="absolute inset-0 h-full w-full -rotate-90"
                viewBox="0 0 200 200"
                aria-hidden
              >
                <defs>
                  <linearGradient id="proc-ring-gradient" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="#f2d100" />
                    <stop offset="100%" stopColor="#d5b700" />
                  </linearGradient>
                </defs>
                <circle className="proc-ring-track" cx="100" cy="100" r={RING_RADIUS} />
                <circle
                  className="proc-ring-progress"
                  cx="100"
                  cy="100"
                  r={RING_RADIUS}
                  strokeDasharray={RING_CIRCUMFERENCE}
                  strokeDashoffset={ringOffset}
                />
              </svg>

              {!allDone && (
                <>
                  <div className="proc-scan-beam absolute inset-3 rounded-full" />
                  <div
                    className="proc-orbit-dot"
                    style={{ "--orbit-radius": "78px", "--orbit-duration": "7s" } as CSSProperties}
                  />
                  <div
                    className="proc-orbit-dot"
                    style={
                      {
                        "--orbit-radius": "62px",
                        "--orbit-duration": "5s",
                        animationDirection: "reverse",
                      } as CSSProperties
                    }
                  />
                  <div
                    className="proc-orbit-dot h-1.5 w-1.5 opacity-60"
                    style={{ "--orbit-radius": "92px", "--orbit-duration": "11s" } as CSSProperties}
                  />
                </>
              )}

              <div className="absolute inset-8 rounded-full border border-primary/15 bg-slate-950/50 shadow-inner shadow-black/40" />
              <div className="relative flex flex-col items-center gap-1">
                <span
                  className={`material-symbols-outlined text-4xl text-primary sm:text-5xl ${allDone ? "proc-celebrate" : "ai-pulse"}`}
                >
                  {allDone ? "check_circle" : "radar"}
                </span>
                <span className="text-2xl font-extrabold tabular-nums text-slate-50">{pct}%</span>
              </div>
            </div>

            <div className="min-w-0 flex-1">
              <div
                className={`mb-4 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-bold shadow-lg ${statusTone}`}
              >
                <span className={`h-2 w-2 rounded-full ${statusDot} ${allDone ? "" : "animate-pulse"}`} />
                {statusLabel}
              </div>
              <h2
                className={`max-w-xl text-3xl font-extrabold tracking-tight text-slate-50 sm:text-4xl ${allDone ? "proc-celebrate" : ""}`}
              >
                {allDone
                  ? "Your export is ready"
                  : backendError
                    ? "Processing is paused"
                    : "We are still searching"}
              </h2>
              <p
                key={liveMessage}
                className="proc-message-swap mt-3 max-w-2xl text-sm leading-6 text-slate-300 sm:text-base"
              >
                {allDone
                  ? "All names have been processed. Open Results to review confidence scores and social links."
                  : backendError
                    ? "The job status could not be found. If the Python server restarted, start Discovery again to create a fresh job."
                    : isWaitingForFirstRow
                      ? "The Python job has started and is warming up the first row. This can take a moment on larger uploads."
                      : liveMessage}
              </p>

              {!allDone && !backendError && (
                <div className="mt-5 flex flex-wrap gap-2">
                  {["Serper", "Filter", "AI score", "Export"].map((chip, i) => (
                    <span
                      key={chip}
                      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-[11px] font-bold ring-1 transition-all duration-300 ${
                        i === activeStep
                          ? "bg-primary/15 text-primary ring-primary/30 shadow-md shadow-primary/10"
                          : "bg-slate-950/60 text-slate-500 ring-white/10"
                      }`}
                    >
                      <span className="material-symbols-outlined text-sm">
                        {PIPELINE_STEPS[i]?.icon}
                      </span>
                      {chip}
                    </span>
                  ))}
                </div>
              )}

              {allDone && (
                <Link
                  href="/results"
                  className="proc-btn-glow mt-6 inline-flex items-center gap-2 rounded-xl bg-primary px-6 py-3 text-sm font-bold text-slate-950 shadow-lg shadow-primary/30"
                >
                  <span className="material-symbols-outlined text-lg">table_chart</span>
                  Open Results
                  <span className="material-symbols-outlined text-lg">arrow_forward</span>
                </Link>
              )}
            </div>
          </div>
        </div>

        <aside className="proc-enter proc-enter-delay-2 proc-card-hover rounded-[2rem] border border-white/10 bg-slate-900/80 p-5 shadow-2xl shadow-black/25 ring-1 ring-white/5 sm:p-6">
          <RunProgressPanel
            activeName={allDone ? "Finished" : activeName}
            doneCount={doneCount}
            remainingCount={remainingCount}
            total={total}
            workbookStatus={allDone ? "Ready to view" : "Building export"}
            pct={pct}
            allDone={allDone}
          />
        </aside>
      </section>

      <div className="proc-enter proc-enter-delay-3 relative overflow-hidden rounded-[2rem] border border-white/10 bg-slate-900/80 p-6 shadow-2xl shadow-black/30 ring-1 ring-white/5 md:p-10">
        <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-primary/10 blur-3xl" />
        <div className="absolute -bottom-24 -left-24 h-64 w-64 rounded-full bg-violet-500/10 blur-3xl" />

        <div className="relative z-10">
          <div className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-primary">
                <span className="material-symbols-outlined text-sm">
                  {source === "python" ? "terminal" : "preview"}
                </span>
                {source === "python" ? "Python backend" : "Preview mode"}
              </p>
              <h2 className="mt-2 text-2xl font-extrabold tracking-tight text-slate-50 md:text-3xl">
                Name-by-name discovery
              </h2>
              <p className="mt-2 max-w-xl text-sm text-slate-400">
                {source === "python" ? (
                  <>
                    Status comes from the FastAPI service in{" "}
                    <code className="rounded bg-slate-950/80 px-1.5 py-0.5 text-slate-500 ring-1 ring-white/10">
                      C:\Testing
                    </code>{" "}
                    — Serper, URL filtering, and scoring run in Python.
                  </>
                ) : (
                  <>
                    Connect{" "}
                    <code className="rounded bg-slate-950/80 px-1.5 py-0.5 text-slate-500 ring-1 ring-white/10">
                      NEXT_PUBLIC_PYTHON_API_URL
                    </code>{" "}
                    and start{" "}
                    <code className="rounded bg-slate-950/80 px-1.5 py-0.5 text-slate-500 ring-1 ring-white/10">
                      uvicorn
                    </code>{" "}
                    for live jobs.
                  </>
                )}
              </p>
            </div>
            <div className="flex shrink-0 flex-col items-end gap-2">
              {allDone ? (
                <Link
                  href="/results"
                  className="proc-btn-glow inline-flex cursor-pointer items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-slate-950 shadow-lg shadow-primary/25"
                >
                  View Results
                  <span className="material-symbols-outlined text-lg">table_chart</span>
                </Link>
              ) : (
                <span className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-4 py-1.5 text-xs font-semibold text-amber-200 shadow-lg shadow-amber-950/20">
                  <span className="material-symbols-outlined text-sm">hourglass_top</span>
                  {doneCount} / {total} complete
                </span>
              )}
            </div>
          </div>

          <div className="mb-8">
            <div className="mb-3 flex items-center justify-between text-sm">
              <span className="inline-flex items-center gap-2 font-semibold text-slate-300">
                <span className="material-symbols-outlined text-base text-primary">
                  {allDone ? "task_alt" : "person_search"}
                </span>
                {allDone ? (
                  "All names processed"
                ) : (
                  <>
                    Working on: <span className="text-primary">{activeName}</span>
                  </>
                )}
              </span>
              <span className="rounded-full bg-slate-950/70 px-2.5 py-0.5 text-xs font-extrabold tabular-nums text-primary ring-1 ring-primary/20">
                {pct}%
              </span>
            </div>
            <div className="relative h-3.5 w-full overflow-hidden rounded-full bg-slate-800/90 ring-1 ring-white/5">
              <div
                className="relative h-full rounded-full bg-gradient-to-r from-primary-dim via-primary to-amber-200 transition-all duration-700 ease-out"
                style={{ width: `${pct}%` }}
              >
                <div className="progress-shimmer absolute inset-0 rounded-full" />
              </div>
            </div>
          </div>

          <div className="mb-8 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {PIPELINE_STEPS.map((step, i) => {
              const isStageActive = !allDone && i === activeStep;
              const isStageComplete =
                allDone || (!isStageActive && i < activeStep);
              return (
                <div
                  key={step.label}
                  className={`proc-step-connector proc-card-hover group relative overflow-hidden rounded-xl px-4 py-3 ring-1 transition-all duration-500 ${
                    isStageActive
                      ? "bg-primary/15 ring-primary/35 shadow-lg shadow-primary/15"
                      : isStageComplete
                        ? "bg-emerald-500/10 ring-emerald-500/25"
                        : "bg-slate-950/60 ring-white/10"
                  }`}
                >
                  {isStageActive && (
                    <div className="progress-shimmer absolute inset-0 opacity-40" aria-hidden />
                  )}
                  <div className="relative z-10 flex items-start gap-3">
                    <div
                      className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ring-1 transition-transform duration-300 group-hover:scale-110 ${
                        isStageComplete
                          ? "bg-emerald-500/15 text-emerald-400 ring-emerald-400/25"
                          : isStageActive
                            ? "bg-primary/20 text-primary ring-primary/30"
                            : "bg-slate-900/80 text-slate-500 ring-white/10"
                      }`}
                    >
                      <span className="material-symbols-outlined text-lg">{step.icon}</span>
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-bold text-slate-200">{step.label}</p>
                      <p className="mt-0.5 text-[10px] text-slate-500">{step.detail}</p>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="overflow-hidden rounded-2xl border border-white/10 bg-slate-950/50 shadow-inner shadow-black/20">
            <div className="flex flex-col gap-2 border-b border-white/10 bg-slate-900/40 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
              <div>
                <span className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-primary">
                  <span className="material-symbols-outlined text-sm">hub</span>
                  Platform-wise tracker
                </span>
                <p className="mt-1 text-sm text-slate-400">
                  Each row moves through Facebook, Instagram, X, TikTok, and YouTube.
                </p>
              </div>
              <span className="inline-flex w-fit items-center gap-2 rounded-full border border-white/10 bg-slate-900/80 px-3 py-1.5 text-xs font-bold text-slate-300 shadow-md">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-40" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
                </span>
                Live platform scan
              </span>
            </div>
            <ul className="max-h-[min(560px,62vh)] space-y-3 overflow-y-auto p-3 sm:p-4">
              {names.map((name, i) => {
                const s = statuses[i] ?? "queued";
                const platformProgress = rowPlatformProgress[i] ?? EMPTY_PLATFORM_PROGRESS;
                return (
                  <PlatformProgressRow
                    key={`${name}-${i}`}
                    name={name}
                    rowNumber={i + 1}
                    status={s}
                    platformProgress={platformProgress}
                    index={i}
                  />
                );
              })}
            </ul>
          </div>
        </div>
      </div>

      <p className="proc-enter proc-enter-delay-4 flex items-center justify-center gap-2 text-center text-xs uppercase tracking-[0.15em] text-slate-500">
        <span className="material-symbols-outlined text-sm text-primary/70">description</span>
        Export: Talent_Social_Lookup_*.xlsx in C:\Testing
      </p>
    </div>
  );
}

function StatusBadge({ status }: { status: RowStatus }) {
  if (status === "done") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-bold text-emerald-300 ring-1 ring-emerald-500/30 shadow-sm shadow-emerald-950/30">
        <span className="material-symbols-outlined text-sm">check</span>
        Done
      </span>
    );
  }
  if (status === "processing") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/15 px-2.5 py-1 text-xs font-bold text-primary ring-1 ring-primary/30 shadow-sm shadow-primary/10">
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-50" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
        </span>
        Processing
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-800/80 px-2.5 py-1 text-xs font-semibold text-slate-500 ring-1 ring-white/10">
      <span className="material-symbols-outlined text-sm opacity-60">schedule</span>
      Queued
    </span>
  );
}

function PlatformProgressRow({
  name,
  rowNumber,
  status,
  platformProgress,
  index,
}: {
  name: string;
  rowNumber: number;
  status: RowStatus;
  platformProgress: RowPlatformProgress;
  index: number;
}) {
  const isDone = status === "done";
  const isProcessing = status === "processing";
  const rowTone = isDone
    ? "border-emerald-500/25 bg-emerald-500/[0.07] shadow-emerald-950/20"
    : isProcessing
      ? "border-primary/35 bg-primary/[0.08] shadow-primary/15"
      : "border-white/10 bg-slate-900/60";

  return (
    <li
      className={`proc-row-enter proc-card-hover rounded-2xl border p-4 shadow-lg transition-all duration-300 ${rowTone}`}
      style={{ animationDelay: `${Math.min(index * 0.06, 0.48)}s` }}
    >
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-xs font-extrabold ring-1 transition-transform duration-300 hover:scale-105 ${
              isDone
                ? "bg-emerald-500/15 text-emerald-300 ring-emerald-400/25"
                : isProcessing
                  ? "bg-primary/15 text-primary ring-primary/30"
                  : "bg-slate-950/70 text-slate-500 ring-white/10"
            }`}
          >
            {isDone ? (
              <span className="material-symbols-outlined text-lg">verified</span>
            ) : (
              rowNumber.toString().padStart(2, "0")
            )}
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-extrabold text-slate-100">{name}</p>
            <p className="mt-1 inline-flex items-center gap-1 text-xs text-slate-500">
              <span className="material-symbols-outlined text-sm">
                {isDone ? "done_all" : isProcessing ? "sync" : "pending"}
              </span>
              {isDone
                ? "All platform checks completed"
                : isProcessing
                  ? "Resolving links platform by platform"
                  : "Waiting for backend worker"}
            </p>
          </div>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        {SOCIAL_PLATFORM_STEPS.map((platform) => {
          const platformState = getPlatformState(platform.label, status, platformProgress);
          return (
            <PlatformStepPill
              key={platform.label}
              label={platform.label}
              short={platform.short}
              icon={platform.icon}
              color={platform.color}
              state={platformState}
            />
          );
        })}
      </div>
    </li>
  );
}

function PlatformStepPill({
  label,
  short,
  icon,
  color,
  state,
}: {
  label: string;
  short: string;
  icon: string;
  color: string;
  state: "done" | "active" | "queued";
}) {
  const tone =
    state === "done"
      ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
      : state === "active"
        ? "border-primary/45 bg-primary/15 text-primary shadow-lg shadow-primary/15"
        : "border-white/10 bg-slate-950/55 text-slate-500";

  return (
    <div
      className={`group/pill relative overflow-hidden rounded-xl border bg-gradient-to-br px-3 py-2.5 ring-1 ring-white/5 transition-all duration-300 hover:-translate-y-0.5 ${tone} ${state !== "queued" ? color : ""}`}
    >
      {state === "active" && (
        <div className="progress-shimmer absolute inset-0 opacity-50" aria-hidden />
      )}
      <div className="relative z-10 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className={`material-symbols-outlined text-base transition-transform duration-300 group-hover/pill:scale-110 ${state === "active" ? "animate-pulse" : ""}`}
          >
            {icon}
          </span>
          <span className="hidden truncate text-xs font-bold sm:inline">{label}</span>
          <span className="text-xs font-bold sm:hidden">{short}</span>
        </div>
        {state === "done" ? (
          <span className="material-symbols-outlined text-sm text-emerald-300">check_circle</span>
        ) : state === "active" ? (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
          </span>
        ) : (
          <span className="h-2 w-2 shrink-0 rounded-full bg-slate-700" />
        )}
      </div>
    </div>
  );
}

function RunProgressPanel({
  activeName,
  doneCount,
  remainingCount,
  total,
  workbookStatus,
  pct,
  allDone,
}: {
  activeName: string;
  doneCount: number;
  remainingCount: number;
  total: number;
  workbookStatus: string;
  pct: number;
  allDone: boolean;
}) {
  return (
    <div className="w-full">
      <div className="mb-5 flex items-center justify-between gap-3">
        <div>
          <p className="inline-flex items-center gap-2 text-xs font-bold uppercase tracking-[0.18em] text-slate-500">
            <span className="material-symbols-outlined text-sm text-primary">analytics</span>
            Run progress
          </p>
          <p className="mt-1 text-sm font-semibold text-slate-200">
            {doneCount} of {total} rows completed
          </p>
        </div>
        <span
          className={`shrink-0 rounded-full border px-3 py-1 text-xs font-extrabold shadow-md ${
            allDone
              ? "border-emerald-400/30 bg-emerald-400/10 text-emerald-200"
              : "border-primary/25 bg-primary/10 text-primary"
          }`}
        >
          {allDone ? "Complete" : `${remainingCount} left`}
        </span>
      </div>

      <div className="mb-5 rounded-xl bg-slate-950/60 p-3 ring-1 ring-white/10">
        <div className="mb-2 flex items-center justify-between text-[10px] font-bold uppercase tracking-wider text-slate-500">
          <span>Overall</span>
          <span className="text-primary">{pct}%</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-gradient-to-r from-primary-dim to-primary transition-all duration-700"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>

      <div className="grid gap-3">
        <ProgressTile
          marker="01"
          icon="person"
          label="Current row"
          value={activeName}
          highlight={!allDone}
        />
        <ProgressTile
          marker="02"
          icon="verified"
          label="Verified rows"
          value={`${doneCount}/${total}`}
        />
        <ProgressTile marker="03" icon="description" label="Workbook" value={workbookStatus} />
      </div>
    </div>
  );
}

function ProgressTile({
  label,
  value,
  marker,
  icon,
  highlight = false,
}: {
  label: string;
  value: string;
  marker: string;
  icon: string;
  highlight?: boolean;
}) {
  return (
    <div
      className={`proc-tile-hover flex h-full min-w-0 items-start gap-3 rounded-xl p-3 ring-1 ${
        highlight
          ? "bg-primary/[0.06] ring-primary/20"
          : "bg-slate-900/70 ring-white/10"
      }`}
    >
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary ring-1 ring-primary/20">
        <span className="material-symbols-outlined text-lg">{icon}</span>
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-bold uppercase tracking-[0.14em] text-slate-500">
            {label}
          </p>
          <span className="text-[10px] font-extrabold text-primary/60">{marker}</span>
        </div>
        <p className="mt-0.5 truncate text-sm font-extrabold text-slate-100">{value}</p>
      </div>
    </div>
  );
}
