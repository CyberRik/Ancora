"use client";

import { useEffect, useState } from "react";

/**
 * The signature visual: work flowing through Ancora's durable pipeline.
 *
 * Two readings of the same five stages, toggled live:
 *  - "Plain English" (default) — what each stage *does*, in everyday words, so
 *    someone who has never heard of Temporal or Ray can follow the story.
 *  - "Technical" — the real component names for engineers.
 *
 * Animated packets travel the wires to make "work streaming through the system"
 * tangible; their density scales with how many runs are in flight. The memory /
 * durable-log node pulses because it is the source of truth everything else
 * recovers from. Motion is disabled under prefers-reduced-motion.
 */

export interface FlowQueue {
  queue: string;
  capability: string | null;
  live_worker_count: number;
  worker_count: number;
}

export interface SystemFlowProps {
  running: number;
  completed: number;
  waiting: number;
  liveWorkers: number;
  queues: FlowQueue[];
  connected: boolean;
}

type Mode = "plain" | "tech";

interface Labels {
  eyebrow: string;
  title: string;
  sub: string;
}

interface NodeSpec {
  id: string;
  x: number;
  accent: string; // css var name
  plain: Labels;
  tech: Labels;
}

const NODE_W = 168;
const NODE_H = 82;
const NODE_Y = 60;
const MID_Y = NODE_Y + NODE_H / 2;

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

