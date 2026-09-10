"use client";

import { createContext, useContext, useEffect, useState } from "react";

const STORAGE_KEY = "lf-sidebar-collapsed";

type SidebarContextValue = {
  collapsed: boolean;
  toggle: () => void;
};

const SidebarContext = createContext<SidebarContextValue | null>(null);

/** Remembers whether the left nav is collapsed, shared across every route so
 *  the choice sticks as the analyst moves between pages. */
export function SidebarProvider({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY) === "1";
      // Reads localStorage (an external system) once on mount to restore the
      // last choice — not derivable from props/state, so this is not a
      // cascading render, just the initial sync.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCollapsed(stored);
    } catch {
      /* localStorage unavailable — default to expanded */
    }
  }, []);

  const toggle = () =>
    setCollapsed((prev) => {
      const next = !prev;
      try {
        window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* best effort */
      }
      return next;
    });

  return (
    <SidebarContext.Provider value={{ collapsed, toggle }}>{children}</SidebarContext.Provider>
  );
}

export function useSidebarState(): SidebarContextValue {
  const ctx = useContext(SidebarContext);
  if (!ctx) return { collapsed: false, toggle: () => {} };
  return ctx;
}
