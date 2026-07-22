"use client";

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { api, type RunActivity, type RunLive, type Run } from "@/lib/api";

/**
 * The show-off page: a *real* worker crash and recovery.
 *
 * Clicking "Run the crash" starts the `durability_demo` workflow, whose GPU
 * training step kills its own worker process (os._exit) on the first attempt.
 * Everything on screen is driven by real Temporal state pulled from the API —
 * the attempt counter ticking 1 → 2, the real failure message, the worker
 * identity changing — so a visitor sees an actual failure survive, not a canned
 * animation.
 */

const TERMINAL = new Set(["Completed", "Failed", "Cancelled", "Terminated", "TimedOut"]);

type Phase = "idle" | "downloading" | "training" | "crashed" | "recovering" | "completed" | "failed";

interface DemoEvent {
  key: string;
  tone: "flow" | "success" | "danger" | "muted" | "warning";
  time: string;
  text: string;
}

function trainingActivity(live: RunLive | null): RunActivity | null {
  if (!live) return null;
  return (
    live.activities.find((a) => /train/i.test(a.activity_type)) ?? live.activities[0] ?? null
  );
}

export default function DemoPage() {
  const [run, setRun] = useState<Run | null>(null);
  const [live, setLive] = useState<RunLive | null>(null);
  const [events, setEvents] = useState<DemoEvent[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Sticky facts observed across polls (so the narrative doesn't flicker).
  const seen = useRef<Set<string>>(new Set());
  const maxAttempt = useRef(1);
  const workers = useRef<Set<string>>(new Set());
  const crashObserved = useRef(false);

  const pushEvent = useCallback((e: DemoEvent) => {
    if (seen.current.has(e.key)) return;
    seen.current.add(e.key);
    setEvents((prev) => [...prev, e]);
  }, []);

  const reset = useCallback(() => {
    seen.current = new Set();
    maxAttempt.current = 1;
    workers.current = new Set();
    crashObserved.current = false;
    setEvents([]);
    setLive(null);
    setRun(null);
    setError(null);
  }, []);

  const start = useCallback(async () => {
    reset();
    setStarting(true);
    try {
      const res = await api.startRun("durability_demo", { message: "start" });
      const r = await api.getRun(res.run_id);
      setRun(r);
      pushEvent(now("started", "flow", "Pipeline started — a durable workflow is now running."));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start. Is the stack up on :8080?");
    } finally {
      setStarting(false);
    }
  }, [reset, pushEvent]);

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
        deriveEvents(r, l, { pushEvent, maxAttempt, workers, crashObserved });
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

  const act = trainingActivity(live);
  const attempt = Math.max(maxAttempt.current, act?.attempt ?? 1);
  const recovered = attempt >= 2 || crashObserved.current;
  const phase = derivePhase(run, live, recovered);
  const isRunning = !!run && !TERMINAL.has(run.status);

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      {/* Hero */}
      <header className="max-w-2xl">
        <p className="font-mono text-[11px] uppercase tracking-[0.2em] text-danger">
          Live failure demo
        </p>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
          Kill the worker. The work survives.
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
          This runs a real workflow. Halfway through, the training step{" "}
          <span className="text-foreground">crashes its own worker process</span> — an actual
          <code className="mx-1 rounded bg-muted px-1 text-foreground">os._exit(1)</code>. Temporal
          notices the death, reschedules onto a fresh worker, and finishes from the last checkpoint.
          Nothing below is animated on a timer — it&apos;s Temporal&apos;s real state.
        </p>
      </header>

      {/* Action */}
      <div className="flex flex-wrap items-center gap-4">
        <button
          onClick={start}
          disabled={starting || isRunning}
          className="inline-flex items-center gap-2 rounded-lg bg-danger px-5 py-2.5 text-sm font-semibold text-background transition hover:opacity-90 disabled:opacity-50"
        >
          {starting ? "Starting…" : isRunning ? "Running…" : run ? "Run it again" : "Run the crash"}
        </button>
        {run && <StatusChip phase={phase} attempt={attempt} />}
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          {error}
        </div>
      )}

      {/* The stage */}
      {run && (
        <>
          <Stage phase={phase} attempt={attempt} live={live} />
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
  const act = trainingActivity(live);
  const failing = !!act?.last_failure || (act ? act.state === "Scheduled" && act.attempt >= 2 : false);
  if (failing && recovered && (act?.state ?? "") !== "Started") return "crashed";
  if (recovered) return "recovering";
  if (/train/i.test(note)) return "training";
  if (/download/i.test(note)) return "downloading";
  return "training";
}

function deriveEvents(
  run: Run,
  live: RunLive | null,
  refs: {
    pushEvent: (e: DemoEvent) => void;
    maxAttempt: MutableRefObject<number>;
    workers: MutableRefObject<Set<string>>;
    crashObserved: MutableRefObject<boolean>;
  },
) {
  const { pushEvent, maxAttempt, workers, crashObserved } = refs;
  const note = live?.status_note ?? "";
  const act = trainingActivity(live);

  if (/download/i.test(note)) pushEvent(now("downloading", "flow", "Downloading the dataset…"));
  if (/train/i.test(note)) {
    pushEvent(now("download-done", "success", "Dataset downloaded and checkpointed to history."));
    pushEvent(now("training", "flow", "GPU training started (attempt 1)."));
  }
  if (act) {
    if (act.attempt > maxAttempt.current) maxAttempt.current = act.attempt;
    if (act.last_worker_identity) workers.current.add(act.last_worker_identity);
  }
  const crashed = (act?.attempt ?? 1) >= 2 || !!act?.last_failure;
  if (crashed && !crashObserved.current) {
    crashObserved.current = true;
    const why = act?.last_failure ? ` (${short(act.last_failure)})` : " — heartbeat stopped";
    pushEvent(now("crash", "danger", `💥 Worker process died${why}. Temporal detected it.`));
  }
  if ((act?.attempt ?? 1) >= 2) {
    pushEvent(
      now(
        "recovered",
        "warning",
        `Rescheduled onto a fresh worker — resuming at attempt ${act?.attempt}. The download was not re-run.`,
      ),
    );
  }
  if (run.status === "Completed") {
    const on = (run.output?.training as Record<string, unknown> | undefined)?.recovered_on_attempt;
    pushEvent(
      now("completed", "success", `Completed — the work finished intact${on ? ` on attempt ${on}` : ""}. Zero data lost.`),
    );
  }
}

function now(key: string, tone: DemoEvent["tone"], text: string): DemoEvent {
  return { key, tone, text, time: new Date().toLocaleTimeString() };
}

function short(s: string): string {
  return s.length > 60 ? s.slice(0, 57) + "…" : s;
}

// --------------------------------------------------------------------------- //
// Pieces
// --------------------------------------------------------------------------- //
const PHASE_LABEL: Record<Phase, { text: string; tone: string }> = {
  idle: { text: "Ready", tone: "muted-foreground" },
  downloading: { text: "Downloading", tone: "flow" },
  training: { text: "Training", tone: "flow" },
  crashed: { text: "Worker died — recovering", tone: "danger" },
  recovering: { text: "Resumed on new worker", tone: "warning" },
  completed: { text: "Survived", tone: "success" },
  failed: { text: "Failed", tone: "danger" },
};

function StatusChip({ phase, attempt }: { phase: Phase; attempt: number }) {
  const p = PHASE_LABEL[phase];
  return (
    <span
      className="inline-flex items-center gap-2 rounded-full border px-3 py-1 font-mono text-xs"
      style={{ color: `hsl(var(--${p.tone}))`, borderColor: `hsl(var(--${p.tone}) / 0.4)` }}
    >
      <span
        className={`h-1.5 w-1.5 rounded-full ${phase === "crashed" || phase === "training" || phase === "downloading" || phase === "recovering" ? "animate-pulse" : ""}`}
        style={{ backgroundColor: `hsl(var(--${p.tone}))` }}
      />
      {p.text}
      {attempt > 1 && <span className="opacity-70">· attempt {attempt}</span>}
    </span>
  );
}

function Stage({ phase, attempt, live }: { phase: Phase; attempt: number; live: RunLive | null }) {
  const downloadDone = phase !== "downloading" && phase !== "idle";
  const trainState: "pending" | "active" | "crashed" | "done" =
    phase === "completed"
      ? "done"
      : phase === "crashed"
        ? "crashed"
        : phase === "training" || phase === "recovering"
          ? "active"
          : "pending";

  return (
    <div className="overflow-hidden rounded-xl border bg-card/50 p-5">
      <div className="grid items-stretch gap-3 sm:grid-cols-[1fr_auto_1fr_auto_1fr]">
        <StageNode
          label="Download dataset"
          sub="5 GB"
          state={downloadDone ? "done" : phase === "downloading" ? "active" : "pending"}
          tone="flow"
        />
        <Connector done={downloadDone} />
        <StageNode
          label="Train model (GPU)"
          sub={attempt > 1 ? `attempt ${attempt}` : "attempt 1"}
          state={trainState}
          tone={trainState === "crashed" ? "danger" : "flow"}
        />
        <Connector done={phase === "completed"} />
        <StageNode
          label="Finish"
          sub="exactly once"
          state={phase === "completed" ? "done" : "pending"}
          tone="success"
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
}: {
  label: string;
  sub: string;
  state: "pending" | "active" | "crashed" | "done";
  tone: "flow" | "danger" | "success";
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
          className={`h-2 w-2 rounded-full ${active ? "animate-pulse" : ""}`}
          style={{ backgroundColor: `hsl(${color})` }}
        />
      </div>
      <div className="mt-1 font-mono text-[11px]" style={{ color: `hsl(${color})` }}>
        {state === "crashed"
          ? "✕ worker died"
          : state === "done"
            ? "✓ done"
            : state === "active"
              ? "● working"
              : sub}
      </div>
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
  const act = trainingActivity(live);
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <h3 className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground">
        Real Temporal state
      </h3>
      <Metric label="Run status" value={run.status} />
      <Metric label="Training attempt" value={String(attempt)} highlight={attempt > 1} />
      <Metric
        label="Activity state"
        value={act ? act.state : run.status === "Completed" ? "—" : "scheduling…"}
      />
      <Metric label="Worker instances seen" value={String(Math.max(workers.size, 1))} highlight={workers.size > 1} />
      {act?.last_failure && (
        <div className="rounded-md border border-danger/30 bg-danger/5 p-2">
          <div className="font-mono text-[10px] uppercase text-danger">Last failure</div>
          <div className="mt-0.5 break-words text-xs text-muted-foreground">{act.last_failure}</div>
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
        A worker died mid-run{attempt > 1 ? ` (recovered on attempt ${attempt})` : ""} — and the
        workflow still finished, exactly once.
      </p>
      <p className="mt-1 text-sm text-muted-foreground">
        The download step ran before the crash; its result was replayed from history rather than
        recomputed. No lost state, no duplicated work.
      </p>
      <pre className="mt-3 overflow-x-auto rounded-md bg-background/60 p-3 text-[11px] text-muted-foreground">
        {JSON.stringify(run.output, null, 2)}
      </pre>
    </div>
  );
}

function IdleHint() {
  return (
    <div className="rounded-xl border border-dashed bg-card/40 p-8 text-center">
      <p className="text-sm text-muted-foreground">
        Press <span className="font-medium text-danger">Run the crash</span> to start a real durable
        workflow and watch a worker die and recover — live.
      </p>
    </div>
  );
}
