"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  WORKFLOW_SHAPES,
  type GraphNodeState,
  type Run,
  type WorkflowStep,
  type RunGraph,
  type RunLive,
  type RunRecovery,
  type RunReplay,
} from "@/lib/api";
import { useRunStream } from "@/lib/stream";
import { StatusBadge } from "@/components/status-badge";
import { RunInspector } from "@/components/run-inspector";
import { RecoveryTimeline } from "@/components/recovery-timeline";
import { RunDag } from "@/components/run-dag";
import { HistoryScrubber } from "@/components/history-scrubber";
import { ArrowLeft, CheckCircle2, ShieldAlert, Zap } from "lucide-react";
import { cn } from "@/lib/utils";

const TERMINAL = new Set([
  "Completed",
  "Failed",
  "Cancelled",
  "Terminated",
  "TimedOut",
]);

function fmtDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

export default function RunDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [live, setLive] = useState<RunLive | null>(null);
  // Owned here rather than inside RecoveryTimeline: this endpoint reads a whole
  // workflow history, and the step cards need the same answer.
  const [recovery, setRecovery] = useState<RunRecovery | null>(null);
  const [graph, setGraph] = useState<RunGraph | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [busy, setBusy] = useState(false);
  // History scrubber: node states at the scrubbed instant, or null for live.
  const [scrub, setScrub] = useState<Record<string, GraphNodeState> | null>(null);
  const [replay, setReplay] = useState<RunReplay | null>(null);
  const [replaying, setReplaying] = useState(false);

  const load = useCallback(async () => {
    try {
      const [r, l, rec, g] = await Promise.all([
        api.getRun(id),
        api.getRunActivities(id).catch(() => null),
        api.getRunRecovery(id).catch(() => null),
        api.getRunGraph(id).catch(() => null),
      ]);
      setRun(r);
      if (l) setLive(l);
      if (rec) setRecovery(rec);
      if (g) setGraph(g);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, [id]);

  // Coalesce a burst of live events (e.g. a fan-out of three completing at once)
  // into a single refetch shortly after — the graph endpoint is the authoritative
  // reconstruction, so we re-read it rather than mutating vertices client-side.
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scheduleRefetch = useCallback(() => {
    if (refetchTimer.current) return;
    refetchTimer.current = setTimeout(() => {
      refetchTimer.current = null;
      load();
    }, 250);
  }, [load]);

  const isRunning = !!run && !TERMINAL.has(run.status);
  // Live push: the DAG animates off worker-emitted events. `run` may be null on
  // the very first render — stream once we know the run and it is still moving.
  const streamState = useRunStream(id, scheduleRefetch, isRunning);

  useEffect(() => {
    load();
    // Safety net only: the socket carries live updates, but a slow poll catches
    // the terminal transition if the stream is ever down (Redis blip, no consumer).
    const poll = setInterval(() => {
      setRun((cur) => {
        if (cur && TERMINAL.has(cur.status)) return cur;
        load();
        return cur;
      });
    }, 5000);
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(clock);
      if (refetchTimer.current) clearTimeout(refetchTimer.current);
    };
  }, [load]);

  async function approve() {
    if (!run) return;
    setBusy(true);
    try {
      if (run.workflow_name === "research_agent") {
        await api.sendSignal(run.id, "submit_decision", {
          gate_id: "publish",
          approved: true,
          comment: "Approved via UI",
        });
      } else {
        await api.sendSignal(run.id, "approve");
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "approve failed");
    } finally {
      setBusy(false);
    }
  }

  if (error && !run) {
    return (
      <div className="max-w-3xl space-y-4">
        <Link
          href="/runs"
          className="text-sm text-muted-foreground hover:text-foreground"
        >
          ← Runs
        </Link>
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          {error}
        </div>
      </div>
    );
  }
  if (!run) return <div className="text-sm text-muted-foreground">Loading…</div>;

  const shape = WORKFLOW_SHAPES[run.workflow_name];
  const hasGraph = (graph?.nodes.length ?? 0) > 0;
  const isTerminal = TERMINAL.has(run.status);
  const startedMs = run.started_at ? new Date(run.started_at).getTime() : null;
  const endMs = run.closed_at ? new Date(run.closed_at).getTime() : now;
  const elapsed = startedMs ? fmtDuration(endMs - startedMs) : "—";

  // A gated run that is Running with no output yet is parked at its approval gate.
  const atGate =
    (run.workflow_name === "gated" || run.workflow_name === "research_agent") &&
    run.status === "Running" &&
    !run.output;

  return (
    <div className="max-w-4xl space-y-6">
      <Link
        href="/runs"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Runs
      </Link>

      {/* Header */}
      <header>
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0">
            <p className="eyebrow">
              {run.workflow_name} · v{run.version}
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">
              {shape?.summary ?? "Durable workflow run"}
            </h2>
            <p className="mt-1.5 break-all font-mono text-xs text-muted-foreground">
              {run.id}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            {isRunning && streamState === "open" && (
              <span className="inline-flex items-center gap-1.5 rounded-full border border-flow/40 bg-flow/10 px-2.5 py-1 font-mono text-[10px] uppercase tracking-wider text-flow">
                <span className="relative flex h-1.5 w-1.5">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-flow opacity-75" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-flow" />
                </span>
                Live
              </span>
            )}
            <StatusBadge status={run.status} />
            {!isTerminal && (
              <button
                onClick={() => api.cancelRun(run.id).then(load)}
                className="rounded-md border bg-card px-3 py-1.5 text-sm transition-colors hover:border-border-strong hover:bg-elevated"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
        <div
          aria-hidden
          className={`rule-tape rule-tape--fade mt-5 ${isTerminal ? "" : "rule-tape--live"}`}
        />
      </header>

      {/* Durable-wait callout — the gated case */}
      {atGate && (
        <div className="rounded-xl border border-warning/40 bg-warning/5 p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-mono text-[11px] uppercase tracking-wider text-warning">
                Durable wait · {elapsed}
              </p>
              <p className="mt-1 text-sm">
                This run is parked at a <strong>human-approval gate</strong>. It has
                been waiting durably — surviving worker restarts — and will resume the
                instant it&apos;s approved.
              </p>
            </div>
            <button
              onClick={approve}
              disabled={busy}
              className="rounded-md bg-warning px-4 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-50"
            >
              {busy ? "Approving…" : "Approve"}
            </button>
          </div>
        </div>
      )}

      {/* Live progress note */}
      {live?.status_note && (
        <div className="rounded-xl border border-flow/40 bg-flow/5 p-4">
          <div className="font-mono text-[11px] uppercase tracking-wider text-flow">
            Live Progress
          </div>
          <p className="mt-1 text-sm font-medium">{live.status_note}</p>
        </div>
      )}

      {/* Lifecycle timeline */}
      <Timeline run={run} atGate={atGate} elapsed={elapsed} />

      {/* The DAG this run executed, from history. Supersedes the declared shape
          below as soon as the workflow has scheduled anything real. */}
      <RunDag data={graph} override={scrub} />

      {/* Scrub the recorded event log; the DAG above replays through history. */}
      {hasGraph && <HistoryScrubber runId={run.id} onScrub={setScrub} />}

      {/* Determinism replay — the durability guarantee, made checkable. */}
      {isTerminal && hasGraph && (
        <ReplayCard
          replay={replay}
          busy={replaying}
          onReplay={async () => {
            setReplaying(true);
            try {
              setReplay(await api.replayRun(run.id));
            } catch (e) {
              setError(e instanceof Error ? e.message : "replay failed");
            } finally {
              setReplaying(false);
            }
          }}
        />
      )}

      {/* Declared shape — what this workflow is *meant* to do. Only shown before
          the run has committed to any work, when there is no graph to draw. */}
      {shape && !hasGraph && (
        <section className="space-y-3">
          <h3 className="text-sm font-medium">Steps</h3>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-stretch">
            {shape.steps.map((step, i) => (
              <StepCard
                key={i}
                step={step}
                index={i}
                total={shape.steps.length}
                run={run}
                atGate={atGate}
                onApprove={approve}
                busy={busy}
                replayed={recovery?.replayed_activities ?? 0}
              />
            ))}
          </div>
        </section>
      )}

      {/* What a worker death did to this run, and what it is waiting on now */}
      <RecoveryTimeline data={recovery} terminal={isTerminal} />

      {/* Per-node inspector: attempts, failures, and cost (Phase 3) */}
      <RunInspector runId={run.id} live={live} terminal={isTerminal} />

      {/* Result */}
      <Result run={run} />

      {/* Provenance */}
      <details className="rounded-lg border bg-card">
        <summary className="cursor-pointer px-4 py-2.5 text-sm text-muted-foreground">
          Temporal identifiers & raw payloads
        </summary>
        <div className="space-y-3 border-t p-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Workflow ID" value={run.temporal_wf_id} mono />
            <Field label="Run ID" value={run.temporal_run_id} mono />
          </div>
          <Payload title="Input" data={run.input} />
          <Payload title="Output" data={run.output} />
        </div>
      </details>
    </div>
  );
}

function Timeline({
  run,
  atGate,
  elapsed,
}: {
  run: Run;
  atGate: boolean;
  elapsed: string;
}) {
  const stages: {
    label: string;
    state: "done" | "active" | "pending" | "error";
    at?: string | null;
  }[] = [];
  stages.push({ label: "Submitted", state: "done", at: run.started_at });

  if (atGate) {
    stages.push({ label: "Executing", state: "done" });
    stages.push({ label: "Awaiting approval", state: "active" });
  } else if (run.status === "Running" || run.status === "Queued") {
    stages.push({ label: "Executing", state: "active" });
  } else {
    stages.push({ label: "Executing", state: "done" });
  }

  if (run.status === "Completed")
    stages.push({ label: "Completed", state: "done", at: run.closed_at });
  else if (TERMINAL.has(run.status))
    stages.push({ label: run.status, state: "error", at: run.closed_at });
  else stages.push({ label: "Result", state: "pending" });

  const color = {
    done: "hsl(var(--success))",
    active: "hsl(var(--flow))",
    pending: "hsl(var(--muted-foreground))",
    error: "hsl(var(--danger))",
  };

  return (
    <div className="rounded-xl border bg-card p-4">
      <div className="flex items-center">
        {stages.map((s, i) => (
          <div key={i} className="flex flex-1 items-center last:flex-none">
            <div className="flex flex-col items-center gap-1.5">
              <span
                className={cn(
                  "h-3 w-3 rounded-full",
                  s.state === "active" && "animate-pulse",
                )}
                style={{
                  backgroundColor: color[s.state],
                  boxShadow:
                    s.state === "active"
                      ? `0 0 0 4px hsl(var(--flow) / 0.15)`
                      : undefined,
                }}
              />
              <span className="whitespace-nowrap text-xs font-medium">{s.label}</span>
              <span className="h-3 text-[10px] text-muted-foreground">
                {s.at ? new Date(s.at).toLocaleTimeString() : ""}
              </span>
            </div>
            {i < stages.length - 1 && (
              <div
                className="mx-2 h-px flex-1"
                style={{
                  backgroundColor:
                    color[stages[i + 1].state === "pending" ? "pending" : s.state],
                  opacity: 0.5,
                }}
              />
            )}
          </div>
        ))}
        <div className="ml-4 whitespace-nowrap font-mono text-xs text-muted-foreground">
          {elapsed}
        </div>
      </div>
    </div>
  );
}

const KIND_STYLES: Record<WorkflowStep["kind"], { label: string; cls: string }> = {
  activity: { label: "activity", cls: "bg-accent/15 text-accent" },
  gate: { label: "approval gate", cls: "bg-warning/15 text-warning" },
  dispatch: { label: "ray dispatch", cls: "bg-flow/15 text-flow" },
};

function StepCard({
  step,
  index,
  total,
  run,
  atGate,
  onApprove,
  busy,
  replayed,
}: {
  step: WorkflowStep;
  index: number;
  total: number;
  run: Run;
  atGate: boolean;
  onApprove: () => void;
  busy: boolean;
  /** Recorded activity results a replacement worker rebuilt state from. */
  replayed: number;
}) {
  const kind = KIND_STYLES[step.kind];
  const done = !!run.output || run.status === "Completed";
  const waitingHere = atGate && step.kind === "gate";
  // Approximate: steps are a display shape, not the node graph, so this marks
  // the leading steps that were already recorded when a new worker took over.
  const isReplayed = done && index < replayed;
  const border = waitingHere
    ? "border-warning/50"
    : isReplayed
      ? "border-flow/50 bg-flow/5"
      : done
        ? "border-success/30"
        : "border-border";

  return (
    <div
      className={cn("relative flex-1 rounded-lg border p-3 transition-colors", border)}
    >
      <div className="flex items-center justify-between">
        <span className="font-mono text-[10px] text-muted-foreground">
          {String(index + 1).padStart(2, "0")}/{String(total).padStart(2, "0")}
        </span>
        {isReplayed ? (
          <span className="flex items-center gap-1 rounded bg-flow/15 px-1.5 py-0.5 text-[9px] font-medium uppercase text-flow">
            <Zap className="h-2.5 w-2.5" /> Replayed
          </span>
        ) : (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[9px] font-medium uppercase",
              kind.cls,
            )}
          >
            {kind.label}
          </span>
        )}
      </div>
      <div className="mt-2 font-mono text-sm font-medium">{step.label}</div>
      <p className="mt-1 text-xs leading-snug text-muted-foreground">{step.detail}</p>
      <div className="mt-2 flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground">
          {step.runsOn === "activity-worker"
            ? "→ activity worker"
            : "→ workflow worker"}
        </span>
        {waitingHere ? (
          <button
            onClick={onApprove}
            disabled={busy}
            className="rounded bg-warning px-2 py-0.5 text-[11px] font-medium text-background hover:opacity-90 disabled:opacity-50"
          >
            Approve
          </button>
        ) : (
          <span
            className="h-2 w-2 rounded-full"
            style={{
              backgroundColor: done
                ? "hsl(var(--success))"
                : "hsl(var(--muted-foreground))",
              opacity: done ? 1 : 0.4,
            }}
          />
        )}
      </div>
    </div>
  );
}

