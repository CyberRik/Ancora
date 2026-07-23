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
  type RunRecovery,
  type RunStatus,
} from "@/lib/api";
import { StatusBadge } from "@/components/status-badge";
import { RecoveryTimeline } from "@/components/recovery-timeline";
import { Alert, Button, Card, PageHeader } from "@/components/ui";
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
  // The run whose recovery is shown below the kill buttons. Tracked as an id so
  // it survives the run going terminal — the aftermath is the interesting part.
  const [watching, setWatching] = useState<string | null>(null);
  const [recovery, setRecovery] = useState<RunRecovery | null>(null);
  const abort = useRef<AbortController | null>(null);
  const watchingRef = useRef<string | null>(null);
  watchingRef.current = watching;

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
      .then((r) => {
        const recent = r.slice(0, 6);
        setRuns(recent);
        // Default to the newest in-flight run: it is the one about to be hit.
        if (!watchingRef.current) {
          const victim = recent.find((run) => !TERMINAL.has(run.status));
          if (victim) setWatching(victim.id);
        }
      })
      .catch(() => {});
    const id = watchingRef.current;
    if (id) {
      api
        .getRunRecovery(id, c?.signal)
        .then(setRecovery)
        .catch(() => {});
    }
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
      const started = await api.startRun("research_agent", {
        topic: "chaos engineering",
        summaries: 3,
      });
      setWatching(started.run_id);
      setRecovery(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not start a run");
    } finally {
      setStarting(false);
    }
  }

  const live = runs.filter((r) => !TERMINAL.has(r.status));
  const watched = runs.find((r) => r.id === watching) ?? null;
  const watchedTerminal = watched ? TERMINAL.has(watched.status) : false;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Prove it"
        title="Chaos Lab"
        live={live.length > 0}
        description={
          <>
            Kill a worker — really kill it,{" "}
            <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">SIGKILL</code>
            , no drain, no warning — while a run is mid-flight, and watch the run finish anyway.
            Completed steps replay from Temporal&apos;s history instead of re-executing, so nothing
            is lost and nothing happens twice.
          </>
        }
      />

      {status && !status.enabled && (
        <Alert tone="warning" icon={AlertTriangle} title="Chaos injection is off">
          {status.reason}
        </Alert>
      )}

      {error && <Alert title="That didn't work">{error}</Alert>}

      {/* Step 1 — something to break */}
      <section className="space-y-3">
        <Step
          n={1}
          title="Give it something to lose"
          action={
            <Button variant="primary" onClick={startVictim} disabled={starting}>
              <Play className="h-4 w-4" />
              {starting ? "Starting…" : "Start a research agent"}
            </Button>
          }
        />
        {live.length === 0 ? (
          <div className="rounded-lg border border-dashed bg-card/40 p-4 text-sm text-muted-foreground">
            No runs in flight. Start one above — it makes several LLM calls and then parks at a
            human gate, which gives you a wide window to kill something.
          </div>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {live.map((r) => (
              <div
                key={r.id}
                className={cn(
                  "flex items-center gap-2 rounded-lg border bg-card px-3 py-2 transition-colors",
                  r.id === watching ? "border-flow/50 bg-flow/5" : "hover:border-border-strong",
                )}
              >
                <button
                  onClick={() => {
                    setWatching(r.id);
                    setRecovery(null);
                  }}
                  className="flex min-w-0 flex-1 items-center gap-2 text-left"
                >
                  <Zap className="h-3.5 w-3.5 shrink-0 text-flow" />
                  <div className="min-w-0 flex-1 flex flex-col">
                    <span className="truncate font-mono text-sm">
                      {r.workflow_name}
                    </span>
                    <span className="text-[10px] text-muted-foreground">
                      Started {r.created_at ? relTime(new Date(r.created_at).getTime() / 1000) : "just now"}
                    </span>
                  </div>
                  <StatusBadge status={r.status} />
                </button>
                <Link
                  href={`/runs/${r.id}`}
                  className="shrink-0 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  open →
                </Link>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Step 2 — break it */}
      <section className="space-y-3">
        <Step n={2} title="Break something" />
        {status?.enabled && status.targets.length === 0 && (
          <div className="rounded-lg border border-dashed bg-card/40 p-4 text-sm text-muted-foreground">
            No containers found in this Compose project.
          </div>
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
            host does not.
          </p>
        )}
      </section>

      {/* Step 3 — the pause, explained while it is happening */}
      {watched && (
        <section className="space-y-3">
          <Step
            n={3}
            title="Watch it rebuild"
            action={
              <Link
                href={`/runs/${watched.id}`}
                className="text-xs text-muted-foreground transition-colors hover:text-foreground"
              >
                {watched.workflow_name} →
              </Link>
            }
          />
          {recovery && (recovery.spans.length > 0 || recovery.windows.length > 0) ? (
            <RecoveryTimeline data={recovery} terminal={watchedTerminal} />
          ) : (
            <div className="rounded-lg border border-dashed bg-card/50 p-4 text-sm text-muted-foreground">
              Nothing has gone wrong yet. Kill a worker above and this fills in:
              which attempt died with it, which clock has to run down before the
              server is allowed to reassign the work, and how much of the run a
              replacement rebuilt from history instead of re-executing.
            </div>
          )}
          <p className="max-w-3xl text-xs text-muted-foreground">
            The pause after a kill is not the system deciding what to do — it is
            the system refusing to guess. A worker that stopped answering and a
            worker that is merely slow are indistinguishable from the server&apos;s
            side, so it waits out the timeout that attempt was granted rather than
            risk running a node twice. Work that had already finished is replayed
            from history instantly; only the attempt that was actually in flight
            has to wait.
          </p>
        </section>
      )}

      {/* Step 4 — the receipt */}
      {status && status.events.length > 0 && (
        <section className="space-y-3">
          <Step n={4} title="What you broke" />
          <Card className="divide-y overflow-hidden">
            {status.events.map((e, i) => (
              <div key={i} className="flex items-center gap-2.5 px-3 py-2 text-sm">
                <span
                  className={cn(
                    "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wider",
                    e.action === "kill"
                      ? "border-danger/30 bg-danger/10 text-danger"
                      : "border-success/30 bg-success/10 text-success",
                  )}
                >
                  {e.action}
                </span>
                <span className="shrink-0 font-mono text-xs">{e.service}</span>
                <span className="truncate text-xs text-muted-foreground">{e.detail}</span>
                <span className="ml-auto shrink-0 font-mono text-[11px] text-muted-foreground">
                  {relTime(e.at)}
                </span>
              </div>
            ))}
          </Card>
          <p className="max-w-3xl text-xs leading-relaxed text-muted-foreground">
            Open a run and check its node list: one ledger line per node that actually executed. A
            step that survived the kill was replayed, not re-run — that is the exactly-once
            guarantee, visible as an absence of duplicate rows.
          </p>
        </section>
      )}
    </div>
  );
}

/**
 * A numbered step header. The numbering is load-bearing here — this page is a
 * procedure you work through in order, not a set of parallel panels.
 */
function Step({
  n,
  title,
  action,
}: {
  n: number;
  title: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b pb-2.5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded border border-flow/30 bg-flow/10 font-mono text-[10px] font-medium text-flow">
          {n}
        </span>
        <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
      </div>
      {action}
    </div>
  );
}
