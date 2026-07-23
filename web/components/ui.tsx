/**
 * Shared presentation primitives.
 *
 * Every page was hand-rolling its own header, error box, "Loading…" string and
 * empty state, which is why they drifted apart. These are the shared versions:
 * no data fetching, no behaviour, just the house style in one place.
 */

import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/* -------------------------------------------------------------------------- */
/* Page header                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * The top of every page: an optional mono eyebrow, the title, one line of
 * orientation, and the tick-tape rule that runs under all of it.
 *
 * `live` drifts the tape — reserve it for pages whose data is actually polling,
 * so the motion stays meaningful rather than ambient.
 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  live = false,
}: {
  eyebrow?: string;
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  live?: boolean;
}) {
  return (
    <header className="animate-fade-up">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 max-w-2xl">
          {eyebrow && <p className="eyebrow text-flow">{eyebrow}</p>}
          <h2
            className={cn(
              "text-2xl font-semibold tracking-tight text-foreground",
              eyebrow && "mt-2",
            )}
          >
            {title}
          </h2>
          {description && (
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
      <div
        aria-hidden
        className={cn("rule-tape rule-tape--fade mt-5", live && "rule-tape--live")}
      />
    </header>
  );
}

/* -------------------------------------------------------------------------- */
/* Section                                                                     */
/* -------------------------------------------------------------------------- */

