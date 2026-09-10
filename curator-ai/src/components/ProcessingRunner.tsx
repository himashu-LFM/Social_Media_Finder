"use client";

import { authedFetch } from "@/lib/auth";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { AppShell, StageStrip } from "@/components/AppShell";
import { useToast } from "@/components/ToastProvider";
import { ViewResultsLink } from "@/components/ViewResultsLink";
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
  { label: "Wikipedia metadata", icon: "menu_book", detail: "Structured identity" },
  { label: "Apify + Serper", icon: "hub", detail: "Candidate links" },
  { label: "LLM verification", icon: "neurology", detail: "Rank per platform" },
  { label: "Status + export", icon: "fact_check", detail: "Workbook assembly" },
] as const;

const SOCIAL_PLATFORM_STEPS = [
  { label: "Instagram", short: "IG", icon: "photo_camera", color: "from-pink-500/20 to-purple-600/5" },
  { label: "Facebook", short: "FB", icon: "groups", color: "from-blue-500/20 to-blue-600/5" },
  { label: "YouTube", short: "YT", icon: "smart_display", color: "from-red-500/20 to-red-700/5" },
  { label: "TikTok", short: "TT", icon: "music_note", color: "from-cyan-400/20 to-teal-600/5" },
  { label: "X", short: "X", icon: "alternate_email", color: "from-slate-400/20 to-slate-600/5" },
] as const;

/** Row lifecycle → the badge in the tracker's Status column. */
const ROW_TONE: Record<RowStatus, string> = {
  done: "sc-tone-good",
  processing: "sc-tone-live",
  queued: "sc-tone-mute",
};
const ROW_LABEL: Record<RowStatus, string> = {
  done: "Done",
  processing: "Processing",
  queued: "Queued",
};

/** Per-platform cell: colour, glyph, and what the title attribute says. */
const CELL_COLOR = { done: "#34d399", active: "#f2d100", queued: "#475569" } as const;
const CELL_ICON = { done: "check_circle", active: "sync", queued: "schedule" } as const;
const CELL_TITLE = { done: "done", active: "checking now", queued: "queued" } as const;

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

