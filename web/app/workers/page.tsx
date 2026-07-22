"use client";

import { useEffect, useState } from "react";
import { api, type Queue, type Worker } from "@/lib/api";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<Worker["status"], string> = {
  live: "bg-success/15 text-success",
  stale: "bg-danger/15 text-danger",
  unknown: "bg-muted text-muted-foreground",
};

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

export default function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[] | null>(null);
  const [queues, setQueues] = useState<Queue[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    const load = () => {
      Promise.all([api.listWorkers(c.signal), api.listQueues(c.signal)])
        .then(([w, q]) => {
          setWorkers(w);
          setQueues(q);
          setError(null);
        })
        .catch((e) => {
          if (!c.signal.aborted) setError(e instanceof Error ? e.message : "failed to load");
        });
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Workers</h2>
        <p className="text-sm text-muted-foreground">
          Activity workers and the capability queues they serve. Health is a Redis
          liveness TTL refreshed each heartbeat; a worker goes{" "}
          <span className="text-danger">stale</span> when its TTL lapses.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          API error: {error}. Is the stack running?
        </div>
      )}

      {/* Queues */}
      <section className="space-y-3">
        <h3 className="text-sm font-medium text-muted-foreground">Queues</h3>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {queues?.map((q) => (
            <div key={q.queue} className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm">{q.queue}</span>
                {q.capability && (
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] uppercase text-muted-foreground">
                    {q.capability}
                  </span>
                )}
              </div>
              <div className="mt-3 flex items-baseline gap-4">
                <div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {q.live_worker_count}
                    <span className="text-sm text-muted-foreground">/{q.worker_count}</span>
                  </div>
                  <div className="text-[11px] text-muted-foreground">live workers</div>
                </div>
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{q.backlog}</div>
                  <div className="text-[11px] text-muted-foreground">backlog</div>
                </div>
              </div>
            </div>
          ))}
          {queues?.length === 0 && (
            <div className="text-sm text-muted-foreground">No queues.</div>
          )}
        </div>
      </section>

      {/* Workers */}
      <section className="space-y-3">
        <h3 className="text-sm font-medium text-muted-foreground">Registered workers</h3>
        {workers === null && !error && (
          <div className="text-sm text-muted-foreground">Loading…</div>
        )}
        {workers?.length === 0 && (
          <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
            No activity workers registered. Start one with{" "}
            <code className="rounded bg-muted px-1">ancora-activity-worker</code>.
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {workers?.map((w) => (
            <div key={w.worker_id} className="rounded-lg border bg-card p-4">
              <div className="flex items-center justify-between">
                <span className="font-mono text-sm">{w.worker_id}</span>
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                    STATUS_STYLES[w.status],
                  )}
                >
                  {w.status}
                </span>
              </div>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {w.pools.map((p) => (
                  <span
                    key={p}
                    className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase text-foreground"
                  >
                    {p}
                  </span>
                ))}
              </div>
              <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs text-muted-foreground">
                <dt>CPUs</dt>
                <dd className="text-right tabular-nums">{w.resources.total_cpus ?? "—"}</dd>
                <dt>GPUs</dt>
                <dd className="text-right tabular-nums">
                  {w.resources.total_gpus ?? 0}
                  {w.resources.accelerator_type ? ` · ${w.resources.accelerator_type}` : ""}
                </dd>
                <dt>Host / PID</dt>
                <dd className="text-right">
                  {w.host ?? "—"}
                  {w.pid ? ` · ${w.pid}` : ""}
                </dd>
                <dt>Heartbeat</dt>
                <dd className="text-right">{relTime(w.last_heartbeat_at)}</dd>
              </dl>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