export function Section({
  title,
  description,
  actions,
  children,
  className,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("space-y-3", className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div>
          <h3 className="text-sm font-semibold tracking-tight text-foreground">{title}</h3>
          {description && (
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{description}</p>
          )}
        </div>
        {actions && <div className="flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Card                                                                        */
/* -------------------------------------------------------------------------- */

export function Card({
  className,
  children,
  interactive = false,
  ...rest
}: React.HTMLAttributes<HTMLDivElement> & { interactive?: boolean }) {
  return (
    <div
      className={cn(
        "rounded-lg border bg-card shadow-card",
        interactive && "transition-colors hover:border-border-strong hover:bg-elevated",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Stat                                                                        */
/* -------------------------------------------------------------------------- */

const STAT_TONE = {
  flow: "text-flow",
  success: "text-success",
  danger: "text-danger",
  warning: "text-warning",
  neutral: "text-foreground",
} as const;

export type StatTone = keyof typeof STAT_TONE;

/**
 * One number, its name, and what it means. The value is the only thing at
 * display size — the label and hint stay quiet so a row of these scans as data
 * rather than as four competing headlines.
 */
export function Stat({
  label,
  value,
  hint,
  tone = "neutral",
  icon: Icon,
  live = false,
}: {
  label: string;
  value: number | string;
  hint?: string;
  tone?: StatTone;
  icon?: LucideIcon;
  live?: boolean;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5">
        {Icon && <Icon className="h-3.5 w-3.5 text-muted-foreground" />}
        <span className="eyebrow">{label}</span>
        {live && (
          <span className={cn("relative ml-auto h-1.5 w-1.5 rounded-full", STAT_TONE[tone])}>
            <span className="pulse-dot absolute inset-0 rounded-full" />
            <span className="absolute inset-0 rounded-full bg-current" />
          </span>
        )}
      </div>
      <div
        data-numeric
        className={cn("mt-2 text-3xl font-semibold leading-none", STAT_TONE[tone])}
      >
        {value}
      </div>
      {hint && <div className="mt-2 text-xs text-muted-foreground">{hint}</div>}
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/* Empty state                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * An empty screen is an invitation to act, so this always ends in one — either
 * a link, a button, or the command that would produce the missing thing.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed bg-card/40 px-6 py-12 text-center",
        className,
      )}
    >
      {Icon && (
        <div className="mb-3 rounded-lg border bg-card p-2.5">
          <Icon className="h-5 w-5 text-muted-foreground" />
        </div>
      )}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && (
        <p className="mt-1.5 max-w-sm text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Loading                                                                     */
/* -------------------------------------------------------------------------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} />;
}

/** Placeholder rows shaped like the cards they stand in for. */
export function SkeletonCards({ count = 4, className }: { count?: number; className?: string }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <Card key={i} className={cn("space-y-3 p-4", className)}>
          <Skeleton className="h-2.5 w-20" />
          <Skeleton className="h-7 w-16" />
          <Skeleton className="h-2.5 w-24" />
        </Card>
      ))}
    </>
  );
}

/** Placeholder rows shaped like table rows, so the layout does not jump. */
export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  const widths = ["w-32", "w-20", "w-24", "w-16", "w-28"];
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-t">
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c} className="px-4 py-3">
              <Skeleton className={cn("h-3.5", widths[c % widths.length])} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Alert                                                                       */
/* -------------------------------------------------------------------------- */

const ALERT_TONE = {
  danger: { box: "border-danger/40 bg-danger/5", title: "text-danger" },
  warning: { box: "border-warning/40 bg-warning/5", title: "text-warning" },
  info: { box: "border-border-strong bg-elevated", title: "text-foreground" },
} as const;

/**
 * Errors say what happened and how to fix it. They do not apologise and they
 * are never vague, so `title` is the fact and `children` is the next move.
 */
export function Alert({
  tone = "danger",
  title,
  children,
  icon: Icon,
}: {
  tone?: keyof typeof ALERT_TONE;
  title: string;
  children?: React.ReactNode;
  icon?: LucideIcon;
}) {
  const t = ALERT_TONE[tone];
  return (
    <div className={cn("rounded-lg border p-4", t.box)} role="status">
      <div className="flex items-center gap-2">
        {Icon && <Icon className={cn("h-4 w-4", t.title)} />}
        <p className={cn("text-sm font-medium", t.title)}>{title}</p>
      </div>
      {children && (
        <div className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{children}</div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Buttons                                                                     */
/* -------------------------------------------------------------------------- */

const BUTTON_VARIANT = {
  primary:
    "bg-flow/15 text-flow border-flow/30 hover:bg-flow/25 hover:border-flow/50",
  secondary: "bg-card border-border hover:bg-elevated hover:border-border-strong",
  danger: "bg-danger/10 text-danger border-danger/30 hover:bg-danger/20 hover:border-danger/50",
} as const;

const BUTTON_BASE =
  "inline-flex items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-40";

export function Button({
  variant = "secondary",
  className,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: keyof typeof BUTTON_VARIANT;
}) {
  return <button className={cn(BUTTON_BASE, BUTTON_VARIANT[variant], className)} {...rest} />;
}

export function ButtonLink({
  variant = "secondary",
  className,
  ...rest
}: React.ComponentProps<typeof Link> & { variant?: keyof typeof BUTTON_VARIANT }) {
  return <Link className={cn(BUTTON_BASE, BUTTON_VARIANT[variant], className)} {...rest} />;
}

/* -------------------------------------------------------------------------- */
/* Small pieces                                                                */
/* -------------------------------------------------------------------------- */

/** A run/worker identifier. Mono, dimmed, and never the loudest thing in a row. */
export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("font-mono text-xs text-muted-foreground", className)}>{children}</span>
  );
}

/** A neutral metadata chip. */
export function Chip({
  children,
  className,
  tone,
}: {
  children: React.ReactNode;
  className?: string;
  tone?: "flow" | "muted";
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider",
        tone === "flow"
          ? "border-flow/25 bg-flow/10 text-flow"
          : "border-border bg-muted text-muted-foreground",
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Table shell: hairline border, sticky header, consistent density. */
export function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="overflow-hidden rounded-lg border shadow-card">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">{children}</table>
      </div>
    </div>
  );
}

export function Th({
  children,
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      scope="col"
      className={cn(
        "whitespace-nowrap border-b bg-elevated px-4 py-2.5 text-left font-mono text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground",
        className,
      )}
    >
      {children}
    </th>
  );
}

/* -------------------------------------------------------------------------- */
/* Switch                                                                     */
/* -------------------------------------------------------------------------- */

export function Switch({
  checked,
  onCheckedChange,
  className,
  disabled,
}: {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-flow disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-flow" : "bg-muted-foreground/30",
        className,
      )}
    >
      <span
        className={cn(
          "pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform",
          checked ? "translate-x-4" : "translate-x-0"
        )}
      />
    </button>
  );
}
