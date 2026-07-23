"use client";

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { api, type RunActivity, type RunLive, type Run } from "@/lib/api";

/**
 * The show-off page: a real data pipeline that survives a mid-run worker failure.
 *
 * `durability_demo` runs ingest → process → export. The process step works for a
 * couple of seconds, then fails (a simulated OOM). Temporal waits a visible ~5s
 * backoff and retries; the finished ingest step is replayed from history, not
 * recomputed. The crash, the retry backoff, the attempt counter, and the failure
 * message below are Temporal's *real* state — the per-step progress bars are the
 * only visualization aid. Paced deliberately so a visitor can actually watch the
 * failure happen and heal. The page reconnects to an in-progress run too.
 */

const TERMINAL = new Set(["Completed", "Failed", "Cancelled", "Terminated", "TimedOut"]);

// Kept in sync with the workflow's activity timings (examples.py).
const BACKOFF_SECONDS = 5;
const DUR = { ingest: 3, process: 3, export: 2 };

type Phase =
  | "idle"
  | "ingesting"
  | "processing"
  | "rescheduling"
  | "recovering"
  | "exporting"
  | "completed"
  | "failed";

interface DemoEvent {
  key: string;
  tone: "flow" | "success" | "danger" | "muted" | "warning";
  time: string;
  text: string;
}

function processActivity(live: RunLive | null): RunActivity | null {
  if (!live) return null;
  return live.activities.find((a) => /process/i.test(a.activity_type)) ?? live.activities[0] ?? null;
}

