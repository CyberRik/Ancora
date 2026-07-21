"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type Run } from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";

const TERMINAL = new Set(["Completed", "Failed", "Cancelled", "Terminated", "TimedOut"]);

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRun(await api.getRun(id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, [id]);

  useEffect(() => {
    load();
    const t = setInterval(() => {
      setRun((cur) => {
        if (cur && TERMINAL.has(cur.status)) return cur; // stop polling when done
        load();
        return cur;
      });
    }, 1500);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="max-w-3xl space-y-5">
      <Link href="/runs" className="text-sm text-muted-foreground hover:text-foreground">
        ← Runs
      </Link>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          {error}
        </div>
      )}

      {run && (
        <>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-xl font-semibold tracking-tight">
                {run.workflow_name}{" "}
                <span className="text-muted-foreground">v{run.version}</span>
              </h2>
              <p className="font-mono text-xs text-muted-foreground">{run.id}</p>
            </div>
            <div className="flex items-center gap-3">
              <StatusBadge status={run.status} />
              {!TERMINAL.has(run.status) && (
                <button
                  onClick={() => api.cancelRun(run.id).then(load)}
                  className="rounded-md border bg-card px-3 py-1.5 text-sm hover:bg-muted"
                >
                  Cancel
                </button>
              )}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <Field label="Temporal Workflow ID" value={run.temporal_wf_id} mono />
            <Field label="Temporal Run ID" value={run.temporal_run_id} mono />
            <Field
              label="Started"
              value={run.started_at ? new Date(run.started_at).toLocaleString() : "—"}
            />
            <Field
              label="Closed"
              value={run.closed_at ? new Date(run.closed_at).toLocaleString() : "—"}
            />
          </div>

          <Payload title="Input" data={run.input} />
          <Payload title="Output" data={run.output} />
          {run.error && (
            <div className="rounded-lg border border-danger/40 bg-card p-4">
              <div className="text-xs uppercase text-muted-foreground">Error</div>
              <pre className="mt-1 whitespace-pre-wrap text-sm text-danger">
                {run.error}
              </pre>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1 text-sm ${mono ? "font-mono break-all" : ""}`}>{value}</div>
    </div>
  );
}

function Payload({
  title,
  data,
}: {
  title: string;
  data: Record<string, unknown> | null;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <pre className="mt-1 overflow-x-auto text-sm">
        {data ? JSON.stringify(data, null, 2) : "—"}
      </pre>
    </div>
  );
}
