export function Placeholder({
  title,
  phase,
  children,
}: {
  title: string;
  phase: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="max-w-xl space-y-2">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <div className="rounded-lg border border-dashed bg-card p-6 text-sm text-muted-foreground">
        <div className="font-medium text-foreground">Coming in {phase}</div>
        <p className="mt-1">
          {children ??
            "This screen is scaffolded but not yet implemented. See docs/IMPLEMENTATION-PLAN.md."}
        </p>
      </div>
    </div>
  );
}
