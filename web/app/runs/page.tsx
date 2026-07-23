"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ChevronRight, Play } from "lucide-react";
import { api, type Run, type WorkflowDef } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import {
  Alert,
  Button,
  Chip,
  EmptyState,
  Mono,
  PageHeader,
  SkeletonRows,
  TableShell,
  Th,
} from "@/components/ui";

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowDef[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  // Which workflow the Start button will launch. A select instead of one button
  // per workflow: the list grows with the node library, and ten buttons in the
  // header made every one of them look equally important.
  const [selected, setSelected] = useState<string>("");

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

  // Default the picker to the first workflow once definitions arrive.
  useEffect(() => {
    if (!selected && workflows.length > 0) setSelected(workflows[0].name);
  }, [workflows, selected]);

  async function start() {
    if (!selected) return;
    setStarting(true);
    try {
      await api.startRun(selected, { name: "Ada" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to start");
    } finally {
      setStarting(false);
    }
  }

  const active = runs?.filter((r) => r.status === "Running").length ?? 0;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Executions"
        title="Runs"
        description="Every workflow execution the control plane knows about, newest first. Status refreshes every two seconds."
        live={active > 0}
        actions={
          workflows.length > 0 && (
            <div className="flex items-center gap-2">
              <label htmlFor="workflow" className="sr-only">
                Workflow to start
              </label>
              <select
                id="workflow"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                className="h-[34px] rounded-md border bg-card px-2.5 text-sm text-foreground transition-colors hover:border-border-strong"
              >
                {workflows.map((w) => (
                  <option key={w.name} value={w.name}>
                    {w.name}
                  </option>
                ))}
              </select>
              <Button variant="primary" onClick={start} disabled={starting || !selected}>
                <Play className="h-3.5 w-3.5" />
                {starting ? "Starting…" : "Start run"}
              </Button>
            </div>
          )
        }
      />

      {error && (
        <Alert title="Can't reach the control plane">
          {error}. Check the API is up on{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">:8080</code>.
        </Alert>
      )}

      <TableShell>
        <thead>
          <tr>
            <Th>Workflow</Th>
            <Th>Status</Th>
            <Th>Run ID</Th>
            <Th>Started</Th>
            <Th className="w-10" />
          </tr>
        </thead>
        <tbody>
          {/* Only while genuinely loading. On error the alert above explains it —
              skeletons that never resolve read as a hung page. */}
          {runs === null && !error && <SkeletonRows rows={6} cols={5} />}

          {runs === null && error && (
            <tr>
              <td colSpan={5} className="px-4 py-8 text-center text-sm text-muted-foreground">
                No data to show while the control plane is unreachable.
              </td>
            </tr>
          )}

          {runs?.length === 0 && (
            <tr>
              <td colSpan={5} className="p-0">
                <EmptyState
                  className="rounded-none border-0 border-t border-dashed"
                  icon={Play}
                  title="No runs yet"
                  description={
                    workflows.length > 0
                      ? `Pick a workflow above and start one — ${workflows[0].name} is a good first look.`
                      : "No workflows are registered yet, which usually means the worker isn't running."
                  }
                />
              </td>
            </tr>
          )}

          {runs?.map((run) => (
            <tr key={run.id} className="group border-t transition-colors hover:bg-elevated">
              <td className="px-4 py-2.5">
                <Link
                  href={`/runs/${run.id}`}
                  className="font-medium text-foreground underline-offset-4 hover:underline"
                >
                  {run.workflow_name}
                </Link>
                <Chip className="ml-2">v{run.version}</Chip>
              </td>
              <td className="px-4 py-2.5">
                <StatusBadge status={run.status} />
              </td>
              <td className="px-4 py-2.5">
                <Mono>{run.id.slice(0, 8)}</Mono>
              </td>
              <td className="px-4 py-2.5 text-xs text-muted-foreground">
                {run.started_at ? new Date(run.started_at).toLocaleTimeString() : "—"}
              </td>
              <td className="px-4 py-2.5 text-right">
                <Link href={`/runs/${run.id}`} aria-label={`Open run ${run.id.slice(0, 8)}`}>
                  <ChevronRight className="h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100" />
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </TableShell>
    </div>
  );
}
