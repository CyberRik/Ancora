import { cn } from "@/lib/utils";
import type { RunStatus } from "@/lib/api";

/**
 * Run state, as a badge.
 *
 * Bordered rather than filled: a dozen of these down a table column read as a
 * legible column instead of a stack of coloured blocks. Running uses the
 * identity teal and pulses, because it is the only state that is still moving —
 * everything else is a result and should sit still.
 */
const STYLES: Record<string, string> = {
  Queued: "border-border-strong bg-muted text-muted-foreground",
  Running: "border-flow/30 bg-flow/10 text-flow",
  Completed: "border-success/30 bg-success/10 text-success",
  Failed: "border-danger/30 bg-danger/10 text-danger",
  Cancelled: "border-warning/30 bg-warning/10 text-warning",
  Terminated: "border-warning/30 bg-warning/10 text-warning",
  TimedOut: "border-danger/30 bg-danger/10 text-danger",
};

export function StatusBadge({
  status,
  className,
}: {
  status: RunStatus | string;
  className?: string;
}) {
  const live = status === "Running";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-medium",
        STYLES[status] ?? "border-border-strong bg-muted text-muted-foreground",
        className,
      )}
    >
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        {live && <span className="pulse-dot absolute inset-0 rounded-full" />}
        <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {status}
    </span>
  );
}
