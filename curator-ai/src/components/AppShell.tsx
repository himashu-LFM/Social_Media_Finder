"use client";

import { AppSidebar } from "@/components/AppSidebar";
import "@/app/app-design.css";

/**
 * Every signed-in page: rail on the left, sticky header, then content.
 *
 * The handoff repeats this chrome inline on all seven pages. Collecting it
 * here means the rail, the 56px header and the stage strip are defined once —
 * seven inline copies is seven chances for them to drift.
 */
export function AppShell({
  icon,
  eyebrow,
  title,
  actions,
  stages,
  spinIcon = false,
  children,
}: {
  icon: string;
  eyebrow: string;
  title: string;
  /** Right-hand side of the header: status pills, links. */
  actions?: React.ReactNode;
  /** The Search → Validate → Score → Export strip. Omit to hide it. */
  stages?: React.ReactNode;
  /** Turn the header icon while a run is in flight (Processing). */
  spinIcon?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="sc-shell">
      <AppSidebar />

      <main className="sc-main">
        <div className="sc-header-wrap">
          <header className="sc-header">
            <span className="sc-header-icon">
              <span
                className={`material-symbols-outlined${spinIcon ? " sc-spin" : ""}`}
                style={{ fontSize: 17 }}
              >
                {icon}
              </span>
            </span>
            <span>
              <span className="sc-eyebrow">{eyebrow}</span>
              <span className="sc-title">{title}</span>
            </span>
            {actions && <span className="sc-header-actions">{actions}</span>}
          </header>
          {stages}
        </div>

        {children}
      </main>
    </div>
  );
}

/**
 * The pipeline strip under the header.
 *
 * `current` is the stage the run has reached: everything before it renders as
 * done (green, check), the stage itself as active (yellow, dot), everything
 * after as pending. `note` is the right-aligned status text — pass
 * `noteStrong` for the live counters the design shows in a brighter grey.
 */
const STAGES = [
  { key: "search", icon: "fact_check", label: "Search" },
  { key: "validate", icon: "fact_check", label: "Validate" },
  { key: "score", icon: "percent", label: "Score" },
  { key: "export", icon: "download", label: "Export" },
] as const;

export type StageKey = (typeof STAGES)[number]["key"];

export function StageStrip({
  current,
  note,
  noteStrong = false,
  pulse = false,
}: {
  current: StageKey;
  note?: string;
  noteStrong?: boolean;
  /** Breathe the active dot while a run is genuinely in flight. */
  pulse?: boolean;
}) {
  const currentIndex = STAGES.findIndex((s) => s.key === current);

  return (
    <div className="sc-stages">
      {STAGES.map((stage, i) => {
        const state = i < currentIndex ? "done" : i === currentIndex ? "active" : "";
        return (
          <span key={stage.key} style={{ display: "contents" }}>
            {i > 0 && (
              <span className="material-symbols-outlined sc-stage-sep" aria-hidden>
                chevron_right
              </span>
            )}
            <span
              className={`sc-stage${state ? ` ${state}` : ""}`}
              aria-current={state === "active" ? "step" : undefined}
            >
              {state === "active" ? (
                <span className={`sc-stage-dot${pulse ? " sc-pulse" : ""}`} aria-hidden />
              ) : (
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
                  {state === "done" ? "check_circle" : stage.icon}
                </span>
              )}
              {stage.label}
            </span>
          </span>
        );
      })}
      {note && <span className={`sc-stage-note${noteStrong ? " strong" : ""}`}>{note}</span>}
    </div>
  );
}
