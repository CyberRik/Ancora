import Link from "next/link";

const TILES = [
  { label: "Running", value: "—", hint: "workflows in flight" },
  { label: "Completed", value: "—", hint: "all time" },
  { label: "Failed", value: "—", hint: "needs attention" },
  { label: "Waiting", value: "—", hint: "human approval" },
];

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Dashboard</h2>
        <p className="text-sm text-muted-foreground">
          The control plane is up. Workflow execution lands in Phase 1 — this shell
          is the Phase 0 walking skeleton.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {TILES.map((t) => (
          <div key={t.label} className="rounded-lg border bg-card p-4">
            <div className="text-xs uppercase tracking-wide text-muted-foreground">
              {t.label}
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{t.value}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">{t.hint}</div>
          </div>
        ))}
      </div>

      <div className="rounded-lg border bg-card p-5">
        <h3 className="text-sm font-medium">Getting started</h3>
        <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
          <li>
            • Check the API is reachable on the{" "}
            <Link href="/health" className="text-accent underline underline-offset-2">
              Health
            </Link>{" "}
            page.
          </li>
          <li>• Read the architecture in <code className="text-foreground">docs/RFC-0001</code>.</li>
          <li>• Follow the build in <code className="text-foreground">docs/IMPLEMENTATION-PLAN.md</code>.</li>
        </ul>
      </div>
    </div>
  );
}
