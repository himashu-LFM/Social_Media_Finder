"use client";

import { useSidebarState } from "@/components/SidebarState";

/** `<main>` that leaves room for `AppSidebar`, shrinking when it's collapsed.
 *  `className` should carry every utility except the left margin — this
 *  component owns that so it can stay in sync with the sidebar's state. */
export function AppMain({
  className = "",
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  const { collapsed } = useSidebarState();
  return (
    <main
      className={`${className} transition-[margin] duration-200 ${
        collapsed ? "md:ml-20" : "md:ml-64"
      }`}
    >
      {children}
    </main>
  );
}
