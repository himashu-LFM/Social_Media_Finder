"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV, isNavActive } from "@/config/navigation";

export function AppSidebar() {
  const pathname = usePathname();

  return (
    <nav className="fixed left-0 top-0 z-40 hidden h-screen w-64 flex-col gap-2 border-r border-white/5 bg-surface p-6 md:flex">
      <Link
        href="/"
        className="mb-8 inline-block cursor-pointer rounded-lg font-[family-name:var(--font-manrope)] text-2xl font-black text-primary transition hover:opacity-90 hover:brightness-110"
      >
        Curator AI
      </Link>

      {MAIN_NAV.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            className={`flex cursor-pointer items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium transition hover:translate-x-0.5 hover:brightness-110 active:opacity-80 ${
              active
                ? "bg-surface-high text-primary shadow-sm ring-1 ring-white/5"
                : "text-slate-400 hover:bg-white/5 hover:text-slate-200"
            }`}
          >
            <span className="material-symbols-outlined text-[22px]">{item.icon}</span>
            <span>{item.label}</span>
          </Link>
        );
      })}

      <div className="mt-auto pt-6">
        <div className="rounded-xl border border-primary/20 bg-primary/5 p-4">
          <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-primary">
            Pipeline
          </p>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700">
            <div className="h-full w-[72%] rounded-full bg-primary" />
          </div>
          <p className="mt-2 text-[10px] text-slate-400">
            Search {"->"} Validate {"->"} Export
          </p>
        </div>
      </div>
    </nav>
  );
}
