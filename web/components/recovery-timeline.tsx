"use client";

/**
 * What a worker death did to a run, drawn on a time axis.
 *
 * A killed worker does not make a run fail, but it does make it *pause*, and the
 * pause is what people misread: "nothing is happening" and "it broke" look
 * identical from outside. A list of events cannot fix that, because the pause is
 * an *absence* of events — a list renders it as whitespace. So this is a Gantt
 * chart: the gap has width, and the width is the explanation.
 *
 * Three things are drawn, in the order a confused visitor needs them:
 *
 *   1. the clock the run is waiting on right now, counting down;
 *   2. every attempt of every node on one shared axis, including the attempt
 *      that died with its worker (which Temporal never wrote down — see
 *      recovery.py for why that bar is a bound, not a measurement);
 *   3. the handoff: a different process picked the run up and rebuilt state from
 *      recorded results instead of re-running them.
 *
 * The countdown ticks locally but is anchored to the server's clock on every
 * poll, so a browser with a skewed clock does not invent or erase time.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  Clock,
  HeartPulse,
  Hourglass,
  RotateCcw,
  Skull,
  Timer,
  Zap,
} from "lucide-react";
import {
  type RecoveryMarker,
  type RecoverySpan,
  type RecoveryWindow,
  type RunRecovery,
  type SpanOutcome,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const TICK_MS = 100;

// Bar styling per outcome. `lost` is deliberately the loudest thing on the
// chart: it is the interval the visitor is trying to understand.
const SPAN_STYLE: Record<SpanOutcome, { bar: string; label: string }> = {
  completed: { bar: "bg-success/70", label: "ran" },
  running: { bar: "bg-flow/70 animate-pulse", label: "running" },
  failed: { bar: "bg-danger/70", label: "failed" },
  timed_out: { bar: "bg-danger/60", label: "timed out" },
  canceled: { bar: "bg-muted-foreground/40", label: "cancelled" },
  queued: { bar: "bg-muted-foreground/25", label: "queued" },
  lost: { bar: "bg-danger/25", label: "lost with its worker" },
};

const WINDOW_COPY: Record<
  RecoveryWindow["kind"],
  { title: string; icon: typeof Clock; tone: string }
> = {
  detecting: {
    title: "Waiting out the detection timeout",
    icon: Hourglass,
    tone: "warning",
  },
  backoff: { title: "Retry backoff", icon: Timer, tone: "warning" },
  queued: { title: "Queued — no worker polling", icon: Clock, tone: "flow" },
  workflow_task: { title: "Orchestration step unanswered", icon: Activity, tone: "warning" },
};

function secs(value: number): string {
  if (!Number.isFinite(value)) return "—";
  const v = Math.max(0, value);
  if (v < 60) return `${v.toFixed(v < 10 ? 1 : 0)}s`;
  const m = Math.floor(v / 60);
  const s = Math.round(v % 60);
  return s === 0 ? `${m}m` : `${m}m ${s}s`;
}

function ms(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

/**
 * The live countdown.
 *
 * This is the single most important thing on the page: it converts "it's stuck"
 * into "it resumes in 3m 41s, and here is the timeout that decided that". The
 * bar fills as the timeout is consumed, so a heartbeat-bounded node visibly
 * moves while a start-to-close-bounded one visibly crawls.
 */
