import type { ReactNode } from "react";

type AppPageHeaderProps = {
  title: string;
  subtitle?: string;
  icon?: string;
  badge?: ReactNode;
  actions?: ReactNode;
};

export function AppPageHeader({ title, subtitle, icon, badge, actions }: AppPageHeaderProps) {
  return (
    <header className="lf-enter sticky top-0 z-30 flex w-full items-center justify-between border-b border-white/5 bg-background/85 px-6 py-4 backdrop-blur-xl">
      <div className="flex min-w-0 items-center gap-3">
        {icon && (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/25 shadow-lg shadow-primary/10">
            <span className="material-symbols-outlined text-primary">{icon}</span>
          </div>
        )}
        <div className="min-w-0">
          {subtitle && (
            <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-primary/80">
              {subtitle}
            </p>
          )}
          <h1 className="truncate text-lg font-bold text-slate-100">{title}</h1>
        </div>
        {badge}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
