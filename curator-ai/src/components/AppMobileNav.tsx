"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV, isNavActive } from "@/config/navigation";

export function AppMobileNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-0 left-0 z-50 flex w-full justify-around border-t border-white/10 bg-slate-950/95 px-2 pb-6 pt-2 backdrop-blur-xl md:hidden">
      {MAIN_NAV.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            className={`relative flex min-w-[4.5rem] cursor-pointer flex-col items-center justify-center rounded-xl px-2 py-1.5 transition ${
              active
                ? "bg-primary/10 text-primary"
                : "text-slate-500 hover:bg-white/5 hover:text-slate-200"
            }`}
          >
            {active && (
              <span className="absolute -top-0.5 h-0.5 w-8 rounded-full bg-primary shadow-[0_0_8px_rgba(242,209,0,0.8)]" />
            )}
            <span className={`material-symbols-outlined ${active ? "scale-110" : ""}`}>
              {item.icon}
            </span>
            <span className="mt-1 text-[9px] font-semibold uppercase tracking-wide">
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
