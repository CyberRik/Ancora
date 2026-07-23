"use client";

import { useEffect, useState } from "react";
import { Box, Cpu, ShieldCheck } from "lucide-react";
import { api, type NodeType } from "@/lib/api";
import { cn } from "@/lib/utils";

const SANDBOX_COPY: Record<string, string> = {
  t0: "in-process — trusted built-in",
  t1: "isolated child process / Ray runtime env",
  t2: "container isolation",
};

function schemaFields(schema: Record<string, unknown>): string[] {
  const props = schema?.properties as Record<string, unknown> | undefined;
  return props ? Object.keys(props) : [];
}

function NodeCard({ node }: { node: NodeType }) {
  const [open, setOpen] = useState(false);
  const resources = node.resources as { num_cpus?: number; num_gpus?: number };

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Box className="h-4 w-4 text-accent" />
        <span className="font-mono text-sm font-medium">{node.type_name}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          v{node.version}
        </span>
        <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] uppercase text-foreground">
          {node.origin}
        </span>
        <span
          className={cn(
            "ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
            node.idempotent ? "bg-success/15 text-success" : "bg-warning/15 text-warning",
          )}
          title={
            node.idempotent
              ? "Safe to repeat — retries re-execute freely."
              : "Not safe to repeat — every execution is guarded by the idempotency inbox."
          }
        >
          {node.idempotent ? "idempotent" : "inbox-guarded"}
        </span>
      </div>

      <p className="mt-2 text-sm text-muted-foreground">{node.summary}</p>

      <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <ShieldCheck className="h-3.5 w-3.5" />
          {SANDBOX_COPY[node.sandbox] ?? node.sandbox}
        </span>
        <span className="inline-flex items-center gap-1">
          <Cpu className="h-3.5 w-3.5" />
          {resources.num_cpus ?? 0} cpu
          {resources.num_gpus ? ` · ${resources.num_gpus} gpu` : ""}
        </span>
      </div>

      <div className="mt-3 grid gap-2 sm:grid-cols-2">
        <div className="rounded-md border bg-background/50 p-2">
          <div className="font-mono text-[10px] uppercase text-muted-foreground">Input</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {schemaFields(node.input_schema).map((f) => (
              <span key={f} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                {f}
              </span>
            ))}
          </div>
        </div>
        <div className="rounded-md border bg-background/50 p-2">
          <div className="font-mono text-[10px] uppercase text-muted-foreground">Output</div>
          <div className="mt-1 flex flex-wrap gap-1">
            {schemaFields(node.output_schema).map((f) => (
              <span key={f} className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px]">
                {f}
              </span>
            ))}
          </div>
        </div>
      </div>

      <button
        onClick={() => setOpen((o) => !o)}
        className="mt-3 text-[11px] text-muted-foreground hover:text-foreground"
      >
        {open ? "Hide" : "Show"} JSON schema
      </button>
      {open && (
        <pre className="mt-2 max-h-64 overflow-auto rounded bg-muted/50 p-3 text-[11px] leading-relaxed">
          {JSON.stringify({ input: node.input_schema, output: node.output_schema }, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function NodesPage() {
  const [nodes, setNodes] = useState<NodeType[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    api
      .listNodeTypes(c.signal)
      .then((n) => {
        setNodes(n);
        setError(null);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(e instanceof Error ? e.message : "failed to load");
      });
    return () => c.abort();
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Node catalog</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Every node type this runtime can execute, with the schemas it validates
          against. The catalog comes from the SDK&apos;s own registry, so a node
          cannot appear here without being runnable — and cannot be runnable
          without appearing here. Third-party plugins join this list in Phase 5.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          API error: {error}. Is the stack running?
        </div>
      )}

      {nodes === null && !error && <div className="text-sm text-muted-foreground">Loading…</div>}

      <div className="grid gap-3 lg:grid-cols-2">
        {nodes?.map((n) => (
          <NodeCard key={n.type_name} node={n} />
        ))}
      </div>
    </div>
  );
}
