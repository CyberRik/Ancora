"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRight, CheckCircle2, CircleAlert, Play, ServerCog, Zap } from "lucide-react";
import { api, type Queue, type Run, type Worker } from "@/lib/api";
import { SystemFlow } from "@/components/system-flow";
import { ResilienceStory } from "@/components/resilience-story";
import { StatusBadge } from "@/components/status-badge";
import {
  Alert,
  ButtonLink,
  Card,
  EmptyState,
  Mono,
  Section,
  SkeletonCards,
  Stat,
} from "@/components/ui";

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
    <div className="space-y-9">
      {/* Hero — the thesis, stated once and not repeated below. */}
      <header className="animate-fade-up">
        <div className="max-w-2xl">
          <p className="eyebrow text-flow">Durable execution runtime</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
            Kill any worker mid-run.
            <br />
            The work still finishes.
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-muted-foreground">
            Ancora records every step of a job as it happens, then hands the heavy work to a pool
            of workers. Kill one halfway through and another resumes from the last recorded step —
            no lost progress, nothing done twice.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <ButtonLink href="/chaos" variant="primary">
              <Zap className="h-4 w-4" />
              Break it yourself
            </ButtonLink>
            <ButtonLink href="/demo">
              Watch a run recover
              <ArrowRight className="h-4 w-4" />
            </ButtonLink>
          </div>
        </div>
        <div aria-hidden className="rule-tape rule-tape--fade rule-tape--live mt-7" />
      </header>

      {!connected && (
        <Alert
          tone="warning"
          icon={CircleAlert}
          title="Not connected to the control plane"
        >
          Nothing below is live yet. Start the stack with{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">make up</code>,
          and this page fills in on its own.
        </Alert>
      )}

      {/* Live numbers first — proof the thing is running, before the diagram
          that explains what it is. */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {snap === null && connected ? (
          <SkeletonCards count={4} />
        ) : (
          <>
            <Stat
              label="Running"
              value={running}
              tone={running ? "flow" : "neutral"}
              live={running > 0}
              icon={Play}
              hint={waiting ? `${waiting} awaiting approval` : "in flight now"}
            />
            <Stat
              label="Completed"
              value={completed}
              tone={completed ? "success" : "neutral"}
              icon={CheckCircle2}
              hint="all time"
            />
            <Stat
              label="Failed"
              value={failed}
              tone={failed ? "danger" : "neutral"}
              icon={CircleAlert}
              hint={failed ? "needs attention" : "none"}
            />
            <Stat
              label="Workers"
              value={`${liveWorkers}/${totalWorkers}`}
              tone={liveWorkers ? "success" : totalWorkers ? "danger" : "neutral"}
              icon={ServerCog}
              hint="live / registered"
            />
          </>
        )}
      </div>

      {/* Signature — the live pipeline */}
      <SystemFlow
        running={running}
        completed={completed}
        waiting={waiting}
        liveWorkers={liveWorkers}
        queues={snap?.queues ?? []}
        connected={connected}
      />

      {/* Recent runs */}
      <Section
        title="Recent runs"
        actions={
          <Link
            href="/runs"
            className="inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
          >
            View all
            <ArrowRight className="h-3 w-3" />
          </Link>
        }
      >
        {snap === null && connected ? (
          <Card className="divide-y">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-center gap-3 px-4 py-3">
                <div className="skeleton h-3.5 w-28" />
                <div className="skeleton h-3 w-16" />
                <div className="skeleton ml-auto h-5 w-20 rounded-full" />
              </div>
            ))}
          </Card>
        ) : runs.length === 0 ? (
          <EmptyState
            icon={Play}
            title="No runs yet"
            description="Start one from the Runs page, or let the Chaos Lab start one for you and kill a worker under it."
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <ButtonLink href="/runs" variant="primary">
                  Start a run
                </ButtonLink>
                <ButtonLink href="/chaos">Open Chaos Lab</ButtonLink>
              </div>
            }
          />
        ) : (
          <Card className="divide-y overflow-hidden">
            {runs.slice(0, 6).map((r) => (
              <Link
                key={r.id}
                href={`/runs/${r.id}`}
                className="flex items-center gap-3 px-4 py-3 text-sm transition-colors hover:bg-elevated"
              >
                <span className="truncate font-medium">{r.workflow_name}</span>
                <Mono>{r.id.slice(0, 8)}</Mono>
                <span className="ml-auto flex items-center gap-3">
                  <StatusBadge status={r.status} />
                  <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />
                </span>
              </Link>
            ))}
          </Card>
        )}
      </Section>

      {/* The durability promise, as a picture. Last: it explains, it does not
          report, so it should not outrank live state. */}
      <ResilienceStory />
    </div>
  );
}
