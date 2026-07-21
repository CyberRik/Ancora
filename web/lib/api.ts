import { API_URL } from "./utils";

export interface VersionInfo {
  service: string;
  version: string;
  environment: string;
}

export interface HealthStatus {
  status: string;
  checks: Record<string, string>;
}

export interface WorkflowDef {
  id: string;
  name: string;
  latest_version: number | null;
  versions: number[];
  created_at: string;
}

export type RunStatus =
  | "Queued"
  | "Running"
  | "Completed"
  | "Failed"
  | "Cancelled"
  | "Terminated"
  | "TimedOut";

export interface Run {
  id: string;
  workflow_name: string;
  version: number;
  temporal_wf_id: string;
  temporal_run_id: string;
  status: RunStatus;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  closed_at: string | null;
  created_at: string;
}

export interface StartRunResponse {
  run_id: string;
  temporal_wf_id: string;
  status: RunStatus;
  links: { self: string; stream: string };
}

async function req<T>(
  path: string,
  init?: RequestInit & { signal?: AbortSignal },
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) =>
    req<HealthStatus>("/healthz", { signal }),
  version: (signal?: AbortSignal) =>
    req<VersionInfo>("/v1/version", { signal }),
  listWorkflows: (signal?: AbortSignal) =>
    req<WorkflowDef[]>("/v1/workflows", { signal }),
  listRuns: (signal?: AbortSignal) =>
    req<Run[]>("/v1/runs", { signal }),
  getRun: (id: string, signal?: AbortSignal) =>
    req<Run>(`/v1/runs/${id}`, { signal }),
  startRun: (name: string, input: Record<string, unknown>) =>
    req<StartRunResponse>(`/v1/workflows/${name}/runs`, {
      method: "POST",
      body: JSON.stringify({ input }),
    }),
  cancelRun: (id: string) =>
    req<Run>(`/v1/runs/${id}/cancel`, { method: "POST" }),
};
