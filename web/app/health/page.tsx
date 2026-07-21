"use client";

import { useEffect, useState } from "react";
import { api, type HealthStatus, type VersionInfo } from "@/lib/api";
import { cn } from "@/lib/utils";

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
    <div className="max-w-xl space-y-4">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">API Health</h2>
        <p className="text-sm text-muted-foreground">
          Live check against the control-plane API.
        </p>
      </div>

      {state.kind === "loading" && (
        <div className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
          Checking…
        </div>
      )}

      {state.kind === "error" && (
        <div className="rounded-lg border border-danger/40 bg-card p-4 text-sm">
          <div className="font-medium text-danger">API unreachable</div>
          <div className="mt-1 text-muted-foreground">
            {state.message}. Is the API running on{" "}
            <code>{process.env.NEXT_PUBLIC_API_URL}</code>?
          </div>
        </div>
      )}

      {state.kind === "ok" && (
        <div className="space-y-3">
          <StatusRow label="Overall" value={state.health.status} ok />
          {Object.entries(state.health.checks).map(([k, v]) => (
            <StatusRow key={k} label={k} value={v} ok={v === "ok"} />
          ))}
          <div className="rounded-lg border bg-card p-4 text-sm">
            <div className="text-muted-foreground">Version</div>
            <div className="mt-1 font-mono">
              {state.version.service} {state.version.version} ·{" "}
              {state.version.environment}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusRow({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok: boolean;
}) {
  return (
    <div className="flex items-center justify-between rounded-lg border bg-card p-4">
      <span className="text-sm capitalize text-muted-foreground">{label}</span>
      <span className="flex items-center gap-2 text-sm">
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            ok ? "bg-success" : "bg-danger",
          )}
        />
        {value}
      </span>
    </div>
  );
}