export default function DemoPage() {
  const [run, setRun] = useState<Run | null>(null);
  const [live, setLive] = useState<RunLive | null>(null);
  const [events, setEvents] = useState<DemoEvent[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nowTs, setNowTs] = useState(Date.now());

  const seen = useRef<Set<string>>(new Set());
  const maxAttempt = useRef(1);
  const workers = useRef<Set<string>>(new Set());
  const crashObserved = useRef(false);
  const crashAt = useRef<number | null>(null);
  const phaseStart = useRef<{ phase: Phase; at: number }>({ phase: "idle", at: Date.now() });

  const pushEvent = useCallback((e: DemoEvent) => {
    if (seen.current.has(e.key)) return;
    seen.current.add(e.key);
    setEvents((prev) => [...prev, e]);
  }, []);

  const resetTracking = useCallback(() => {
    seen.current = new Set();
    maxAttempt.current = 1;
    workers.current = new Set();
    crashObserved.current = false;
    crashAt.current = null;
    setEvents([]);
    setLive(null);
    setError(null);
  }, []);

  const start = useCallback(async () => {
    resetTracking();
    setRun(null);
    setStarting(true);
    try {
      const res = await api.startRun("durability_demo", { message: "start", simulate_failure: true });
      const r = await api.getRun(res.run_id);
      setRun(r);
      pushEvent(now("started", "flow", "Pipeline started — a durable workflow is now running."));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start. Is the stack up on :8080?");
    } finally {
      setStarting(false);
    }
  }, [resetTracking, pushEvent]);

  // Reconnect on mount to the most recent durability_demo run.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const runs = await api.listRuns();
        if (cancelled) return;
        const demo = runs.find((r) => r.workflow_name === "durability_demo");
        if (demo) {
          setRun(demo);
          pushEvent(
            now(
              "reconnected",
              "flow",
              TERMINAL.has(demo.status)
                ? "Showing the most recent run."
                : "Reconnected to a run already in progress.",
            ),
          );
        }
      } catch {
        /* stack may be down; the Run button surfaces that */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [pushEvent]);

  const isRunning = !!run && !TERMINAL.has(run.status);

  // Smooth clock for countdown + progress while a run is active.
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(() => setNowTs(Date.now()), 150);
    return () => clearInterval(t);
  }, [isRunning]);

  // Poll real state while the run is active.
  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return;
    let stop = false;
    const tick = async () => {
      try {
        const [r, l] = await Promise.all([
          api.getRun(run.id),
          api.getRunActivities(run.id).catch(() => null),
        ]);
        if (stop) return;
        setRun(r);
        if (l) setLive(l);
        deriveEvents(r, l, { pushEvent, maxAttempt, workers, crashObserved, crashAt });
      } catch {
        /* transient — keep polling */
      }
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [run, pushEvent]);

  const act = processActivity(live);
  const attempt = Math.max(maxAttempt.current, act?.attempt ?? 1);
  const recovered = attempt >= 2 || crashObserved.current;
  const phase = derivePhase(run, live, recovered);

  // Track when each phase began (for progress bars).
  useEffect(() => {
    if (phaseStart.current.phase !== phase) {
      phaseStart.current = { phase, at: Date.now() };
    }
  }, [phase]);
  const elapsedInPhase = (nowTs - phaseStart.current.at) / 1000;

  const backoffRemaining =
    phase === "rescheduling" && crashAt.current
      ? Math.max(0, BACKOFF_SECONDS - (nowTs - crashAt.current) / 1000)
      : 0;

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="max-w-2xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-danger">
          Live failure demo
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
          A worker fails mid-run. The work survives.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          This runs a real 3-step pipeline — <span className="text-foreground">ingest → process →
          export</span>. The process step works for a moment, then{" "}
          <span className="text-foreground">fails</span> (a simulated out-of-memory crash). Temporal
          waits a real retry backoff and reruns it, replaying the finished ingest step from history
          instead of recomputing it. The crash, the backoff countdown, and the attempt counter below
          are Temporal&apos;s real state.
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-4">
        <button
          onClick={start}
          disabled={starting || isRunning}
          className="inline-flex items-center gap-2 rounded-lg bg-danger px-5 py-2.5 text-sm font-semibold text-background transition hover:opacity-90 disabled:opacity-50"
        >
          {starting ? "Starting…" : isRunning ? "Running…" : run ? "Run it again" : "Run the pipeline"}
        </button>
        {run && <StatusChip phase={phase} attempt={attempt} />}
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          {error}
        </div>
      )}

      {run && (
        <>
          {(phase === "rescheduling" || phase === "recovering") && (
            <CrashBanner
              phase={phase}
              attempt={attempt}
              remaining={backoffRemaining}
              failure={act?.last_failure ?? null}
            />
          )}

          <Stage phase={phase} attempt={attempt} live={live} elapsed={elapsedInPhase} />

          <div className="grid gap-6 md:grid-cols-2">
            <EventLog events={events} />
            <SidePanel run={run} live={live} attempt={attempt} workers={workers.current} />
          </div>

          {run.status === "Completed" && <SuccessResult run={run} attempt={attempt} />}
          {TERMINAL.has(run.status) && run.status !== "Completed" && (
            <div className="rounded-xl border border-danger/40 bg-card p-4 text-sm text-danger">
              Run ended {run.status}. {run.error}
            </div>
          )}
        </>
      )}

      {!run && !error && <IdleHint />}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Derivations
// --------------------------------------------------------------------------- //
function derivePhase(run: Run | null, live: RunLive | null, recovered: boolean): Phase {
  if (!run) return "idle";
  if (run.status === "Completed") return "completed";
  if (TERMINAL.has(run.status)) return "failed";
  const note = live?.status_note ?? "";
  const act = processActivity(live);
  // Backoff window: the step has failed and Temporal is waiting to retry it.
  if (act?.last_failure && act.state !== "Started") return "rescheduling";
  if (recovered && (act?.state === "Started" || /process/i.test(note))) return "recovering";
  if (/export/i.test(note)) return "exporting";
  if (/process/i.test(note)) return "processing";
  if (/ingest/i.test(note)) return "ingesting";
  return "ingesting";
}

