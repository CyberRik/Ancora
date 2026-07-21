"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  GitBranch,
  HeartPulse,
  LayoutDashboard,
  Play,
  ServerCog,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/workflows", label: "Workflows", icon: GitBranch },
  { href: "/runs", label: "Runs", icon: Play },
  { href: "/workers", label: "Workers", icon: ServerCog },
  { href: "/history", label: "History", icon: Activity },
  { href: "/chaos", label: "Chaos", icon: Zap },
  { href: "/health", label: "Health", icon: HeartPulse },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r bg-card/40">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <span className="text-lg">⚓</span>
        <span className="font-semibold tracking-tight">Ancora</span>
        <span className="ml-auto rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          v0.1
        </span>
      </div>
      <nav className="flex flex-col gap-1 p-2" aria-label="Primary">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent/15 text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="mt-auto p-3 text-[11px] text-muted-foreground">
        Phase 0 · walking skeleton
      </div>
    </aside>
  );
}
