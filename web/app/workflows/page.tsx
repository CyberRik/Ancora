"use client";

import { useEffect, useState } from "react";
import { api, type WorkflowDef } from "@/lib/api";

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
    <div className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Workflows</h2>
        <p className="text-sm text-muted-foreground">
          Registered workflow definitions. Workers report these on startup; the
          version bumps when a workflow&apos;s code changes.
        </p>
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          API error: {error}. Is the stack running?
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        {defs === null && !error && (
          <div className="text-sm text-muted-foreground">Loading…</div>
        )}
        {defs?.length === 0 && (
          <div className="text-sm text-muted-foreground">
            No workflows registered yet. Start the worker.
          </div>
        )}
        {defs?.map((d) => (
          <div key={d.id} className="rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium">{d.name}</span>
              <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                latest v{d.latest_version ?? "—"}
              </span>
            </div>
            <div className="mt-2 text-xs text-muted-foreground">
              {d.versions.length} version{d.versions.length === 1 ? "" : "s"} · registered{" "}
              {new Date(d.created_at).toLocaleDateString()}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
