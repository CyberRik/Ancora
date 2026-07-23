"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_ITEMS, activeItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

/**
 * The application chrome above the page.
 *
 * It used to hold a static tagline, which told a returning operator nothing.
 * Now it answers the two questions chrome is actually good for: where am I, and
 * which control plane am I pointed at — the second matters the moment more than
 * one stack exists.
 *
 * Below `md` the sidebar is hidden, so the same nav appears here as a scrolling
 * strip rather than disappearing.
 */
export function TopBar() {
  const pathname = usePathname();
  const current = activeItem(pathname);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";

  return (
    <div className="sticky top-0 z-30 border-b bg-background/85 backdrop-blur-md">
      <header className="flex h-14 items-center gap-3 px-6 lg:px-8">
        <span className="text-base leading-none md:hidden" aria-hidden>
          ⚓
        </span>
        <div className="min-w-0">
          <h1 className="truncate text-sm font-medium text-foreground">
            {current?.label ?? "Ancora"}
          </h1>
          {current?.blurb && (
            <p className="truncate text-xs leading-tight text-muted-foreground">
              {current.blurb}
            </p>
          )}
        </div>

        {apiUrl && (
          <div className="ml-auto hidden items-center gap-2 sm:flex">
            <span className="eyebrow">API</span>
            <code className="rounded border bg-card px-2 py-1 font-mono text-[11px] text-muted-foreground">
              {apiUrl.replace(/^https?:\/\//, "")}
            </code>
          </div>
        )}
      </header>

      <nav
        aria-label="Sections"
        className="flex gap-1 overflow-x-auto border-t px-4 py-1.5 md:hidden"
      >
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = current?.href === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={cn(
                "inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors",
                active
                  ? "bg-elevated font-medium text-foreground"
                  : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
              )}
            >
              <Icon className={cn("h-3.5 w-3.5", active && "text-flow")} />
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
