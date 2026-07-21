import { cn } from "@/lib/utils";
import type { RunStatus } from "@/lib/api";

const STYLES: Record<string, string> = {
  Queued: "bg-muted text-muted-foreground",
  Running: "bg-accent/15 text-accent",
  Completed: "bg-success/15 text-success",
  Failed: "bg-danger/15 text-danger",
  Cancelled: "bg-warning/15 text-warning",
  Terminated: "bg-warning/15 text-warning",
  TimedOut: "bg-danger/15 text-danger",
};

export function StatusBadge({ status }: { status: RunStatus | string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium",
        STYLES[status] ?? "bg-muted text-muted-foreground",
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}
