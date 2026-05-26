"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV, isNavActive } from "@/config/navigation";

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <nav className="fixed left-0 top-0 z-40 hidden h-screen w-64 flex-col gap-1.5 border-r border-white/5 bg-surface/95 p-6 backdrop-blur-xl md:flex">
      <Link
        href="/"
        className="lf-card-hover mb-8 inline-flex cursor-pointer items-center gap-2.5 rounded-xl px-2 py-1 font-[family-name:var(--font-manrope)] text-2xl font-black text-primary"
      >
        <span className="inline-block h-2.5 w-2.5 rounded-full bg-primary shadow-[0_0_16px_rgba(242,209,0,0.8)]" />
        ListenFirst
      </Link>

      {MAIN_NAV.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            className={`lf-card-hover group flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 text-sm font-medium transition ${
              active
                ? "bg-primary/10 text-primary shadow-md shadow-primary/10 ring-1 ring-primary/25"
                : "text-slate-400 hover:bg-white/5 hover:text-slate-100"
            }`}
          >
            <span
              className={`material-symbols-outlined text-[22px] transition-transform group-hover:scale-110 ${
                active ? "text-primary" : ""
              }`}
            >
              {item.icon}
            </span>
            <span>{item.label}</span>
            {active && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_8px_rgba(242,209,0,0.9)]" />
            )}
          </Link>
        );
      })}

      <div className="mt-auto pt-6">
        <div className="lf-gradient-border lf-card rounded-xl p-4">
          <div className="relative z-10">
            <p className="mb-2 inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-primary">
              <span className="material-symbols-outlined text-sm">route</span>
              Pipeline
            </p>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
              <div className="progress-shimmer h-full w-2/3 rounded-full bg-gradient-to-r from-primary-dim to-primary" />
            </div>
            <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
              Search → Validate → Score → Export
            </p>
          </div>
        </div>
      </div>
    </nav>
  );
}
