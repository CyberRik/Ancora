"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, type Queue, type Run, type Worker } from "@/lib/api";
import { SystemFlow } from "@/components/system-flow";
import { ResilienceStory } from "@/components/resilience-story";
import { StatusBadge } from "@/components/status-badge";

interface Snapshot {
  runs: Run[];
  workers: Worker[];
  queues: Queue[];
}

export default function DashboardPage() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const [runs, workers, queues] = await Promise.all([
        api.listRuns(signal),
        api.listWorkers(signal).catch(() => [] as Worker[]),
        api.listQueues(signal).catch(() => [] as Queue[]),
      ]);
      setSnap({ runs, workers, queues });
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    const c = new AbortController();
    load(c.signal);
    const t = setInterval(() => load(), 2000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [load]);

  const runs = snap?.runs ?? [];
  const running = runs.filter((r) => r.status === "Running").length;
  const completed = runs.filter((r) => r.status === "Completed").length;
  const failed = runs.filter((r) => r.status === "Failed").length;
  const waiting = runs.filter((r) => r.workflow_name === "gated" && r.status === "Running").length;
  const liveWorkers = (snap?.workers ?? []).filter((w) => w.status === "live").length;
  const totalWorkers = (snap?.workers ?? []).length;

  return (
    <div className="space-y-8">
      {/* Hero — the thesis */}
      <header className="max-w-2xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-flow">
          Durable execution runtime
        </p>
        <h2 className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl">
          Kill any worker mid-run. The work still finishes.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          Ancora writes down every step of a job as it happens, then hands the heavy work to a
          pool of workers. If one is killed halfway through, another picks up from the last saved
          point — no lost progress, no work done twice. Below is a job moving through that
          pipeline, live.
        </p>
      </header>

      {/* Signature — the live pipeline */}
      <SystemFlow
        running={running}
        completed={completed}
        waiting={waiting}
        liveWorkers={liveWorkers}
        queues={snap?.queues ?? []}
        connected={connected}
      />

      {/* The durability promise, as a picture */}
      <ResilienceStory />

      {!connected && (
        <div className="rounded-lg border border-warning/40 bg-card p-3 text-sm text-muted-foreground">
          Can&apos;t reach the API on <code className="text-foreground">:8080</code>. Start the
          stack with <code className="text-foreground">make up</code>, then this page comes alive.
        </div>
      )}

      {/* KPIs — real numbers */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Tile label="Running" value={running} tone="flow" hint={waiting ? `${waiting} awaiting approval` : "in flight"} />
        <Tile label="Completed" value={completed} tone="success" hint="all time" />
        <Tile label="Failed" value={failed} tone={failed ? "danger" : "muted"} hint="needs attention" />
        <Tile
          label="Workers"
          value={`${liveWorkers}/${totalWorkers}`}
          tone={liveWorkers ? "success" : "muted"}
          hint="live / registered"
        />
      </div>

      {/* Recent runs */}
      <section className="space-y-3">
        <div className="flex items-baseline justify-between">
          <h3 className="text-sm font-medium">Recent runs</h3>
          <Link href="/runs" className="text-xs text-accent hover:underline">
            View all →
          </Link>
        </div>
        <div className="divide-y rounded-lg border">
          {runs.length === 0 && (
            <div className="p-4 text-sm text-muted-foreground">
              No runs yet. Go to <Link href="/runs" className="text-accent hover:underline">Runs</Link> and start one.
            </div>
          )}
          {runs.slice(0, 6).map((r) => (
            <Link
              key={r.id}
              href={`/runs/${r.id}`}
              className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-muted/40"
            >
              <span className="font-medium">{r.workflow_name}</span>
              <span className="font-mono text-[11px] text-muted-foreground">
                {r.id.slice(0, 8)}
              </span>
              <span className="ml-auto">
                <StatusBadge status={r.status} />
              </span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}

const TONES: Record<string, string> = {
  flow: "text-flow",
  success: "text-success",
  danger: "text-danger",
  muted: "text-foreground",
};

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: number | string;
  hint: string;
  tone: keyof typeof TONES;
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div className={`mt-1 text-3xl font-semibold tabular-nums ${TONES[tone]}`}>{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>
    </div>
  );
}