function WaitPanel({ window, nowMs }: { window: RecoveryWindow; nowMs: number }) {
  const copy = WINDOW_COPY[window.kind];
  const Icon = copy.icon;
  const startMs = ms(window.started_at);
  const deadlineMs = ms(window.deadline_at);

  const elapsed = startMs ? (nowMs - startMs) / 1000 : window.elapsed_seconds;
  const remaining = deadlineMs ? (deadlineMs - nowMs) / 1000 : null;
  const total = window.timeout_seconds;
  const pct = total && total > 0 ? Math.min(100, Math.max(0, (elapsed / total) * 100)) : null;
  const expired = remaining !== null && remaining <= 0;

  return (
    <div
      className={cn(
        "rounded-xl border p-4",
        window.kind === "queued"
          ? "border-flow/40 bg-flow/5"
          : "border-warning/40 bg-warning/5",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Icon
          className={cn(
            "h-4 w-4",
            window.kind === "queued" ? "text-flow" : "text-warning",
          )}
        />
        <span className="text-sm font-medium">{copy.title}</span>
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
          {window.node_id}
        </code>
        {window.attempt > 1 && (
          <span className="text-[11px] text-muted-foreground">attempt {window.attempt}</span>
        )}
        {window.worker && (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
              window.worker_state === "live"
                ? "bg-success/15 text-success"
                : window.worker_state === "unknown"
                  ? "bg-muted text-muted-foreground"
                  : "bg-danger/15 text-danger",
            )}
          >
            {window.worker_state === "replaced"
              ? "worker replaced"
              : window.worker_state === "gone"
                ? "worker gone"
                : window.worker_state}
          </span>
        )}
      </div>

      {total !== null && total > 0 ? (
        <>
          <div className="mt-3 flex items-baseline justify-between gap-2">
            <span className="font-mono text-2xl tabular-nums">
              {expired ? "any moment now" : secs(remaining ?? 0)}
            </span>
            <span className="text-xs text-muted-foreground">
              {secs(elapsed)} of {secs(total)}{" "}
              {window.clock === "heartbeat"
                ? "heartbeat timeout"
                : window.clock === "retry_backoff"
                  ? "backoff"
                  : "start-to-close"}
            </span>
          </div>
          <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
            <div
              className={cn(
                "h-full rounded-full transition-[width] duration-100 ease-linear",
                expired ? "bg-danger" : "bg-warning",
              )}
              style={{ width: `${pct ?? 0}%` }}
            />
          </div>
        </>
      ) : (
        <div className="mt-3 font-mono text-2xl tabular-nums">
          {secs(elapsed)}
          <span className="ml-2 align-middle text-xs font-sans text-muted-foreground">
            waiting — no timeout to burn
          </span>
        </div>
      )}

      <p className="mt-3 text-xs leading-relaxed text-muted-foreground">{window.reason}</p>

      {window.kind === "detecting" && window.heartbeat_timeout_seconds === null && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          Nothing is lost while this runs down — the work is recorded and will be
          reassigned. The wait is the price of not double-executing a node that
          might still be alive on a worker that is merely slow.
        </p>
      )}
    </div>
  );
}

interface Axis {
  start: number;
  end: number;
  span: number;
}

function pos(axis: Axis, t: number): number {
  return ((t - axis.start) / axis.span) * 100;
}