function Result({ run }: { run: Run }) {
  if (run.error) {
    return (
      <div className="rounded-xl border border-danger/40 bg-card p-4">
        <div className="font-mono text-[10px] uppercase tracking-wider text-danger">
          Error
        </div>
        <pre className="mt-1 whitespace-pre-wrap text-sm text-danger">{run.error}</pre>
      </div>
    );
  }
  if (!run.output) return null;

  // Nicely render the shapes we ship; fall back to JSON otherwise.
  const out = run.output;
  const compute = out.compute as Record<string, unknown> | undefined;
  const message = out.message as string | undefined;

  return (
    <div className="rounded-xl border border-success/30 bg-success/5 p-4">
      <div className="font-mono text-[10px] uppercase tracking-wider text-success">
        Result
      </div>
      {message ? (
        <p className="mt-2 text-lg font-medium">{message}</p>
      ) : compute ? (
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="ran on" value={String(compute.backend ?? "—")} />
          <Stat label="batches" value={String(compute.batches ?? "—")} />
          <Stat label="checksum" value={String(compute.checksum ?? "—")} />
          <Stat label="resumed from" value={String(compute.resumed_from ?? 0)} />
        </div>
      ) : (
        <pre className="mt-2 overflow-x-auto text-sm">
          {JSON.stringify(out, null, 2)}
        </pre>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border bg-card p-2.5">
      <div className="font-mono text-[10px] uppercase text-muted-foreground">
        {label}
      </div>
      <div className="mt-0.5 truncate text-sm font-semibold tabular-nums">{value}</div>
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
      <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </div>
      <div className={cn("mt-1 text-sm", mono && "break-all font-mono")}>{value}</div>
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
    <div className="rounded-lg border bg-card p-3">
      <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <pre className="mt-1 max-h-80 overflow-x-auto overflow-y-auto text-sm">
        {data ? JSON.stringify(data, null, 2) : "—"}
      </pre>
    </div>
  );
}

function ReplayCard({
  replay,
  busy,
  onReplay,
}: {
  replay: RunReplay | null;
  busy: boolean;
  onReplay: () => void;
}) {
  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">Deterministic replay</h3>
          <p className="mt-1 max-w-xl text-xs text-muted-foreground">
            Feed this run&apos;s recorded history back through the current workflow code.
            If every decision still lands the same way, the run would resume cleanly
            after any worker death — that is the durability guarantee, checked.
          </p>
        </div>
        <button
          onClick={onReplay}
          disabled={busy}
          className="shrink-0 rounded-md border bg-elevated px-3 py-1.5 text-sm transition-colors hover:border-border-strong disabled:opacity-50"
        >
          {busy ? "Replaying…" : "Verify replay"}
        </button>
      </div>
      {replay && (
        <div
          className={cn(
            "mt-3 flex items-start gap-2 rounded-lg border p-3 text-sm",
            replay.ok
              ? "border-success/40 bg-success/5"
              : "border-danger/40 bg-danger/5",
          )}
        >
          {replay.ok ? (
            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
          ) : (
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
          )}
          <div className="min-w-0">
            <p className={cn("font-medium", replay.ok ? "text-success" : "text-danger")}>
              {replay.ok
                ? `Determinism holds — ${replay.events_replayed} events replayed cleanly`
                : "Non-determinism detected"}
            </p>
            {replay.detail && (
              <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-[11px] text-danger">
                {replay.detail}
              </pre>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
