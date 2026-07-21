"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, type Run, type WorkflowDef } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, w] = await Promise.all([api.listRuns(), api.listWorkflows()]);
      setRuns(r);
      setWorkflows(w);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000); // crude live refresh (Phase 4 → WebSocket)
    return () => clearInterval(t);
  }, [load]);

  async function start(name: string) {
    setStarting(true);
    try {
      await api.startRun(name, { name: "Ada" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to start");
    } finally {
      setStarting(false);
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold tracking-tight">Runs</h2>
          <p className="text-sm text-muted-foreground">
            Durable workflow executions. Status refreshes automatically.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {workflows.map((w) => (
            <button
              key={w.name}
              onClick={() => start(w.name)}
              disabled={starting}
              className="rounded-md border bg-card px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-50"
            >
              ▷ Start “{w.name}”
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          API error: {error}. Is the stack running?
        </div>
      )}

      <div className="overflow-hidden rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-card/60 text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Workflow</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Run ID</th>
              <th className="px-4 py-2 font-medium">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs === null && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                  Loading…
                </td>
              </tr>
            )}
            {runs?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">
                  No runs yet. Start one above.
                </td>
              </tr>
            )}
            {runs?.map((run) => (
              <tr key={run.id} className="border-t hover:bg-muted/40">
                <td className="px-4 py-2">
                  <Link
                    href={`/runs/${run.id}`}
                    className="font-medium text-accent underline-offset-2 hover:underline"
                  >
                    {run.workflow_name}
                    <span className="ml-1 text-muted-foreground">v{run.version}</span>
                  </Link>
                </td>
                <td className="px-4 py-2">
                  <StatusBadge status={run.status} />
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">
                  {run.id.slice(0, 8)}
                </td>
                <td className="px-4 py-2 text-muted-foreground">
                  {run.started_at
                    ? new Date(run.started_at).toLocaleTimeString()
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
