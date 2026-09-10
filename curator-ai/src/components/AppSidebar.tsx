"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { MAIN_NAV, isNavActive } from "@/config/navigation";
import { fetchMe } from "@/lib/auth";
import { useSidebarState } from "@/components/SidebarState";
import { UserMenu } from "@/components/UserMenu";

export function AppSidebar() {
  const pathname = usePathname();
  const { collapsed, toggle } = useSidebarState();

  // Admin-only links are hidden from analysts. This is presentation only: the
  // API returns 403 to a non-admin whatever the sidebar shows.
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    let alive = true;
    void fetchMe().then((me) => {
      // null means auth is switched off entirely (local dev) — show everything.
      if (alive) setIsAdmin(me === null || me.role?.toLowerCase() === "admin");
    });
    return () => {
      alive = false;
    };
  }, []);

  const items = MAIN_NAV.filter((item) => !item.adminOnly || isAdmin);

  return (
    <nav
      className={`fixed left-0 top-0 z-40 hidden h-screen flex-col gap-1.5 border-r border-white/5 bg-surface/95 p-6 backdrop-blur-xl transition-[width] duration-200 md:flex ${
        collapsed ? "w-20 items-center px-3" : "w-64"
      }`}
    >
      <button
        type="button"
        onClick={toggle}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="lf-card-hover absolute -right-3 top-8 z-50 flex h-6 w-6 cursor-pointer items-center justify-center rounded-full border border-white/10 bg-surface-high text-slate-400 shadow-md transition hover:text-primary"
      >
        <span className="material-symbols-outlined text-sm">
          {collapsed ? "chevron_right" : "chevron_left"}
        </span>
      </button>

      <Link
        href="/"
        className={`lf-card-hover mb-8 inline-flex cursor-pointer items-center gap-2.5 rounded-xl px-2 py-1 font-[family-name:var(--font-manrope)] text-2xl font-black text-primary ${
          collapsed ? "justify-center px-0" : ""
        }`}
      >
        <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-full bg-primary shadow-[0_0_16px_rgba(242,209,0,0.8)]" />
        {!collapsed && "ListenFirst"}
      </Link>

      {items.map((item) => {
        const active = isNavActive(pathname, item.href);
        return (
          <Link
            key={item.label}
            href={item.href}
            title={collapsed ? item.label : undefined}
            className={`lf-card-hover group flex cursor-pointer items-center gap-3 rounded-xl py-3 text-sm font-medium transition ${
              collapsed ? "justify-center px-0" : "px-4"
            } ${
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
            {!collapsed && <span>{item.label}</span>}
            {active && !collapsed && (
              <span className="ml-auto h-1.5 w-1.5 rounded-full bg-primary shadow-[0_0_8px_rgba(242,209,0,0.9)]" />
            )}
          </Link>
        );
      })}

      <div className="mt-auto w-full pt-6">
        {!collapsed && (
          <div className="lf-gradient-border lf-card rounded-xl p-4">
            <div className="relative z-10">
              <p className="mb-2 inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-primary">
                <span className="material-symbols-outlined text-sm">route</span>
                Pipeline
              </p>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                <div className="h-full w-2/3 rounded-full bg-gradient-to-r from-primary-dim to-primary" />
              </div>
              <p className="mt-2 text-[10px] leading-relaxed text-slate-500">
                Search → Validate → Score → Export
              </p>
            </div>
          </div>
        )}
        <UserMenu collapsed={collapsed} />
      </div>
    </nav>
  );
}