/** One node's row: every attempt of that node, laid out on the shared axis. */
function SpanRow({
  nodeId,
  spans,
  axis,
  nowMs,
  onHover,
}: {
  nodeId: string;
  spans: RecoverySpan[];
  axis: Axis;
  nowMs: number;
  onHover: (span: RecoverySpan | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="w-28 shrink-0 truncate text-right font-mono text-[11px] text-muted-foreground">
        {nodeId}
      </div>
      <div className="relative h-6 flex-1 rounded bg-muted/40">
        {spans.map((s, i) => {
          const from = ms(s.started_at);
          if (from === null) return null;
          const to = ms(s.ended_at) ?? nowMs;
          const left = pos(axis, from);
          // Sub-second bars would be invisible; floor the width so a fast node
          // still reads as "it ran" rather than vanishing.
          const width = Math.max(0.8, pos(axis, to) - left);
          const style = SPAN_STYLE[s.outcome];
          return (
            <div
              key={i}
              onMouseEnter={() => onHover(s)}
              onMouseLeave={() => onHover(null)}
              className={cn(
                "absolute top-1 h-4 rounded-sm",
                style.bar,
                // A reconstructed bound is drawn as an outline: the runtime knows
                // this interval happened but not exactly when it began.
                s.approximate && "border border-dashed border-danger/70",
              )}
              style={{ left: `${left}%`, width: `${width}%` }}
              title={`${s.node_id} · attempt ${s.attempt} · ${style.label}${
                s.worker ? ` · ${s.worker}` : ""
              }`}
            />
          );
        })}
      </div>
    </div>
  );
}

function MarkerLines({ markers, axis }: { markers: RecoveryMarker[]; axis: Axis }) {
  return (
    <>
      {markers.map((m, i) => {
        const t = ms(m.at);
        if (t === null) return null;
        const left = pos(axis, t);
        if (left < 0 || left > 100) return null;
        const isKill = m.kind === "kill";
        return (
          <div
            key={i}
            className="pointer-events-none absolute inset-y-0"
            style={{ left: `${left}%` }}
          >
            <div
              className={cn(
                "h-full w-px",
                isKill ? "bg-danger" : m.kind === "restart" ? "bg-success" : "bg-accent/60",
                m.kind === "worker_changed" && "border-l border-dashed border-accent/70 bg-transparent",
              )}
            />
          </div>
        );
      })}
    </>
  );
}

/**
 * The recovery view is driven by the run page's poll rather than its own: the
 * endpoint reads a whole workflow history, and the step cards need the same
 * answer, so polling it twice would double that cost for one screen.
 */
export function RecoveryTimeline({
  data,
  terminal,
}: {
  data: RunRecovery | null;
  terminal: boolean;
}) {
  const [hover, setHover] = useState<RecoverySpan | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  // Anchor the countdown to the server clock, re-taken on every poll, so a
  // browser whose clock is minutes off does not invent or erase remaining time.
  const anchor = useMemo(() => {
    const server = ms(data?.now ?? null);
    return server === null ? null : { server, local: Date.now() };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.now]);

  useEffect(() => {
    const tick = () =>
      setNowMs(anchor ? anchor.server + (Date.now() - anchor.local) : Date.now());
    tick();
    if (terminal) return;
    const t = setInterval(tick, TICK_MS);
    return () => clearInterval(t);
  }, [anchor, terminal]);

  const rows = useMemo(() => {
    const byNode = new Map<string, RecoverySpan[]>();
    for (const s of data?.spans ?? []) {
      const list = byNode.get(s.node_id);
      if (list) list.push(s);
      else byNode.set(s.node_id, [s]);
    }
    return [...byNode.entries()];
  }, [data]);

  const axis = useMemo<Axis | null>(() => {
    const times: number[] = [];
    for (const s of data?.spans ?? []) {
      const a = ms(s.started_at);
      const b = ms(s.ended_at);
      if (a !== null) times.push(a);
      if (b !== null) times.push(b);
    }
    for (const m of data?.markers ?? []) {
      const t = ms(m.at);
      // Injections are process-wide, so one from a previous experiment can sit
      // far outside this run. Markers extend the axis only within its own range.
      if (t !== null && times.length > 0 && t >= Math.min(...times)) times.push(t);
    }
    if (times.length === 0) return null;
    const start = Math.min(...times);
    // Only follow the clock while something is actually pending. A run parked at
    // a human gate can idle for hours, and letting `now` set the right edge would
    // crush the work that is being explained into a sliver at the far left.
    const pending =
      !terminal &&
      ((data?.windows.length ?? 0) > 0 ||
        (data?.spans ?? []).some((s) => s.outcome === "running"));
    const end = Math.max(...times, pending ? nowMs : start);
    // Guard against a zero-width axis on a run that has barely started.
    const span = Math.max(1000, end - start);
    return { start, end: start + span, span };
  }, [data, nowMs, terminal]);

  if (!data || rows.length === 0) return null;

  const window = data.windows[0];
  const total = axis ? axis.span / 1000 : 0;
  const lost = data.spans.filter((s) => s.outcome === "lost");
  const stalled = data.spans.filter((s) => s.outcome === "queued");
  const hadTrouble =
    lost.length > 0 || stalled.length > 0 || data.handoffs > 0 || window !== undefined;

  if (!hadTrouble) return null;

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">Recovery</h3>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          {data.handoffs > 0 && (
            <span className="inline-flex items-center gap-1">
              <RotateCcw className="h-3.5 w-3.5" />
              {data.handoffs} {data.handoffs === 1 ? "handoff" : "handoffs"}
            </span>
          )}
          {data.replayed_activities > 0 && (
            <span className="inline-flex items-center gap-1 text-success">
              <Zap className="h-3.5 w-3.5" />
              {data.replayed_activities} replayed, not re-run
            </span>
          )}
          {lost.length > 0 && (
            <span className="inline-flex items-center gap-1 text-danger">
              <Skull className="h-3.5 w-3.5" />
              {lost.length} lost {lost.length === 1 ? "attempt" : "attempts"}
            </span>
          )}
        </div>
      </div>

      {window && <WaitPanel window={window} nowMs={nowMs} />}

      {axis && (
        <div className="rounded-xl border bg-card p-4">
          <div className="relative space-y-1.5">
            {/* Marker lines sit behind the bars so a kill reads as a moment in
                time that the bars are drawn across, not as another row. */}
            <div className="pointer-events-none absolute inset-y-0 left-[7.5rem] right-0">
              <MarkerLines markers={data.markers} axis={axis} />
            </div>
            {rows.map(([nodeId, spans]) => (
              <SpanRow
                key={nodeId}
                nodeId={nodeId}
                spans={spans}
                axis={axis}
                nowMs={nowMs}
                onHover={setHover}
              />
            ))}
          </div>

          <div className="mt-2 flex items-center gap-2 pl-[7.5rem] text-[10px] text-muted-foreground">
            <span>0s</span>
            <span className="flex-1 border-t border-dashed" />
            <span>{secs(total)}</span>
          </div>

          {/* Only legend what is actually on the chart — an entry for something
              absent reads as a thing the viewer failed to find. */}
          <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-4 rounded-sm bg-success/70" /> ran
            </span>
            {data.spans.some((s) => s.outcome === "running") && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-4 rounded-sm bg-flow/70" /> in flight
              </span>
            )}
            {lost.length > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-4 rounded-sm border border-dashed border-danger/70 bg-danger/25" />
                lost with its worker
              </span>
            )}
            {stalled.length > 0 && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-2 w-4 rounded-sm bg-muted-foreground/25" /> queued, no worker
              </span>
            )}
            {data.markers.some((m) => m.kind === "kill") && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-3 w-px bg-danger" /> kill
              </span>
            )}
            {data.markers.some((m) => m.kind === "restart") && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-3 w-px bg-success" /> restart
              </span>
            )}
            {data.markers.some((m) => m.kind === "worker_changed") && (
              <span className="inline-flex items-center gap-1.5">
                <span className="h-3 border-l border-dashed border-accent/70" /> new worker
              </span>
            )}
          </div>

          {hover && (
            <div className="mt-3 rounded-lg border bg-muted/30 p-2.5 text-xs">
              <span className="font-mono">{hover.node_id}</span>
              <span className="text-muted-foreground">
                {" · "}attempt {hover.attempt} · {SPAN_STYLE[hover.outcome].label}
                {hover.worker ? ` · ${hover.worker}` : ""}
              </span>
              {hover.approximate && (
                <p className="mt-1 text-muted-foreground">
                  Reconstructed. Temporal does not record the start of an attempt
                  that dies with its worker, so this bar is bounded by the
                  schedule and the next attempt rather than measured.
                </p>
              )}
              {hover.failure && (
                <p className="mt-1 font-mono text-[11px] text-danger">{hover.failure}</p>
              )}
            </div>
          )}
        </div>
      )}

      {data.markers
        .filter((m) => m.kind === "worker_changed")
        .map((m, i) => (
          <div
            key={i}
            className="flex flex-wrap items-center gap-2 rounded-lg border border-success/40 bg-success/5 px-3 py-2 text-xs"
          >
            <HeartPulse className="h-3.5 w-3.5 shrink-0 text-success" />
            <span className="font-mono text-[11px]">{m.label}</span>
            <span className="text-muted-foreground">{m.detail}</span>
          </div>
        ))}
    </section>
  );
}
