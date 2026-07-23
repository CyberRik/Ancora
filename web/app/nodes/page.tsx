"use client";

import { useEffect, useState } from "react";
import { Box, Cpu, ShieldCheck } from "lucide-react";
import { api, type NodeType } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Alert, Card, Chip, EmptyState, PageHeader, Skeleton } from "@/components/ui";

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
    <Card className="flex flex-col p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Box className="h-4 w-4 shrink-0 text-flow" />
        <span className="font-mono text-sm font-medium">{node.type_name}</span>
        <Chip>v{node.version}</Chip>
        <Chip>{node.origin}</Chip>
        <span
          className={cn(
            "ml-auto rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wider",
            node.idempotent
              ? "border-success/30 bg-success/10 text-success"
              : "border-warning/30 bg-warning/10 text-warning",
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

      <p className="mt-2.5 text-sm leading-relaxed text-muted-foreground">{node.summary}</p>

      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
          {SANDBOX_COPY[node.sandbox] ?? node.sandbox}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Cpu className="h-3.5 w-3.5 shrink-0" />
          {resources.num_cpus ?? 0} cpu
          {resources.num_gpus ? ` · ${resources.num_gpus} gpu` : ""}
        </span>
      </div>

      <div className="mt-4 grid gap-2 sm:grid-cols-2">
        <SchemaBox label="Input" fields={schemaFields(node.input_schema)} />
        <SchemaBox label="Output" fields={schemaFields(node.output_schema)} />
      </div>

      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="mt-4 self-start border-t-0 text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        {open ? "Hide" : "Show"} JSON schema
      </button>
      {open && (
        <pre className="mt-2 max-h-64 overflow-auto rounded-md border bg-background/60 p-3 font-mono text-[11px] leading-relaxed">
          {JSON.stringify({ input: node.input_schema, output: node.output_schema }, null, 2)}
        </pre>
      )}
    </Card>
  );
}

function SchemaBox({ label, fields }: { label: string; fields: string[] }) {
  return (
    <div className="rounded-md border bg-background/50 p-2.5">
      <div className="eyebrow">{label}</div>
      <div className="mt-2 flex flex-wrap gap-1">
        {fields.length === 0 ? (
          <span className="font-mono text-[10px] text-muted-foreground">—</span>
        ) : (
          fields.map((f) => (
            <span
              key={f}
              className="rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-foreground"
            >
              {f}
            </span>
          ))
        )}
      </div>
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
      <PageHeader
        eyebrow="Library"
        title="Node catalog"
        description="Every node type this runtime can execute, with the schemas it validates against. The catalog comes from the SDK's own registry, so a node cannot appear here without being runnable — and cannot be runnable without appearing here. Third-party plugins join this list in Phase 5."
      />

      {error && (
        <Alert title="Can't reach the control plane">
          {error}. Check the API is up on{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">:8080</code>.
        </Alert>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {nodes === null && !error && (
          <>
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="space-y-3 p-4">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3.5 w-full" />
                <Skeleton className="h-3.5 w-2/3" />
                <Skeleton className="h-16 w-full" />
              </Card>
            ))}
          </>
        )}

        {nodes?.length === 0 && (
          <EmptyState
            className="lg:col-span-2"
            icon={Box}
            title="No node types registered"
            description="The catalog is reported by the activity worker on startup. If this is empty, nothing is running to report it."
          />
        )}

        {nodes?.map((n) => (
          <NodeCard key={n.type_name} node={n} />
        ))}
      </div>
    </div>
  );
}