export function ProcessingRunner() {
  const { pushToast } = useToast();
  const [mounted, setMounted] = useState(false);
  const [names, setNames] = useState<string[] | null>(null);
  const [statuses, setStatuses] = useState<RowStatus[]>([]);
  const [rowPlatformProgress, setRowPlatformProgress] = useState<RowPlatformProgress[]>([]);
  const [source, setSource] = useState<RunSource>("idle");
  const [currentStepIndex, setCurrentStepIndex] = useState(0);
  const [backendError, setBackendError] = useState<string | null>(null);
  const [stopRequested, setStopRequested] = useState(false);
  const [runCancelled, setRunCancelled] = useState(false);
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
        const res = await authedFetch(`/api/jobs/${jid}`);
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
        if (data.status === "cancelling") {
          setStopRequested(true);
        }
        if (data.status === "cancelled") {
          setRunCancelled(true);
          setStopRequested(false);
          if (!completionMarkedRef.current) {
            completionMarkedRef.current = true;
            // Partial results ARE saved, so treat a stop as a finished run —
            // the operator can still open and export what completed.
            markProcessingRunFinished();
            pushToast("Run stopped. Partial results were saved.", "success");
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

  async function handleStop() {
    if (stopRequested || runCancelled) return;
    setStopRequested(true);

    // Preview mode has no backend job — just halt the local simulation.
    if (source !== "python") {
      cancelledRef.current = true;
      setRunCancelled(true);
      pushToast("Preview run stopped.", "success");
      return;
    }

    const api = getPythonApiUrl();
    const jid = readPythonJobId();
    if (!api || !jid) return;

    try {
      const res = await authedFetch(`/api/jobs/${jid}/cancel`, { method: "POST" });
      if (!res.ok) throw new Error(String(res.status));
      pushToast("Stopping — finishing the checks already in flight…", "success");
    } catch {
      setStopRequested(false);
      pushToast("Could not reach the API to stop the run.", "error");
    }
  }
  /* ── render ─────────────────────────────────────────────────────────────
     The Claude Design handoff's Processing page: a run summary naming the row
     being worked on, the four pipeline phases, and a platform-wise tracker.
     The chrome comes from AppShell so the rail, header and stage strip match
     every other page. */

  const pct = total ? Math.round((doneCount / total) * 100) : 0;
  const activeName = currentNameIndex >= 0 ? names?.[currentNameIndex] : undefined;
  const liveMessage = LIVE_MESSAGES[currentStepIndex % LIVE_MESSAGES.length];
  const isWaitingForFirstRow = !allDone && doneCount === 0 && currentNameIndex < 0;
  const activeStep = currentStepIndex % PIPELINE_STEPS.length;

  const statusLabel = runCancelled
    ? "Stopped"
    : stopRequested
      ? "Stopping…"
      : allDone
        ? "Completed"
        : backendError
          ? "Needs attention"
          : "Live processing";
  const statusTone = backendError
    ? "sc-tone-bad"
    : runCancelled || stopRequested
      ? "sc-tone-info"
      : allDone
        ? "sc-tone-good"
        : "sc-tone-live";
  const running = !allDone && !runCancelled && !backendError && total > 0;
  // Stop is offered only while a run is genuinely in progress.
  const canStop = running && !stopRequested;

  /** Every state of this page wears the same chrome, so it is built once. */
  const shell = (children: React.ReactNode) => (
    <AppShell
      icon="sync"
      spinIcon={running}
      eyebrow="Live pipeline"
      title="Processing"
      stages={
        total > 0 ? (
          <StageStrip
            current={allDone ? "export" : "validate"}
            pulse={running}
            noteStrong
            note={`${doneCount} of ${total} rows · ${pct}%`}
          />
        ) : (
          <StageStrip current="search" note="No run in progress" />
        )
      }
      actions={
        <>
          <span className={`sc-pill ${statusTone}`} style={{ color: "var(--tone)" }}>
            <span className={`sc-dot${running ? " sc-pulse" : ""}`} />
            {statusLabel}
          </span>
          {canStop && (
            <button type="button" className="sc-btn danger" onClick={() => void handleStop()}>
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                stop_circle
              </span>
              Stop run
            </button>
          )}
          <ViewResultsLink className="sc-btn">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
              table_chart
            </span>
            View Results
          </ViewResultsLink>
        </>
      }
    >
      {children}
    </AppShell>
  );

  if (!mounted) {
    return shell(
      <div className="sc-stack">
        <div className="sc-empty" aria-busy="true">
          <span className="material-symbols-outlined sc-spin">progress_activity</span>
          Loading pipeline status…
        </div>
      </div>,
    );
  }

  if (names === null) return shell(<div className="sc-stack" />);

  if (names.length === 0) {
    return shell(
      <div className="sc-stack">
        <section className="sc-drop" style={{ borderStyle: "solid" }}>
          <span className="sc-drop-icon">
            <span className="material-symbols-outlined" style={{ fontSize: 22 }}>
              folder_open
            </span>
          </span>
          <h2>No names to process</h2>
          <p>
            Go to Discovery, upload a talent list or enter names, then start the run. You land
            here while each name is searched and scored in the background.
          </p>
          <Link href="/discovery" className="sc-btn-primary" style={{ marginTop: 16 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
              arrow_back
            </span>
            Back to Discovery
          </Link>
        </section>
      </div>,
    );
  }

  return shell(
    <div className="sc-stack" style={{ gap: 16 }}>
      {backendError && (
        <div className="sc-banner sc-tone-bad" role="alert">
          <span className="material-symbols-outlined">error</span>
          <span>
            <strong>Processing connection needs attention</strong> — {backendError}
          </span>
        </div>
      )}

      <section className="sc-card" style={{ overflow: "hidden" }}>
        <div className="sc-hero-head">
          <span style={{ minWidth: 0 }}>
            <span className="sc-metric-label">Working on</span>
            <span className="sc-hero-name">
              {allDone
                ? "Finished"
                : runCancelled
                  ? "Stopped"
                  : (activeName ?? "Waiting for the first row…")}
            </span>
          </span>
          <span className="sc-metrics">
            <span className="sc-metric">
              <span className="sc-metric-label">Progress</span>
              <span className="sc-metric-value">{pct}%</span>
            </span>
            <span className="sc-metric good">
              <span className="sc-metric-label">Done</span>
              <span className="sc-metric-value">{doneCount}</span>
            </span>
            <span className="sc-metric">
              <span className="sc-metric-label">Remaining</span>
              <span className="sc-metric-value">{remainingCount}</span>
            </span>
            <span className="sc-metric">
              <span className="sc-metric-label">Workbook</span>
              <span className="sc-metric-text">
                {allDone ? "Ready to view" : "Building export"}
              </span>
            </span>
          </span>
        </div>
        <div className="sc-progress">
          <span style={{ width: `${pct}%` }} />
        </div>
        <p className="sc-hero-note">
          <span className="material-symbols-outlined">info</span>
          {allDone
            ? "Every row is done. Open Results to review and export the workbook."
            : runCancelled
              ? "The run was stopped. Partial results were saved — open Results to review them."
              : isWaitingForFirstRow
                ? "Waiting for the backend to pick up the first row…"
                : liveMessage}
        </p>
      </section>

      <section className="sc-row" style={{ gap: 8 }}>
        {PIPELINE_STEPS.map((step, i) => {
          const state =
            allDone || i < activeStep ? "done" : i === activeStep && running ? "active" : "";
          return (
            <span key={step.label} className={`sc-phase${state ? ` ${state}` : ""}`}>
              <span className="material-symbols-outlined">{step.icon}</span>
              <span style={{ minWidth: 0 }}>
                <span className="sc-phase-name">{step.label}</span>
                <span className="sc-phase-sub">{step.detail}</span>
              </span>
              {state === "done" && (
                <span
                  className="material-symbols-outlined sc-phase-mark"
                  style={{ fontSize: 16, color: "#34d399" }}
                >
                  check_circle
                </span>
              )}
              {state === "active" && (
                <span className="sc-dot sc-pulse sc-phase-mark sc-tone-live" />
              )}
            </span>
          );
        })}
      </section>

      <section className="sc-card" style={{ overflow: "hidden" }}>
        <div className="sc-card-head">
          <span className="material-symbols-outlined">hub</span>
          <h3 className="sc-card-title">Platform-wise tracker</h3>
          <span className="sc-card-note">
            Each row moves through Instagram, Facebook, YouTube, TikTok, and X.
          </span>
          {running && (
            <span className="sc-pill sc-tone-live" style={{ marginLeft: "auto", height: 26 }}>
              <span className="sc-dot sc-pulse" />
              Live platform scan
            </span>
          )}
        </div>

        <div className="sc-data-wrap tall">
          <table className="sc-data" style={{ minWidth: 720 }}>
            <thead>
              <tr>
                <th className="num" style={{ width: 34 }}>
                  #
                </th>
                <th>Talent</th>
                <th>Status</th>
                {SOCIAL_PLATFORM_STEPS.map((p) => (
                  <th key={p.short} className="mid" style={{ width: 62 }}>
                    {p.short}
                  </th>
                ))}
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {names.map((name, i) => {
                const status = statuses[i] ?? "queued";
                const progress = rowPlatformProgress[i] ?? EMPTY_PLATFORM_PROGRESS;
                return (
                  <tr
                    key={`${name}-${i}`}
                    className={status === "processing" ? "sc-row-active" : ""}
                  >
                    <td className="num" style={{ fontSize: 11, color: "#64748b" }}>
                      {String(i + 1).padStart(2, "0")}
                    </td>
                    <td className="name" style={{ fontSize: 12.5 }}>
                      {name}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      <span className={`sc-badge ${ROW_TONE[status]}`}>{ROW_LABEL[status]}</span>
                    </td>
                    {SOCIAL_PLATFORM_STEPS.map((p) => {
                      const cell = getPlatformState(p.label, status, progress);
                      return (
                        <td key={p.short} className="mid">
                          <span
                            className={`material-symbols-outlined${cell === "active" ? " sc-spin" : ""}`}
                            style={{ fontSize: 17, color: CELL_COLOR[cell] }}
                            title={`${p.label}: ${CELL_TITLE[cell]}`}
                          >
                            {CELL_ICON[cell]}
                          </span>
                        </td>
                      );
                    })}
                    <td style={{ fontSize: 11.5, color: "#64748b" }}>
                      {status === "done"
                        ? "All platform checks completed"
                        : status === "processing"
                          ? `Resolving ${progress.currentPlatform ?? "links"} platform by platform`
                          : "Waiting for backend worker"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="sc-foot">
          <span className="sc-foot-item">
            <span className="material-symbols-outlined" style={{ color: "#34d399" }}>
              check_circle
            </span>
            Done
          </span>
          <span className="sc-foot-item">
            <span className="material-symbols-outlined" style={{ color: "#f2d100" }}>
              sync
            </span>
            Checking now
          </span>
          <span className="sc-foot-item">
            <span className="material-symbols-outlined" style={{ color: "#475569" }}>
              schedule
            </span>
            Queued
          </span>
          <span className="sc-foot-item" style={{ marginLeft: "auto" }}>
            <span className="material-symbols-outlined" style={{ color: "rgba(242,209,0,0.7)" }}>
              description
            </span>
            Export: Talent_Social_Lookup_*.xlsx
          </span>
        </div>
      </section>
    </div>,
  );
}