export function SystemFlow(props: SystemFlowProps) {
  const { running, completed, waiting, liveWorkers, queues, connected } = props;
  const reduced = useReducedMotion();
  const [mode, setMode] = useState<Mode>("tech");

  const xs = [16, 220, 424, 628, 832];
  // Only the durable log carries the identity colour. Everything else is
  // structural: the alternating accents this used to have encoded nothing, and
  // the point of the picture is that one node is the source of truth the others
  // recover from.
  const QUIET = "--border-strong";
  const nodes: NodeSpec[] = [
    {
      id: "client",
      x: xs[0],
      accent: QUIET,
      plain: { eyebrow: "you ask", title: "A request", sub: "“run this job”" },
      tech: { eyebrow: "submit", title: "API gateway", sub: "REST · idempotent" },
    },
    {
      id: "log",
      x: xs[1],
      accent: "--flow",
      plain: { eyebrow: "the memory", title: "Writes it down", sub: "never forgets a step" },
      tech: { eyebrow: "source of truth", title: "Durable log", sub: "Temporal · event-sourced" },
    },
    {
      id: "orchestrator",
      x: xs[2],
      accent: QUIET,
      plain: { eyebrow: "the plan", title: "Plans the work", sub: "follows the recipe" },
      tech: { eyebrow: "orchestrate", title: "Workflow worker", sub: "deterministic replay" },
    },
    {
      id: "queues",
      x: xs[3],
      accent: QUIET,
      plain: { eyebrow: "the router", title: "Sends to a desk", sub: "fast · heavy · waiting" },
      tech: { eyebrow: "route", title: "Capability queues", sub: "cpu · gpu · io" },
    },
    {
      id: "runtime",
      x: xs[4],
      accent: QUIET,
      plain: { eyebrow: "the workers", title: "Does the work", sub: `${liveWorkers} on the job` },
      tech: { eyebrow: "execute", title: "Activity workers", sub: `${liveWorkers} live · Ray / local` },
    },
  ];

  // Packets per wire scale with in-flight work (always ≥1 so it feels alive).
  const density = connected ? Math.min(4, 1 + Math.floor(running)) : 0;
  const connectors = [0, 1, 2, 3].map((i) => {
    const from = xs[i] + NODE_W;
    const to = xs[i + 1];
    return { id: `wire-${i}`, d: `M ${from} ${MID_Y} L ${to} ${MID_Y}` };
  });

  // Result path: curves back underneath from the runtime to the client.
  const resultY = NODE_Y + NODE_H + 58;
  const resultPath = `M ${xs[4] + NODE_W / 2} ${NODE_Y + NODE_H} C ${xs[4] + NODE_W / 2} ${resultY + 30}, ${xs[0] + NODE_W / 2} ${resultY + 30}, ${xs[0] + NODE_W / 2} ${NODE_Y + NODE_H}`;

  return (
    <div className="overflow-hidden rounded-xl border bg-card/50">
      {/* header row: caption + mode toggle */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2">
        <p className="text-xs text-muted-foreground">
          {mode === "plain"
            ? "One job, left to right. Follow the moving dots."
            : "Request → durable log → orchestration → routing → execution."}
        </p>
        <div className="inline-flex rounded-md border p-0.5 text-[11px]">
          <button
            onClick={() => setMode("plain")}
            className={`rounded px-2 py-0.5 font-medium transition ${
              mode === "plain" ? "bg-flow/15 text-flow" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Plain English
          </button>
          <button
            onClick={() => setMode("tech")}
            className={`rounded px-2 py-0.5 font-medium transition ${
              mode === "tech" ? "bg-flow/15 text-flow" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            Technical
          </button>
        </div>
      </div>

      <div className="overflow-x-auto p-2">
        <div className="min-w-[900px]">
          <svg viewBox="0 0 1016 244" className="h-auto w-full" role="img" aria-label="Ancora durable execution pipeline">
            <defs>
              <linearGradient id="wireGrad" x1="0" x2="1" y1="0" y2="0">
                <stop offset="0" stopColor="hsl(var(--flow))" stopOpacity="0.15" />
                <stop offset="0.5" stopColor="hsl(var(--flow))" stopOpacity="0.55" />
                <stop offset="1" stopColor="hsl(var(--flow))" stopOpacity="0.15" />
              </linearGradient>
            </defs>

            {/* connectors */}
            {connectors.map((c) => (
              <path key={c.id} id={c.id} d={c.d} fill="none" stroke="url(#wireGrad)" strokeWidth={2} />
            ))}

            {/* result return path */}
            <path
              id="result-wire"
              d={resultPath}
              fill="none"
              stroke="hsl(var(--success))"
              strokeOpacity={0.35}
              strokeWidth={1.5}
              strokeDasharray="3 5"
            />
            <text
              x={(xs[0] + xs[4]) / 2 + NODE_W / 2}
              y={resultY + 24}
              textAnchor="middle"
              className="fill-success"
              fontSize="10"
              fontFamily="var(--font-mono, ui-monospace), monospace"
              opacity={0.75}
            >
              {mode === "plain" ? "finished answer comes back to you" : "result → caller"}
            </text>

            {/* packets */}
            {!reduced &&
              connectors.flatMap((c) =>
                Array.from({ length: density }).map((_, k) => (
                  <circle key={`${c.id}-p${k}`} r={3.5} fill="hsl(var(--flow))">
                    <animateMotion
                      dur={`${2.6 + k * 0.4}s`}
                      begin={`${k * (2.6 / Math.max(density, 1))}s`}
                      repeatCount="indefinite"
                    >
                      <mpath href={`#${c.id}`} />
                    </animateMotion>
                  </circle>
                )),
              )}
            {!reduced && completed > 0 && (
              <circle r={3.5} fill="hsl(var(--success))">
                <animateMotion dur="3.2s" repeatCount="indefinite">
                  <mpath href="#result-wire" />
                </animateMotion>
              </circle>
            )}

            {/* nodes */}
            {nodes.map((n, i) => {
              const l = mode === "plain" ? n.plain : n.tech;
              return (
                <g key={n.id}>
                  {/* pulsing ring on the memory / durable log */}
                  {n.id === "log" && !reduced && (
                    <rect
                      x={n.x}
                      y={NODE_Y}
                      width={NODE_W}
                      height={NODE_H}
                      rx={12}
                      fill="none"
                      stroke={`hsl(var(${n.accent}))`}
                      strokeWidth={1.5}
                    >
                      <animate attributeName="opacity" values="0.6;0;0.6" dur="2.8s" repeatCount="indefinite" />
                      <animate attributeName="width" values={`${NODE_W};${NODE_W + 14};${NODE_W}`} dur="2.8s" repeatCount="indefinite" />
                      <animate attributeName="height" values={`${NODE_H};${NODE_H + 14};${NODE_H}`} dur="2.8s" repeatCount="indefinite" />
                      <animate attributeName="x" values={`${n.x};${n.x - 7};${n.x}`} dur="2.8s" repeatCount="indefinite" />
                      <animate attributeName="y" values={`${NODE_Y};${NODE_Y - 7};${NODE_Y}`} dur="2.8s" repeatCount="indefinite" />
                    </rect>
                  )}
                  <rect
                    x={n.x}
                    y={NODE_Y}
                    width={NODE_W}
                    height={NODE_H}
                    rx={12}
                    fill="hsl(var(--card))"
                    stroke={`hsl(var(${n.accent}))`}
                    strokeOpacity={0.4}
                  />
                  {/* accent bar */}
                  <rect x={n.x} y={NODE_Y} width={4} height={NODE_H} rx={2} fill={`hsl(var(${n.accent}))`} />
                  <text
                    x={n.x + 16}
                    y={NODE_Y + 22}
                    className="fill-muted-foreground"
                    fontSize="9"
                    letterSpacing="1.5"
                    fontFamily="var(--font-mono, ui-monospace), monospace"
                  >
                    {`${String(i + 1).padStart(2, "0")} · ${l.eyebrow.toUpperCase()}`}
                  </text>
                  <text x={n.x + 16} y={NODE_Y + 44} className="fill-foreground" fontSize="15" fontWeight="600">
                    {l.title}
                  </text>
                  <text
                    x={n.x + 16}
                    y={NODE_Y + 62}
                    className="fill-muted-foreground"
                    fontSize="10.5"
                    fontFamily="var(--font-mono, ui-monospace), monospace"
                  >
                    {l.sub}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
      </div>

      {/* live legend */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 px-3 pb-2.5 pt-1 text-xs text-muted-foreground">
        <LegendDot color="flow" label={`${running} running`} />
        <LegendDot color="warning" label={`${waiting} awaiting approval`} />
        <LegendDot color="success" label={`${completed} completed`} />
        <span className="ml-auto font-mono text-[11px]">
          {connected ? "live · polling 2s" : "disconnected"}
        </span>
      </div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: `hsl(var(--${color}))` }} />
      {label}
    </span>
  );
}