function deriveEvents(
  run: Run,
  live: RunLive | null,
  refs: {
    pushEvent: (e: DemoEvent) => void;
    maxAttempt: MutableRefObject<number>;
    workers: MutableRefObject<Set<string>>;
    crashObserved: MutableRefObject<boolean>;
    crashAt: MutableRefObject<number | null>;
  },
) {
  const { pushEvent, maxAttempt, workers, crashObserved, crashAt } = refs;
  const note = live?.status_note ?? "";
  const act = processActivity(live);

  if (/ingest/i.test(note)) pushEvent(now("ingesting", "flow", "Ingesting the dataset…"));
  if (/process/i.test(note)) {
    pushEvent(now("ingest-done", "success", "Dataset ingested and checkpointed to history."));
    pushEvent(now("processing", "flow", "Processing records started (attempt 1)."));
  }
  if (act) {
    if (act.attempt > maxAttempt.current) maxAttempt.current = act.attempt;
    if (act.last_worker_identity) workers.current.add(act.last_worker_identity);
  }
  const crashed = (act?.attempt ?? 1) >= 2 || !!act?.last_failure;
  if (crashed && !crashObserved.current) {
    crashObserved.current = true;
    crashAt.current = Date.now();
    const why = act?.last_failure ? ` — ${short(act.last_failure)}` : "";
    pushEvent(now("crash", "danger", `💥 The process step failed${why}. Temporal caught it.`));
    pushEvent(now("backoff", "warning", "Waiting out the retry backoff, then rerunning the step…"));
  }
  if ((act?.attempt ?? 1) >= 2 && act?.state === "Started") {
    pushEvent(
      now("recovered", "warning", `Rerunning the step (attempt ${act?.attempt}). Ingest was not repeated.`),
    );
  }
  if (/export/i.test(note)) {
    pushEvent(now("exporting", "flow", "Records processed. Exporting the results…"));
  }
  if (run.status === "Completed") {
    const on = (run.output?.process as Record<string, unknown> | undefined)?.recovered_on_attempt;
    pushEvent(
      now("completed", "success", `Pipeline finished intact${on ? ` (recovered on attempt ${on})` : ""}. Zero data lost.`),
    );
  }
}

function now(key: string, tone: DemoEvent["tone"], text: string): DemoEvent {
  return { key, tone, text, time: new Date().toLocaleTimeString() };
}

function short(s: string): string {
  return s.length > 64 ? s.slice(0, 61) + "…" : s;
}

// --------------------------------------------------------------------------- //
// Pieces
// --------------------------------------------------------------------------- //
const PHASE_LABEL: Record<Phase, { text: string; tone: string }> = {
  idle: { text: "Ready", tone: "muted-foreground" },
  ingesting: { text: "Ingesting", tone: "flow" },
  processing: { text: "Processing", tone: "flow" },
  rescheduling: { text: "Worker failed — rescheduling", tone: "danger" },
  recovering: { text: "Rerunning the step", tone: "warning" },
  exporting: { text: "Exporting", tone: "flow" },
  completed: { text: "Survived", tone: "success" },
  failed: { text: "Failed", tone: "danger" },
};

