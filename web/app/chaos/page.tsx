"use client";

/**
 * Chaos Lab — kill a real worker and watch the run survive it.
 *
 * The claim "kill any worker mid-run and the workflow recovers" is worth very
 * little if you have to take it on faith. This page makes it something a visitor
 * can do: press the button, a container gets a real SIGKILL, and the run that was
 * mid-flight finishes anyway.
 *
 * Nothing here is simulated. The kill goes to the Docker daemon; the worker gets
 * no chance to drain or acknowledge. What recovers is the *run* — the host does
 * not restart itself, which is why "Restart" is a separate, manual button.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, Play, RotateCcw, Skull, Zap } from "lucide-react";
import {
  api,
  type ChaosStatus,
  type ChaosTarget,
  type Run,
  type RunStatus,
} from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import { cn } from "@/lib/utils";

const TERMINAL = new Set<RunStatus>([
  "Completed",
  "Failed",
  "Cancelled",
  "Terminated",
  "TimedOut",
]);

const SERVICE_COPY: Record<string, { title: string; blurb: string }> = {
  worker: {
    title: "Workflow worker",
    blurb:
      "Runs the deterministic orchestration code. Kill it and every in-flight run loses its brain — until a replacement replays history and picks up exactly where it left off.",
  },
  "activity-worker": {
    title: "Activity worker",
    blurb:
      "Runs the nodes: LLM calls, HTTP, SQL, Python. Kill it mid-node and the node is retried on a fresh worker — with the inbox guard making sure the side effect still happens exactly once.",
  },
  scheduler: {
    title: "Scheduler",
    blurb:
      "Admission control. Kill it and nothing stops: the worker's client fails open, so runs keep flowing without rate limiting rather than halting the fleet.",
  },
};

function relTime(epochSeconds: number): string {
  const secs = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function TargetCard({
  target,
  busy,
  hasActiveRun,
  onAct,
}: {
  target: ChaosTarget;
  busy: string | null;
  hasActiveRun: boolean;
  onAct: (action: "kill" | "restart", service: string) => void;
}) {
  const copy = SERVICE_COPY[target.service] ?? {
    title: target.service,
    blurb: "",
  };
  const running = target.state === "running";
  const working = busy === target.service;

  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-4 transition-colors",
        running ? "border-border" : "border-danger/50 bg-danger/5",
      )}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            running ? "bg-success" : "bg-danger animate-pulse",
          )}
        />
        <span className="font-medium">{copy.title}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">
          {target.name}
        </span>
        <span
          className={cn(
            "ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
            running ? "bg-success/15 text-success" : "bg-danger/15 text-danger",
          )}
        >
          {target.state}
        </span>
      </div>

      {copy.blurb && (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{copy.blurb}</p>
      )}

      <div className="mt-3 flex gap-2">
        <button
          onClick={() => onAct("kill", target.service)}
          disabled={!running || !target.killable || working || !hasActiveRun}
          className="inline-flex items-center gap-1.5 rounded-md bg-danger/15 px-3 py-1.5 text-sm text-danger transition-colors hover:bg-danger/25 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Skull className="h-4 w-4" />
          {working ? "Killing…" : "SIGKILL"}
        </button>
        <button
          onClick={() => onAct("restart", target.service)}
          disabled={running || working}
          className="inline-flex items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-sm transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-40"
        >
          <RotateCcw className="h-4 w-4" />
          Restart
        </button>
      </div>
      {!target.killable && (
        <p className="mt-2 text-[11px] text-muted-foreground">
          Not killable from here — it serves this page.
        </p>
      )}
    </div>
  );
}

export default function ChaosPage() {
  const [status, setStatus] = useState<ChaosStatus | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const abort = useRef<AbortController | null>(null);

  const load = useCallback(() => {
    const c = abort.current;
    api
      .chaosStatus(c?.signal)
      .then((s) => {
        setStatus(s);
        setError(null);
      })
      .catch((e) => {
        if (!c?.signal.aborted) setError(e instanceof Error ? e.message : "failed to load");
      });
    api
      .listRuns(c?.signal)
      .then((r) => setRuns(r.slice(0, 6)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    const c = new AbortController();
    abort.current = c;
    load();
    const t = setInterval(load, 2000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [load]);

  async function act(action: "kill" | "restart", service: string) {
    setBusy(service);
    setError(null);
    try {
      await api.chaosInject(action, service);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : `${action} failed`);
    } finally {
      setBusy(null);
    }
  }

  async function startVictim() {
    setStarting(true);
    setError(null);
    try {
      await api.startRun("research_agent", { topic: "chaos engineering", summaries: 3 });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start a run");
    } finally {
      setStarting(false);
    }
  }

  const live = runs.filter((r) => !TERMINAL.has(r.status));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Chaos Lab</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Kill a worker — really kill it, <code className="rounded bg-muted px-1">SIGKILL</code>,
          no drain, no warning — while a run is mid-flight, and watch the run
          finish anyway. Completed steps replay from Temporal&apos;s history
          instead of re-executing, so nothing is lost and nothing happens twice.
        </p>
      </div>

      {status && !status.enabled && (
        <div className="rounded-xl border border-warning/40 bg-warning/5 p-4">
          <div className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-warning">
            <AlertTriangle className="h-4 w-4" />
            Chaos unavailable
          </div>
          <p className="mt-1 text-sm text-muted-foreground">{status.reason}</p>
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-danger">
          {error}
        </div>
      )}

      {/* Step 1 — something to break */}
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium">1 · Give it something to lose</h3>
          <button
            onClick={startVictim}
            disabled={starting}
            className="inline-flex items-center gap-1.5 rounded-md bg-accent/15 px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-accent/25 disabled:opacity-50"
          >
            <Play className="h-4 w-4" />
            {starting ? "Starting…" : "Start a research agent"}
          </button>
        </div>
        {live.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-card/50 p-4 text-sm text-muted-foreground">
            No runs in flight. Start one above — it makes several LLM calls and
            then parks at a human gate, which gives you a wide window to kill
            something.
          </div>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {live.map((r) => (
              <Link
                key={r.id}
                href={`/runs/${r.id}`}
                className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2 transition-colors hover:bg-muted/50"
              >
                <Zap className="h-3.5 w-3.5 text-flow" />
                <span className="min-w-0 flex-1 truncate font-mono text-sm">
                  {r.workflow_name}
                </span>
                <StatusBadge status={r.status} />
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* Step 2 — break it */}
      <section className="space-y-3">
        <h3 className="text-sm font-medium">2 · Break something</h3>
        {status?.enabled && status.targets.length === 0 && (
          <div className="text-sm text-muted-foreground">No containers found.</div>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          {status?.targets.map((t) => (
            <TargetCard key={t.service} target={t} busy={busy} hasActiveRun={live.length > 0} onAct={act} />
          ))}
        </div>
        {status?.enabled && (
          <p className="max-w-3xl text-xs text-muted-foreground">
            A killed container stays down until you restart it: Docker treats a
            manual kill as intentional, so its restart policy does not fire. That
            separation is the point — the <em>run</em> recovers on its own, the
            host does not. Also expect a pause before work resumes: an activity
            that was already in flight is only rescheduled once its timeout
            elapses, because Temporal cannot tell a dead worker from a slow one
            any sooner.
          </p>
        )}
      </section>

      {/* Step 3 — the receipt */}
      {status && status.events.length > 0 && (
        <section className="space-y-3">
          <h3 className="text-sm font-medium">3 · What you broke</h3>
          <div className="rounded-lg border bg-card">
            {status.events.map((e, i) => (
              <div
                key={i}
                className="flex items-center gap-2 border-b px-3 py-2 text-sm last:border-b-0"
              >
                <span
                  className={cn(
                    "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
                    e.action === "kill"
                      ? "bg-danger/15 text-danger"
                      : "bg-success/15 text-success",
                  )}
                >
                  {e.action}
                </span>
                <span className="font-mono text-xs">{e.service}</span>
                <span className="truncate text-xs text-muted-foreground">{e.detail}</span>
                <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
                  {relTime(e.at)}
                </span>
              </div>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            Open a run and check its node list: one ledger line per node that
            actually executed. A step that survived the kill was replayed, not
            re-run — that is the exactly-once guarantee, visible as an absence of
            duplicate rows.
          </p>
        </section>
      )}
    </div>
  );
}
