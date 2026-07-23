"use client";

import { useEffect, useState } from "react";
import { api, type HealthStatus, type VersionInfo } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Alert, Card, PageHeader, Skeleton } from "@/components/ui";

type State =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ok"; health: HealthStatus; version: VersionInfo };

export default function HealthPage() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const [health, version] = await Promise.all([
          api.health(controller.signal),
          api.version(controller.signal),
        ]);
        setState({ kind: "ok", health, version });
      } catch (e) {
        setState({
          kind: "error",
          message: e instanceof Error ? e.message : "unknown error",
        });
      }
    })();
    return () => controller.abort();
  }, []);

  return (
    <div className="max-w-2xl space-y-6">
      <PageHeader
        eyebrow="Control plane"
        title="Health"
        description="A live check against the API and its dependencies. If the dashboard looks frozen, this is the first place to look."
      />

      {state.kind === "loading" && (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="flex items-center justify-between p-4">
              <Skeleton className="h-3.5 w-24" />
              <Skeleton className="h-3.5 w-16" />
            </Card>
          ))}
        </div>
      )}

      {state.kind === "error" && (
        <Alert title="API unreachable">
          {state.message}. Confirm the control plane is listening on{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">
            {process.env.NEXT_PUBLIC_API_URL}
          </code>
          , then reload.
        </Alert>
      )}

      {state.kind === "ok" && (
        <div className="space-y-4">
          <div className="space-y-2">
            <StatusRow label="Overall" value={state.health.status} ok />
            {Object.entries(state.health.checks).map(([k, v]) => (
              <StatusRow key={k} label={k} value={v} ok={v === "ok"} />
            ))}
          </div>

          <Card className="p-4">
            <div className="eyebrow">Build</div>
            <div className="mt-2 font-mono text-sm">
              {state.version.service}{" "}
              <span className="text-flow">{state.version.version}</span>
              <span className="text-muted-foreground"> · {state.version.environment}</span>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function StatusRow({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return (
    <Card className="flex items-center justify-between gap-4 px-4 py-3">
      <span className="text-sm capitalize text-muted-foreground">{label}</span>
      <span
        className={cn(
          "inline-flex items-center gap-2 font-mono text-xs font-medium",
          ok ? "text-success" : "text-danger",
        )}
      >
        <span className="relative flex h-1.5 w-1.5">
          {ok && <span className="pulse-dot absolute inset-0 rounded-full" />}
          <span className="relative h-1.5 w-1.5 rounded-full bg-current" />
        </span>
        {value}
      </span>
    </Card>
  );
}