function StatusChip({ phase, attempt }: { phase: Phase; attempt: number }) {
  const p = PHASE_LABEL[phase];
  const animated = !["idle", "completed", "failed"].includes(phase);
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-xs"
      style={{ color: `hsl(var(--${p.tone}))`, borderColor: `hsl(var(--${p.tone}) / 0.4)` }}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${animated ? "animate-pulse" : ""}`}
        style={{ backgroundColor: `hsl(var(--${p.tone}))` }}
      />
      {p.text}
      {attempt > 1 && <span className="opacity-70">· attempt {attempt}</span>}
    </span>
  );
}

function CrashBanner({
  phase,
  attempt,
  remaining,
  failure,
}: {
  phase: Phase;
  attempt: number;
  remaining: number;
  failure: string | null;
}) {
  const isBackoff = phase === "rescheduling";
  const tone = isBackoff ? "danger" : "warning";
  const pct = isBackoff ? ((BACKOFF_SECONDS - remaining) / BACKOFF_SECONDS) * 100 : 100;
  return (
    <div
      className="rounded-xl border p-4"
      style={{
        borderColor: `hsl(var(--${tone}) / 0.5)`,
        backgroundColor: `hsl(var(--${tone}) / 0.06)`,
        boxShadow: `0 0 0 3px hsl(var(--${tone}) / 0.08)`,
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-lg"
            style={{ backgroundColor: `hsl(var(--${tone}) / 0.15)` }}
          >
            {isBackoff ? "💥" : "↻"}
          </span>
          <div>
            <p className="text-sm font-semibold" style={{ color: `hsl(var(--${tone}))` }}>
              {isBackoff
                ? "A worker failed while processing"
                : `Rerunning on a healthy worker — attempt ${attempt}`}
            </p>
            <p className="text-xs text-muted-foreground">
              {isBackoff
                ? failure
                  ? short(failure)
                  : "The step crashed mid-work."
                : "The ingested data is safe in history — it is not being recomputed."}
            </p>
          </div>
        </div>
        {isBackoff && (
          <div className="text-right">
            <div className="font-mono text-2xl font-semibold tabular-nums text-danger">
              {remaining.toFixed(1)}s
            </div>
            <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              retry in
            </div>
          </div>
        )}
      </div>
      <div className="mt-3 h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-all duration-150"
          style={{ width: `${pct}%`, backgroundColor: `hsl(var(--${tone}))` }}
        />
      </div>
    </div>
  );
}

type NodeState = "pending" | "active" | "crashed" | "done";

function Stage({
  phase,
  attempt,
  live,
  elapsed,
}: {
  phase: Phase;
  attempt: number;
  live: RunLive | null;
  elapsed: number;
}) {
  const ingest: NodeState =
    phase === "idle" ? "pending" : phase === "ingesting" ? "active" : "done";
  const process: NodeState =
    phase === "idle" || phase === "ingesting"
      ? "pending"
      : phase === "rescheduling"
        ? "crashed"
        : phase === "processing" || phase === "recovering"
          ? "active"
          : "done";
  const exportState: NodeState =
    phase === "completed" ? "done" : phase === "exporting" ? "active" : "pending";

  const cap = (s: number) => Math.min(0.95, Math.max(0, s));
  const ingestPct = ingest === "done" ? 1 : ingest === "active" ? cap(elapsed / DUR.ingest) : 0;
  const processPct =
    process === "done"
      ? 1
      : process === "crashed"
        ? 0.66
        : process === "active"
          ? cap(elapsed / DUR.process)
          : 0;
  const exportPct = exportState === "done" ? 1 : exportState === "active" ? cap(elapsed / DUR.export) : 0;

  return (
    <div className="overflow-hidden rounded-xl border bg-card/50 p-5">
      <div className="grid items-stretch gap-3 sm:grid-cols-[1fr_auto_1fr_auto_1fr]">
        <StageNode label="Ingest dataset" sub="5 GB" state={ingest} tone="flow" progress={ingestPct} />
        <Connector done={ingest === "done"} />
        <StageNode
          label="Process records"
          sub={attempt > 1 ? `attempt ${attempt}` : "attempt 1"}
          state={process}
          tone={process === "crashed" ? "danger" : "flow"}
          progress={processPct}
        />
        <Connector done={process === "done"} />
        <StageNode
          label="Export results"
          sub="exactly once"
          state={exportState}
          tone="success"
          progress={exportPct}
        />
      </div>
      {live?.status_note && (
        <p className="mt-4 text-center font-mono text-xs text-muted-foreground">
          {live.status_note}
        </p>
      )}
    </div>
  );
}

function StageNode({
  label,
  sub,
  state,
  tone,
  progress,
}: {
  label: string;
  sub: string;
  state: NodeState;
  tone: "flow" | "danger" | "success";
  progress: number;
}) {
  const color =
    state === "crashed"
      ? "var(--danger)"
      : state === "done"
        ? "var(--success)"
        : state === "active"
          ? `var(--${tone})`
          : "var(--muted-foreground)";
  const active = state === "active" || state === "crashed";
  return (
    <div
      className="rounded-lg border bg-card p-4 transition-all duration-500"
      style={{
        borderColor: active || state === "done" ? `hsl(${color} / 0.5)` : undefined,
        boxShadow: active ? `0 0 0 3px hsl(${color} / 0.12)` : undefined,
        borderStyle: state === "crashed" ? "dashed" : "solid",
        opacity: state === "pending" ? 0.6 : 1,
      }}
    >
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">{label}</span>
        <span
          className={`h-2 w-2 rounded-full ${active && state !== "crashed" ? "animate-pulse" : ""}`}
          style={{ backgroundColor: `hsl(${color})` }}
        />
      </div>
      <div className="mt-1 font-mono text-[11px]" style={{ color: `hsl(${color})` }}>
        {state === "crashed"
          ? "✕ worker failed"
          : state === "done"
            ? "✓ done"
            : state === "active"
              ? "● working"
              : sub}
      </div>
      {state !== "pending" && (
        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full transition-all duration-200"
            style={{ width: `${progress * 100}%`, backgroundColor: `hsl(${color})` }}
          />
        </div>
      )}
    </div>
  );
}

function Connector({ done }: { done: boolean }) {
  return (
    <div className="hidden items-center sm:flex">
      <div
        className="h-px w-8 transition-colors duration-500"
        style={{ backgroundColor: done ? "hsl(var(--success))" : "hsl(var(--border))" }}
      />
    </div>
  );
}

function EventLog({ events }: { events: DemoEvent[] }) {
  return (
    <div className="rounded-xl border bg-card p-4">
      <h3 className="mb-3 font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        Live event log
      </h3>
      <ol className="space-y-2.5">
        {events.length === 0 && (
          <li className="text-sm text-muted-foreground">Waiting for the first event…</li>
        )}
        {events.map((e) => (
          <li key={e.key} className="flex gap-2.5 text-sm">
            <span
              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: `hsl(var(--${e.tone}))` }}
            />
            <div>
              <span className="font-mono text-[10px] text-muted-foreground">{e.time}</span>
              <p style={{ color: e.tone === "muted" ? undefined : `hsl(var(--${e.tone}))` }}>
                {e.text}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SidePanel({
  run,
  live,
  attempt,
  workers,
}: {
  run: Run;
  live: RunLive | null;
  attempt: number;
  workers: Set<string>;
}) {
  const act = processActivity(live);
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <h3 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        Real Temporal state
      </h3>
      <Metric label="Run status" value={run.status} />
      <Metric label="Process attempt" value={String(attempt)} highlight={attempt > 1} />
      <Metric
        label="Activity state"
        value={act ? act.state : run.status === "Completed" ? "—" : "scheduling…"}
      />
      {act?.last_failure && (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-2">
          <div className="font-mono text-[10px] uppercase text-danger">Last failure</div>
          <div className="mt-0.5 break-words text-xs text-muted-foreground">{act.last_failure}</div>
        </div>
      )}
      {workers.size > 0 && (
        <div className="text-[11px] text-muted-foreground">
          worker: <span className="font-mono">{[...workers].join(", ")}</span>
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="flex items-center justify-between border-b border-border/50 pb-2 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span
        className="font-mono tabular-nums"
        style={{ color: highlight ? "hsl(var(--warning))" : undefined }}
      >
        {value}
      </span>
    </div>
  );
}

function SuccessResult({ run, attempt }: { run: Run; attempt: number }) {
  return (
    <div className="rounded-xl border border-success/40 bg-success/5 p-5">
      <p className="font-mono text-[11px] uppercase tracking-wider text-success">
        Recovery successful
      </p>
      <p className="mt-2 text-lg font-medium">
        A worker failed mid-run{attempt > 1 ? ` (recovered on attempt ${attempt})` : ""} — and the
        pipeline still finished, exactly once.
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        The ingest step ran before the failure; its result was replayed from history rather than
        recomputed. No lost state, no duplicated work.
      </p>
      <pre className="mt-3 max-h-80 overflow-y-auto overflow-x-auto rounded-md bg-background/60 p-3 text-[11px] text-muted-foreground">
        {JSON.stringify(run.output, null, 2)}
      </pre>
    </div>
  );
}

function IdleHint() {
  return (
    <div className="rounded-xl border border-dashed bg-card/40 p-8 text-center">
      <p className="text-sm text-muted-foreground">
        Press <span className="font-medium text-danger">Run the pipeline</span> to start a real
        durable workflow and watch a worker fail and recover — live.
      </p>
    </div>
  );
}
