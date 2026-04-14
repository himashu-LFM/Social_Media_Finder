"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { MAIN_NAV, isNavActive } from "@/config/navigation";

export function AppMobileNav() {
  const pathname = usePathname();

  return (
    <nav className="fixed bottom-0 left-0 z-50 flex w-full justify-around border-t border-white/10 bg-slate-950/90 px-4 pb-6 pt-3 backdrop-blur-xl md:hidden">
      {MAIN_NAV.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            className={`flex cursor-pointer flex-col items-center justify-center transition hover:brightness-125 active:scale-90 ${
              active ? "scale-110 text-primary" : "text-slate-500 hover:text-primary"
            }`}
          >
            <span className="material-symbols-outlined">{item.icon}</span>
            <span className="mt-1 text-[10px] font-semibold uppercase tracking-wide">
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}
