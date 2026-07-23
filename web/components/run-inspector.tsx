"use client";

/**
 * Node inspector + cost breakdown for a single run (AN-065, AN-057).
 *
 * Three sources are merged into one list of nodes, because no single one of them
 * knows the whole story:
 *
 *   - the cost ledger  → what finished, and what it cost
 *   - the retry log    → what failed, and whether the runtime judged it retryable
 *   - live activities  → what is running *right now*, with the real attempt counter
 *
 * A node that failed twice and then succeeded appears in all three. Merging by
 * node id is what lets the inspector show "attempt 3 · 2 failures · $0.004"
 * instead of three disconnected rows.
 *
 * The graph is hand-drawn rather than pulled from a diagramming library: the
 * shapes we ship are linear or single-fan-out, and matching the dashboard's own
 * visual language matters more here than general graph layout. The real
 * event-sourced node projection lands in Phase 4 and will carry edges with it.
 */

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Coins, RefreshCw, Zap } from "lucide-react";
import { api, type RetryAttempt, type RunCost, type RunLive } from "@/lib/api";
import { cn } from "@/lib/utils";

type NodeState = "running" | "retrying" | "failed" | "done";

interface MergedNode {
  nodeId: string;
  nodeType: string;
  state: NodeState;
  attempt: number;
  usd: number;
  inputTokens: number;
  outputTokens: number;
  provider: string | null;
  model: string | null;
  failures: RetryAttempt[];
  lastError: string | null;
}

const STATE_STYLES: Record<NodeState, { dot: string; chip: string; label: string }> = {
  running: { dot: "bg-flow", chip: "bg-flow/15 text-flow", label: "running" },
  retrying: {
    dot: "bg-warning",
    chip: "bg-warning/15 text-warning",
    label: "retrying",
  },
  failed: { dot: "bg-danger", chip: "bg-danger/15 text-danger", label: "failed" },
  done: { dot: "bg-success", chip: "bg-success/15 text-success", label: "done" },
};

function usd(value: number): string {
  if (value === 0) return "$0";
  if (value < 0.01) return `$${value.toFixed(5)}`;
  return `$${value.toFixed(4)}`;
}

function merge(
  cost: RunCost | null,
  retries: RetryAttempt[],
  live: RunLive | null,
): MergedNode[] {
  const byId = new Map<string, MergedNode>();

  const ensure = (nodeId: string, nodeType: string): MergedNode => {
    let node = byId.get(nodeId);
    if (!node) {
      node = {
        nodeId,
        nodeType,
        state: "done",
        attempt: 1,
        usd: 0,
        inputTokens: 0,
        outputTokens: 0,
        provider: null,
        model: null,
        failures: [],
        lastError: null,
      };
      byId.set(nodeId, node);
    }
    if (nodeType && node.nodeType === "node") node.nodeType = nodeType;
    return node;
  };

  for (const line of cost?.lines ?? []) {
    const node = ensure(line.node_id, line.node_type);
    node.usd += line.usd;
    node.inputTokens += line.input_tokens;
    node.outputTokens += line.output_tokens;
    node.provider = line.provider ?? node.provider;
    node.model = line.model ?? node.model;
    node.attempt = Math.max(node.attempt, line.attempt);
  }

  for (const r of retries) {
    const node = ensure(r.node_id, r.node_type);
    node.failures.push(r);
    node.attempt = Math.max(node.attempt, r.attempt);
    node.lastError = r.error;
    // A failure with no later cost row means the node never succeeded.
    node.state = node.usd > 0 ? "done" : r.transient ? "retrying" : "failed";
  }

  // Live activities are the only source that knows about work in flight.
  for (const act of live?.activities ?? []) {
    const nodeId =
      act.activity_type === "run_node" ? act.activity_id : act.activity_type;
    const node = ensure(nodeId, act.activity_type);
    node.attempt = Math.max(node.attempt, act.attempt);
    node.state = act.last_failure && act.state !== "Started" ? "retrying" : "running";
    if (act.last_failure) node.lastError = act.last_failure;
  }

  return [...byId.values()];
}

function Bar({ value, total }: { value: number; total: number }) {
  const pct = total > 0 ? Math.max(2, Math.round((value / total) * 100)) : 0;
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
      <div className="h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
    </div>
  );
}

