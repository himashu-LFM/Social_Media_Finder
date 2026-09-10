export type NavItem = {
  icon: string;
  label: string;
  href: string;
  /** Shown only to admins. The API enforces the role regardless — hiding the
   *  link is a courtesy, not a control. */
  adminOnly?: boolean;
};

export const MAIN_NAV: NavItem[] = [
  { icon: "dashboard", label: "Discovery", href: "/discovery" },
  { icon: "network_intel_node", label: "Processing", href: "/processing" },
  { icon: "table_chart", label: "Results", href: "/results" },
  { icon: "donut_large", label: "Analysis", href: "/analysis" },
  { icon: "history", label: "History", href: "/history" },
  { icon: "manage_accounts", label: "Users", href: "/admin/users", adminOnly: true },
];

export function isNavActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
