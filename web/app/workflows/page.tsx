"use client";

import { useEffect, useState } from "react";
import { GitBranch } from "lucide-react";
import { api, type WorkflowDef } from "@/lib/api";
import { Alert, Card, Chip, EmptyState, PageHeader, SkeletonCards } from "@/components/ui";

export default function WorkflowsPage() {
  const [defs, setDefs] = useState<WorkflowDef[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    api
      .listWorkflows(c.signal)
      .then((res) => {
        setDefs(res);
        setError(null);
      })
      .catch((e) => {
        if (e.name === "AbortError" || (e instanceof Error && e.message.includes("aborted"))) return;
        setError(e instanceof Error ? e.message : "failed to load");
      });
    return () => c.abort();
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Catalog"
        title="Workflows"
        description="Registered workflow definitions. Workers report these on startup, and the version bumps whenever a workflow's code changes — old runs keep replaying against the version they started on."
      />

      {error && (
        <Alert title="Can't reach the control plane">
          {error}. Check the API is up on{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">:8080</code>.
        </Alert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {defs === null && !error && <SkeletonCards count={6} />}

        {defs?.length === 0 && (
          <EmptyState
            className="sm:col-span-2 lg:col-span-3"
            icon={GitBranch}
            title="No workflows registered"
            description="Definitions appear here the moment a workflow worker starts and reports its catalog."
          />
        )}

        {defs?.map((d) => (
          <Card key={d.id} interactive className="p-4">
            <div className="flex items-start justify-between gap-2">
              <span className="truncate font-medium">{d.name}</span>
              <Chip tone="flow">v{d.latest_version ?? "—"}</Chip>
            </div>
            <div className="mt-3 flex items-center gap-1.5 border-t pt-3 text-xs text-muted-foreground">
              <span data-numeric>{d.versions.length}</span>
              <span>version{d.versions.length === 1 ? "" : "s"}</span>
              <span aria-hidden>·</span>
              <span>registered {new Date(d.created_at).toLocaleDateString()}</span>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