function Rollup({
  title,
  groups,
  total,
}: {
  title: string;
  groups: RunCost["by_node"];
  total: number;
}) {
  if (groups.length === 0) return null;
  return (
    <div className="rounded-lg border bg-card p-3">
      <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
        {title}
      </div>
      <div className="mt-2 space-y-2">
        {groups.slice(0, 6).map((g) => (
          <div key={g.key} className="space-y-1">
            <div className="flex items-baseline justify-between gap-2 text-xs">
              <span className="truncate font-mono">{g.key}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {usd(g.usd)} · {g.calls}×
              </span>
            </div>
            <Bar value={g.usd} total={total} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function RunInspector({
  runId,
  live,
  terminal,
}: {
  runId: string;
  live: RunLive | null;
  terminal: boolean;
}) {
  const [cost, setCost] = useState<RunCost | null>(null);
  const [retries, setRetries] = useState<RetryAttempt[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    const load = () => {
      // Both endpoints read projections only, so a Temporal blip cannot blank the
      // inspector. Failures are swallowed — this panel is supplementary.
      api
        .getRunCost(runId, c.signal)
        .then(setCost)
        .catch(() => {});
      api
        .getRunRetries(runId, c.signal)
        .then(setRetries)
        .catch(() => {});
    };
    load();
    if (terminal) return () => c.abort();
    const t = setInterval(load, 2500);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [runId, terminal]);

  const nodes = useMemo(() => merge(cost, retries, live), [cost, retries, live]);
  const active = nodes.find((n) => n.nodeId === selected) ?? null;
  const totalFailures = retries.length;

  if (nodes.length === 0) {
    return (
      <section className="space-y-3">
        <h3 className="text-sm font-medium">Nodes</h3>
        <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
          No node executions recorded for this run yet. Workflows built from the
          built-in node library (
          <code className="rounded bg-muted px-1">research_agent</code>) report cost
          and retries here as they run.
        </div>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="text-sm font-medium">Nodes</h3>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <Coins className="h-3.5 w-3.5" />
              {usd(cost?.total_usd ?? 0)}
            </span>
            <span className="inline-flex items-center gap-1">
              <Zap className="h-3.5 w-3.5" />
              {(cost?.input_tokens ?? 0) + (cost?.output_tokens ?? 0)} tokens
            </span>
            {totalFailures > 0 && (
              <span className="inline-flex items-center gap-1 text-warning">
                <RefreshCw className="h-3.5 w-3.5" />
                {totalFailures} failed {totalFailures === 1 ? "attempt" : "attempts"}
              </span>
            )}
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-[1fr_1fr]">
          {/* Node list — click to inspect */}
          <div className="space-y-1.5">
            {nodes.map((n) => {
              const style = STATE_STYLES[n.state];
              return (
                <button
                  key={n.nodeId}
                  onClick={() => setSelected(n.nodeId === selected ? null : n.nodeId)}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-lg border bg-card px-3 py-2 text-left transition-colors hover:bg-muted/50",
                    n.nodeId === selected && "border-accent/50 bg-accent/5",
                  )}
                >
                  <span
                    className={cn(
                      "h-2 w-2 shrink-0 rounded-full",
                      style.dot,
                      (n.state === "running" || n.state === "retrying") &&
                        "animate-pulse",
                    )}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-sm">{n.nodeId}</span>
                    <span className="block truncate text-[11px] text-muted-foreground">
                      {n.nodeType}
                      {n.model ? ` · ${n.model}` : ""}
                      {n.attempt > 1 ? ` · attempt ${n.attempt}` : ""}
                    </span>
                  </span>
                  {n.failures.length > 0 && (
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-warning" />
                  )}
                  <span className="shrink-0 tabular-nums text-xs text-muted-foreground">
                    {usd(n.usd)}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium uppercase",
                      style.chip,
                    )}
                  >
                    {style.label}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Inspector / cost rollups */}
          <div className="space-y-3">
            {active ? (
              <div className="rounded-lg border bg-card p-4">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm">{active.nodeId}</span>
                  <span
                    className={cn(
                      "rounded px-1.5 py-0.5 text-[9px] font-medium uppercase",
                      STATE_STYLES[active.state].chip,
                    )}
                  >
                    {STATE_STYLES[active.state].label}
                  </span>
                </div>
                <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs text-muted-foreground">
                  <dt>Type</dt>
                  <dd className="text-right font-mono text-foreground">
                    {active.nodeType}
                  </dd>
                  <dt>Attempt</dt>
                  <dd className="text-right tabular-nums text-foreground">
                    {active.attempt}
                  </dd>
                  <dt>Provider / model</dt>
                  <dd className="truncate text-right text-foreground">
                    {active.provider ?? "—"}
                    {active.model ? ` · ${active.model}` : ""}
                  </dd>
                  <dt>Tokens in / out</dt>
                  <dd className="text-right tabular-nums text-foreground">
                    {active.inputTokens} / {active.outputTokens}
                  </dd>
                  <dt>Cost</dt>
                  <dd className="text-right tabular-nums text-foreground">
                    {usd(active.usd)}
                  </dd>
                </dl>

                {active.failures.length > 0 && (
                  <div className="mt-4 space-y-2 border-t pt-3">
                    <div className="font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
                      Failed attempts
                    </div>
                    {active.failures.map((f, i) => (
                      <div
                        key={i}
                        className="rounded-md border border-warning/30 bg-warning/5 p-2"
                      >
                        <div className="flex items-center justify-between text-[11px]">
                          <span className="font-mono">attempt {f.attempt}</span>
                          <span
                            className={f.transient ? "text-warning" : "text-danger"}
                          >
                            {f.transient ? "transient → retried" : "terminal → gave up"}
                          </span>
                        </div>
                        {f.error && (
                          <p className="mt-1 break-words text-[11px] text-muted-foreground">
                            {f.error}
                          </p>
                        )}
                      </div>
                    ))}
                    <p className="text-[11px] text-muted-foreground">
                      The transient/terminal call is what decided whether a retry
                      happened at all — a terminal failure fails fast rather than
                      burning the node&apos;s whole attempt budget.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-lg border border-dashed bg-card/50 p-4 text-sm text-muted-foreground">
                Select a node to inspect its attempts, cost, and failures.
              </div>
            )}

            {cost && cost.total_usd > 0 && (
              <>
                <Rollup
                  title="Cost by model"
                  groups={cost.by_model}
                  total={cost.total_usd}
                />
                <Rollup
                  title="Cost by provider"
                  groups={cost.by_provider}
                  total={cost.total_usd}
                />
              </>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
