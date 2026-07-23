"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV_GROUPS, activeItem } from "@/lib/nav";
import { cn } from "@/lib/utils";

export function Nav() {
  const pathname = usePathname();
  const current = activeItem(pathname);

  return (
    <aside className="sticky top-0 hidden h-screen w-60 shrink-0 flex-col border-r bg-card/50 md:flex">
      {/* Wordmark. Height matches the top bar so the two rules meet. */}
      <div className="flex h-14 shrink-0 items-center gap-2.5 border-b px-5">
        <span className="text-base leading-none" aria-hidden>
          ⚓
        </span>
        <span className="font-semibold tracking-tight">Ancora</span>
        <span className="ml-auto rounded border border-flow/25 bg-flow/10 px-1.5 py-0.5 font-mono text-[10px] text-flow">
          v0.4
        </span>
      </div>

      <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-5 last:mb-0">
            <p className="eyebrow px-2 pb-2">{group.label}</p>
            <ul className="space-y-0.5">
              {group.items.map(({ href, label, icon: Icon }) => {
                const active = current?.href === href;
                return (
                  <li key={href}>
                    <Link
                      href={href}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "group relative flex items-center gap-2.5 rounded-md py-1.5 pl-3 pr-2 text-sm transition-colors",
                        active
                          ? "bg-elevated font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      {/* Active marker: a rail tick, echoing the tape rule. */}
                      <span
                        aria-hidden
                        className={cn(
                          "absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-r-full transition-colors",
                          active ? "bg-flow" : "bg-transparent",
                        )}
                      />
                      <Icon
                        className={cn(
                          "h-4 w-4 shrink-0 transition-colors",
                          active
                            ? "text-flow"
                            : "text-muted-foreground group-hover:text-foreground",
                        )}
                      />
                      <span className="truncate">{label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className="shrink-0 border-t px-5 py-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Durable execution on Temporal, distributed compute on Ray.
        </p>
      </div>
    </aside>
  );
}
