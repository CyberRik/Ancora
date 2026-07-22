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

export type WorkerStatus = "live" | "stale" | "unknown";

export interface WorkerResources {
  total_cpus?: number;
  total_gpus?: number;
  accelerator_type?: string | null;
}

export interface Worker {
  worker_id: string;
  host: string | null;
  pid: number | null;
  pools: string[];
  task_queues: string[];
  resources: WorkerResources;
  status: WorkerStatus;
  registered_at: string;
  last_heartbeat_at: string | null;
}

export interface Queue {
  queue: string;
  capability: string | null;
  worker_count: number;
  live_worker_count: number;
  backlog: number;
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
  sendSignal: (id: string, name: string, arg?: unknown) =>
    req<Run>(`/v1/runs/${id}/signals/${name}`, {
      method: "POST",
      body: JSON.stringify(arg ?? null),
    }),
  listWorkers: (signal?: AbortSignal) =>
    req<Worker[]>("/v1/workers", { signal }),
  listQueues: (signal?: AbortSignal) =>
    req<Queue[]>("/v1/queues", { signal }),
};

// --------------------------------------------------------------------------- //
// Workflow shapes — a small front-end catalog describing what each shipped
// workflow *does*, so a run can be rendered as a readable pipeline of steps
// rather than opaque JSON. (Until the event-sourced node projection lands in
// Phase 4, per-run node state isn't on the wire; these describe the code path.)
// --------------------------------------------------------------------------- //
export type StepKind = "activity" | "gate" | "dispatch";

export interface WorkflowStep {
  label: string;
  kind: StepKind;
  detail: string;
  /** Where the real compute happens for this step. */
  runsOn: "workflow-worker" | "activity-worker";
}

export interface WorkflowShape {
  summary: string;
  steps: WorkflowStep[];
}

export const WORKFLOW_SHAPES: Record<string, WorkflowShape> = {
  hello: {
    summary: "Three activities in sequence — the canonical durable-execution smoke test.",
    steps: [
      { label: "greet", kind: "activity", detail: "Hello, {name}!", runsOn: "workflow-worker" },
      { label: "greet", kind: "activity", detail: "wraps the previous result", runsOn: "workflow-worker" },
      { label: "greet", kind: "activity", detail: "wraps it once more", runsOn: "workflow-worker" },
    ],
  },
  gated: {
    summary: "Runs an activity, waits durably for human approval, then finishes.",
    steps: [
      { label: "greet", kind: "activity", detail: "first activity", runsOn: "workflow-worker" },
      { label: "approval gate", kind: "gate", detail: "waits for the approve signal — durably, indefinitely", runsOn: "workflow-worker" },
      { label: "greet", kind: "activity", detail: "runs once approved", runsOn: "workflow-worker" },
    ],
  },
  pipeline: {
    summary: "Dispatches a GPU-ish compute activity to the execution runtime via async completion.",
    steps: [
      { label: "ray_compute_async", kind: "dispatch", detail: "queued on ancora-cpu, run on Ray / local, completed out-of-band", runsOn: "activity-worker" },
    ],
  },
};
