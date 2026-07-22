"use client";

import { useEffect, useState } from "react";

/**
 * A three-frame storyboard that makes the core promise legible to anyone:
 * a job is running → its worker is killed → a fresh worker picks up from the
 * last saved point and finishes. No jargon, no restart, no lost work.
 *
 * The frames are readable statically (a comic strip); when motion is allowed a
 * highlight walks frame 1 → 2 → 3 on a loop so the eye follows the story.
 */

const FRAMES = [
  {
    key: "run",
    step: "1",
    heading: "A job is running",
    body: "Worker A is 60% done. Every checkpoint is written to memory as it goes.",
    memory: { pct: 60, tone: "flow" as const, note: "saved: 60%" },
    worker: { name: "Worker A", pct: 60, state: "run" as const },
  },
  {
    key: "kill",
    step: "2",
    heading: "The worker is killed",
    body: "Pull the plug — crash, deploy, power loss. The memory still knows exactly where it was.",
    memory: { pct: 60, tone: "flow" as const, note: "still: 60%" },
    worker: { name: "Worker A", pct: 60, state: "dead" as const },
  },
  {
    key: "resume",
    step: "3",
    heading: "Another worker resumes",
    body: "Worker B picks up from 60% — no human, no do-over. The job finishes exactly once.",
    memory: { pct: 100, tone: "success" as const, note: "done: 100% ✓" },
    worker: { name: "Worker B", pct: 100, state: "done" as const },
  },
];

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export function ResilienceStory() {
  const reduced = useReducedMotion();
  const [active, setActive] = useState(0);

  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setActive((a) => (a + 1) % FRAMES.length), 2400);
    return () => clearInterval(t);
  }, [reduced]);

  return (
    <section className="rounded-xl border bg-card/50 p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">What “durable” means here</h3>
        <p className="text-xs text-muted-foreground">A worker dies mid-job. Watch what happens.</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {FRAMES.map((f, i) => (
          <Frame key={f.key} frame={f} highlighted={reduced || i === active} isLast={i === FRAMES.length - 1} />
        ))}
      </div>
    </section>
  );
}

function Frame({
  frame,
  highlighted,
  isLast,
}: {
  frame: (typeof FRAMES)[number];
  highlighted: boolean;
  isLast: boolean;
}) {
  const dead = frame.worker.state === "dead";
  const done = frame.worker.state === "done";
  const memColor = frame.memory.tone === "success" ? "var(--success)" : "var(--flow)";

  return (
    <div
      className="relative rounded-lg border bg-card p-3 transition-all duration-500"
      style={{
        borderColor: highlighted ? `hsl(${memColor})` : undefined,
        boxShadow: highlighted ? `0 0 0 3px hsl(${memColor} / 0.12)` : undefined,
        opacity: highlighted ? 1 : 0.62,
      }}
    >
      {/* connector chevron to the next frame */}
      {!isLast && (
        <span className="absolute -right-[11px] top-1/2 z-10 hidden -translate-y-1/2 text-muted-foreground sm:block">
          ›
        </span>
      )}

      <div className="flex items-center gap-2">
        <span
          className="flex h-5 w-5 items-center justify-center rounded-full font-mono text-[10px] font-semibold"
          style={{ backgroundColor: `hsl(${memColor} / 0.15)`, color: `hsl(${memColor})` }}
        >
          {frame.step}
        </span>
        <span className="text-sm font-semibold">{frame.heading}</span>
      </div>

      {/* Memory chip — the source of truth, always lit */}
      <div className="mt-3 rounded-md border px-2.5 py-1.5" style={{ borderColor: `hsl(${memColor} / 0.4)` }}>
        <div className="flex items-center justify-between">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">Memory</span>
          <span className="font-mono text-[10px] font-semibold" style={{ color: `hsl(${memColor})` }}>
            {frame.memory.note}
          </span>
        </div>
        <Bar pct={frame.memory.pct} color={memColor} />
      </div>

      {/* Worker chip — the disposable compute */}
      <div
        className="mt-2 rounded-md border px-2.5 py-1.5"
        style={{
          borderColor: dead ? "hsl(var(--danger) / 0.5)" : done ? "hsl(var(--success) / 0.4)" : "hsl(var(--accent) / 0.4)",
          borderStyle: dead ? "dashed" : "solid",
        }}
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{
                backgroundColor: dead
                  ? "hsl(var(--danger))"
                  : done
                    ? "hsl(var(--success))"
                    : "hsl(var(--accent))",
              }}
            />
            {frame.worker.name}
          </span>
          <span
            className="font-mono text-[10px] font-semibold"
            style={{
              color: dead ? "hsl(var(--danger))" : done ? "hsl(var(--success))" : "hsl(var(--accent))",
            }}
          >
            {dead ? "✕ killed" : done ? "✓ finished" : "● working"}
          </span>
        </div>
        <Bar
          pct={frame.worker.pct}
          color={dead ? "var(--danger)" : done ? "var(--success)" : "var(--accent)"}
          faded={dead}
        />
      </div>

      <p className="mt-2.5 text-xs leading-snug text-muted-foreground">{frame.body}</p>
    </div>
  );
}

function Bar({ pct, color, faded }: { pct: number; color: string; faded?: boolean }) {
  return (
    <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div
        className="h-full rounded-full transition-all duration-700"
        style={{
          width: `${pct}%`,
          backgroundColor: `hsl(${color})`,
          opacity: faded ? 0.4 : 1,
        }}
      />
    </div>
  );
}
