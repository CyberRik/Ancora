"use client";

/**
 * Scrub a finished (or running) run's timeline and watch the DAG replay.
 *
 * The event log the consumer projected (`run_event`) is an ordered, durable
 * record of what happened. Replaying its prefix up to any point reconstructs the
 * run's state at that instant — which node was running, which had finished — so
 * dragging the scrubber (or pressing play) animates the DAG through history
 * without touching Temporal. This is the recorded run played back, not a
 * simulation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play, RotateCcw } from "lucide-react";
import { api, type GraphNodeState, type RunHistoryEvent } from "@/lib/api";

// How an event moves the node it names.
const KIND_STATE: Record<string, GraphNodeState> = {
  "node.dispatch": "running",
  "activity.started": "running",
  "activity.completed": "completed",
  "activity.failed": "failed",
};

/** Node states implied by the events up to (and including) index `i`. */
function stateAt(events: RunHistoryEvent[], i: number): Record<string, GraphNodeState> {
  const out: Record<string, GraphNodeState> = {};
  for (let k = 0; k <= i && k < events.length; k++) {
    const ev = events[k];
    if (!ev.node_id) continue;
    // A later start after a failure is a retry in flight.
    if (ev.kind === "activity.started" && out[ev.node_id] === "failed") {
      out[ev.node_id] = "retrying";
      continue;
    }
    const s = KIND_STATE[ev.kind];
    if (s) out[ev.node_id] = s;
  }
  return out;
}

export function HistoryScrubber({
  runId,
  onScrub,
}: {
  runId: string;
  /** Node-id → state at the scrubbed instant, or null to hand back to live view. */
  onScrub: (state: Record<string, GraphNodeState> | null) => void;
}) {
  const [events, setEvents] = useState<RunHistoryEvent[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let live = true;
    api
      .getRunHistory(runId)
      .then((h) => {
        if (live) setEvents(h.events);
      })
      .catch(() => {
        if (live) setEvents([]);
      });
    return () => {
      live = false;
    };
  }, [runId]);

  const count = events?.length ?? 0;

  // Push the scrub state up. At the very end we hand back to the live view so the
  // DAG resumes reflecting the real run rather than a frozen frame.
  const emit = useCallback(
    (i: number) => {
      if (!events) return;
      onScrub(i >= events.length - 1 ? null : stateAt(events, i));
    },
    [events, onScrub],
  );

  const seek = useCallback(
    (i: number) => {
      const clamped = Math.max(0, Math.min(count - 1, i));
      setIdx(clamped);
      emit(clamped);
    },
    [count, emit],
  );

  // Playback: advance ~2 events/sec, stopping (and returning to live) at the end.
  useEffect(() => {
    if (!playing || count === 0) return;
    timer.current = setInterval(() => {
      setIdx((cur) => {
        if (cur >= count - 1) {
          setPlaying(false);
          onScrub(null);
          return cur;
        }
        const next = cur + 1;
        if (events) onScrub(next >= events.length - 1 ? null : stateAt(events, next));
        return next;
      });
    }, 500);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, count, events, onScrub]);

  // Release the scrub override when the component unmounts.
  useEffect(() => () => onScrub(null), [onScrub]);

  const current = useMemo(() => (events && count ? events[idx] : null), [events, count, idx]);

  if (events === null)
    return <div className="h-16 animate-pulse rounded-xl border bg-card" />;
  if (count === 0) return null;

  return (
    <section className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-medium">History scrubber</h3>
        <p className="font-mono text-[11px] text-muted-foreground">
          replaying the recorded event log · the graph above shows the run at this
          instant
        </p>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => {
            if (playing) {
              setPlaying(false);
            } else {
              if (idx >= count - 1) seek(0);
              setPlaying(true);
            }
          }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-elevated transition-colors hover:border-border-strong"
          aria-label={playing ? "Pause" : "Play"}
        >
          {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
        </button>
        <button
          onClick={() => {
            setPlaying(false);
            seek(0);
          }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border bg-elevated transition-colors hover:border-border-strong"
          aria-label="Restart"
        >
          <RotateCcw className="h-3.5 w-3.5" />
        </button>
        <input
          type="range"
          min={0}
          max={count - 1}
          value={idx}
          onChange={(e) => {
            setPlaying(false);
            seek(Number(e.target.value));
          }}
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-accent"
          aria-label="Scrub history"
        />
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-muted-foreground">
          {idx + 1}/{count}
        </span>
      </div>

      {current && (
        <div className="flex flex-wrap items-center gap-2 font-mono text-[11px]">
          <span className="rounded bg-muted px-1.5 py-0.5 text-muted-foreground">
            {current.kind}
          </span>
          {current.node_id && <span className="text-foreground">{current.node_id}</span>}
          {current.attempt > 1 && (
            <span className="rounded bg-warning/15 px-1 text-warning">
              attempt {current.attempt}
            </span>
          )}
          <span className="text-muted-foreground">
            {new Date(current.at).toLocaleTimeString()}
          </span>
          {current.error && <span className="text-danger">{current.error}</span>}
        </div>
      )}
    </section>
  );
}
