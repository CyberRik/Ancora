import {
  Activity,
  Box,
  GitBranch,
  HeartPulse,
  LayoutDashboard,
  Play,
  ServerCog,
  ShieldAlert,
  Stamp,
  Zap,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Shown in the sidebar as a one-line explanation of the section. */
  blurb: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * Grouped so the sidebar reads as three jobs rather than ten equal links:
 * what the system is doing now, what it can do, and what it is made of.
 * Shared with the top bar, which derives the current section from it.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operate",
    items: [
      { href: "/", label: "Dashboard", icon: LayoutDashboard, blurb: "Live system overview" },
      { href: "/runs", label: "Runs", icon: Play, blurb: "Workflow executions" },
      { href: "/approvals", label: "Approvals", icon: Stamp, blurb: "Gates awaiting a human" },
    ],
  },
  {
    label: "Prove it",
    items: [
      { href: "/demo", label: "Durability demo", icon: ShieldAlert, blurb: "Survive a worker failure" },
      { href: "/chaos", label: "Chaos lab", icon: Zap, blurb: "Kill a worker for real" },
    ],
  },
  {
    label: "Inspect",
    items: [
      { href: "/workflows", label: "Workflows", icon: GitBranch, blurb: "Registered definitions" },
      { href: "/nodes", label: "Nodes", icon: Box, blurb: "The built-in node library" },
      { href: "/workers", label: "Workers", icon: ServerCog, blurb: "Fleet and queues" },
      { href: "/history", label: "History", icon: Activity, blurb: "Event timeline" },
      { href: "/health", label: "Health", icon: HeartPulse, blurb: "Control-plane checks" },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

/** The nav entry matching a pathname — longest prefix wins, "/" only exactly. */
export function activeItem(pathname: string): NavItem | undefined {
  const matches = NAV_ITEMS.filter((i) =>
    i.href === "/" ? pathname === "/" : pathname === i.href || pathname.startsWith(`${i.href}/`),
  );
  return matches.sort((a, b) => b.href.length - a.href.length)[0];
}
