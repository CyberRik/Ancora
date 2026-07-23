"use client";

/**
 * The DAG a run actually executed, with live node states.
 *
 * The graph is *reconstructed*, not declared. A workflow is ordinary Python that
 * decides step by step what to schedule next, so the fan-out width comes from
 * the input and the tail depends on which branch the run took — there is no
 * static picture that is true for every run. The server reads Temporal's history
 * and returns the shape this run really had (see `graph.py`).
 *
 * Two consequences shape this component:
 *
 *   1. **Layers are exact, positions are ours.** Every activity's scheduled
 *      event names the workflow task that commanded it, so vertices in the same
 *      column were decided on together and can run concurrently. That makes a
 *      layout library unnecessary: column = layer, row = position within it, and
 *      the result is stable across polls instead of drifting the way a force
 *      layout would.
 *   2. **The graph grows.** A run parked at its gate has not yet decided what
 *      comes after, so nodes appear as the workflow commits to them. The counter
 *      says "of N so far" rather than implying the denominator is final.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  type Edge,
  type Node,
  type NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  Ban,
  Check,
  CircleDashed,
  Hourglass,
  Loader2,
  RotateCcw,
  TriangleAlert,
  UserCheck,
  X,
} from "lucide-react";
import type { GraphNode, GraphNodeState, RunGraph } from "@/lib/api";
import { cn } from "@/lib/utils";

// Column pitch has to clear the widest node box plus the arrowhead; row pitch
// only has to clear the box, so a fan-out stays compact.
const COL = 232;
const ROW = 104;
const NODE_W = 176;

type StateStyle = {
  ring: string;
  dot: string;
  text: string;
  label: string;
  icon: typeof Check;
  spin?: boolean;
};

const STATE: Record<GraphNodeState, StateStyle> = {
  completed: {
    ring: "border-success/50 bg-success/5",
    dot: "bg-success",
    text: "text-success",
    label: "done",
    icon: Check,
  },
  running: {
    ring: "border-flow/60 bg-flow/10",
    dot: "bg-flow",
    text: "text-flow",
    label: "running",
    icon: Loader2,
    spin: true,
  },
  retrying: {
    ring: "border-warning/60 bg-warning/10",
    dot: "bg-warning",
    text: "text-warning",
    label: "retrying",
    icon: RotateCcw,
  },
  waiting: {
    ring: "border-warning/50 bg-warning/5",
    dot: "bg-warning",
    text: "text-warning",
    label: "waiting",
    icon: Hourglass,
  },
  queued: {
    ring: "border-border bg-card",
    dot: "bg-muted-foreground/40",
    text: "text-muted-foreground",
    label: "queued",
    icon: CircleDashed,
  },
  failed: {
    ring: "border-danger/60 bg-danger/10",
    dot: "bg-danger",
    text: "text-danger",
    label: "failed",
    icon: X,
  },
  timed_out: {
    ring: "border-danger/50 bg-danger/5",
    dot: "bg-danger",
    text: "text-danger",
    label: "timed out",
    icon: TriangleAlert,
  },
  canceled: {
    ring: "border-muted-foreground/30 bg-card",
    dot: "bg-muted-foreground/50",
    text: "text-muted-foreground",
    label: "cancelled",
    icon: Ban,
  },
};

function duration(seconds: number | null): string | null {
  if (seconds === null) return null;
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  return `${m}m ${Math.round(seconds % 60)}s`;
}

// --------------------------------------------------------------------------- //
// The vertex
// --------------------------------------------------------------------------- //
type DagNodeData = { node: GraphNode; selected: boolean };

function DagNode({ data }: NodeProps<Node<DagNodeData>>) {
  const { node, selected } = data;
  const style = STATE[node.state];
  const Icon = node.kind === "gate" ? UserCheck : style.icon;
  const took = duration(node.duration_seconds);

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 text-left transition-colors",
        style.ring,
        selected && "ring-2 ring-accent/60",
      )}
      style={{ width: NODE_W }}
    >
      <Handle type="target" position={Position.Left} className="!bg-border !border-0" />
      <div className="flex items-center gap-1.5">
        <Icon
          className={cn("h-3 w-3 shrink-0", style.text, style.spin && "animate-spin")}
        />
        <span className="truncate font-mono text-[13px] font-medium">{node.label}</span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-2">
        <span className="truncate font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
          {node.kind === "gate"
            ? "approval gate"
            : node.kind === "wait"
              ? "durable wait"
              : (node.node_type ?? node.activity_type ?? "activity")}
        </span>
        <span className={cn("shrink-0 text-[9px] font-medium uppercase", style.text)}>
          {style.label}
        </span>
      </div>
      {(took || node.attempts > 1) && (
        <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
          {took && <span className="tabular-nums">{took}</span>}
          {node.attempts > 1 && (
            <span className="rounded bg-warning/15 px-1 text-warning">
              attempt {node.attempts}
            </span>
          )}
        </div>
      )}
      <Handle
        type="source"
        position={Position.Right}
        className="!bg-border !border-0"
      />
    </div>
  );
}

const NODE_TYPES = { dag: DagNode };

// --------------------------------------------------------------------------- //
// Layout — exact, because the layers are exact
// --------------------------------------------------------------------------- //
function layout(graph: RunGraph, selectedId: string | null): Node<DagNodeData>[] {
  const byLayer = new Map<number, GraphNode[]>();
  for (const n of graph.nodes) {
    const bucket = byLayer.get(n.layer);
    if (bucket) bucket.push(n);
    else byLayer.set(n.layer, [n]);
  }
  const tallest = Math.max(1, ...[...byLayer.values()].map((b) => b.length));

  return graph.nodes.map((n) => {
    const bucket = byLayer.get(n.layer)!;
    const i = bucket.indexOf(n);
    // Centre each column against the widest one, so a fan-out splays either side
    // of its predecessor instead of hanging below it.
    const offset = (tallest - bucket.length) / 2;
    return {
      id: n.id,
      type: "dag",
      position: { x: n.layer * COL, y: (i + offset) * ROW },
      data: { node: n, selected: n.id === selectedId },
      draggable: false,
      connectable: false,
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    };
  });
}

function edges(graph: RunGraph): Edge[] {
  return graph.edges.map((e) => ({
    id: `${e.source}->${e.target}`,
    source: e.source,
    target: e.target,
    animated: !e.done,
    style: {
      stroke: e.done
        ? "hsl(var(--success) / 0.45)"
        : "hsl(var(--muted-foreground) / 0.35)",
      strokeWidth: 1.5,
    },
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 14,
      height: 14,
      color: e.done
        ? "hsl(var(--success) / 0.45)"
        : "hsl(var(--muted-foreground) / 0.35)",
    },
  }));
}

// --------------------------------------------------------------------------- //
// Detail panel
// --------------------------------------------------------------------------- //
function Detail({ node }: { node: GraphNode }) {
  const rows: [string, string][] = [];
  if (node.node_type) rows.push(["node type", node.node_type]);
  if (node.activity_type && node.kind !== "node")
    rows.push(["activity", node.activity_type]);
  if (node.queue) rows.push(["queue", node.queue]);
  if (node.priority) rows.push(["lane", node.priority]);
  if (node.worker) rows.push(["ran on", node.worker]);
  const took = duration(node.duration_seconds);
  if (took) rows.push(["took", took]);
  if (node.attempts > 1) rows.push(["attempts", String(node.attempts)]);
  if (node.decided_by) rows.push(["decided by", node.decided_by]);

  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-sm font-medium">{node.label}</span>
        <span
          className={cn("text-[10px] font-medium uppercase", STATE[node.state].text)}
        >
          {STATE[node.state].label}
        </span>
      </div>
      {rows.length > 0 && (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3">
          {rows.map(([k, v]) => (
            <div key={k} className="min-w-0">
              <dt className="font-mono text-[9px] uppercase tracking-wide text-muted-foreground">
                {k}
              </dt>
              <dd className="truncate font-mono text-[11px]">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      {node.note && (
        <p className="mt-2 text-xs leading-snug text-muted-foreground">{node.note}</p>
      )}
      {node.failure && (
        <p className="mt-2 break-words font-mono text-[11px] leading-snug text-danger">
          {node.failure}
        </p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Canvas
// --------------------------------------------------------------------------- //
function Canvas({
  graph,
  selectedId,
  onSelect,
}: {
  graph: RunGraph;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { fitView } = useReactFlow();
  const nodes = useMemo(() => layout(graph, selectedId), [graph, selectedId]);
  const flowEdges = useMemo(() => edges(graph), [graph]);

  // Refit only when the shape changes. Refitting on every poll would nudge the
  // viewport out from under someone who has panned to look at a node.
  const shape = graph.nodes.map((n) => n.id).join(",");
  useEffect(() => {
    const t = setTimeout(() => fitView({ padding: 0.18, duration: 240 }), 0);
    return () => clearTimeout(t);
  }, [shape, fitView]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={flowEdges}
      nodeTypes={NODE_TYPES}
      onNodeClick={(_, n) => onSelect(n.id === selectedId ? null : n.id)}
      onPaneClick={() => onSelect(null)}
      proOptions={{ hideAttribution: true }}
      // The shell is dark-first, so React Flow's own chrome has to be told; its
      // default is light and the controls otherwise land as a white box.
      colorMode="dark"
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable
      minZoom={0.3}
      maxZoom={1.6}
      fitView
    >
      <Background
        variant={BackgroundVariant.Dots}
        gap={18}
        size={1}
        className="opacity-40"
      />
      <Controls showInteractive={false} className="!shadow-none" />
    </ReactFlow>
  );
}

export function RunDag({ data }: { data: RunGraph | null }) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const select = useCallback((id: string | null) => setSelectedId(id), []);

  // Nothing has been scheduled yet: the workflow has not decided on any work, so
  // there is genuinely no graph — an empty canvas would just look broken.
  if (!data || data.nodes.length === 0) return null;

  const selected = data.nodes.find((n) => n.id === selectedId) ?? null;
  const height = Math.min(
    520,
    Math.max(
      260,
      120 +
        ROW *
          Math.max(
            ...[...new Set(data.nodes.map((n) => n.layer))].map(
              (l) => data.nodes.filter((n) => n.layer === l).length,
            ),
          ),
    ),
  );

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">Graph</h3>
        <p className="text-xs text-muted-foreground">
          <span className="font-mono tabular-nums">
            {data.completed}/{data.total}
          </span>{" "}
          steps recorded · reconstructed from this run&apos;s history, so it is the
          shape the run <em>took</em>, not a diagram of the code
        </p>
      </div>
      <div
        className="rounded-xl border bg-card"
        style={{ height }}
        data-testid="run-dag"
      >
        <ReactFlowProvider>
          <Canvas graph={data} selectedId={selectedId} onSelect={select} />
        </ReactFlowProvider>
      </div>
      {selected ? (
        <Detail node={selected} />
      ) : (
        <p className="text-xs text-muted-foreground">
          Click a step for its worker, queue, timing, and attempts. Columns are
          Temporal&apos;s own causality — steps in one column were scheduled by a single
          workflow decision, so they ran concurrently.
        </p>
      )}
    </section>
  );
}
