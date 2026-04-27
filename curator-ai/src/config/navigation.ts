export type NavItem = {
  icon: string;
  label: string;
  href: string;
};

export const MAIN_NAV: NavItem[] = [
  { icon: "dashboard", label: "Discovery", href: "/discovery" },
  { icon: "network_intel_node", label: "Processing", href: "/processing" },
  { icon: "table_chart", label: "Results", href: "/results" },
];

export function isNavActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(href + "/");
}
