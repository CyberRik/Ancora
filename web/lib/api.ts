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

export interface RunActivity {
  activity_id: string;
  activity_type: string;
  state: string;
  attempt: number;
  maximum_attempts: number;
  last_failure: string | null;
  last_worker_identity: string | null;
}

export interface RunLive {
  run_id: string;
  status: RunStatus;
  status_note: string | null;
  activities: RunActivity[];
}

// --- Cost accounting (Phase 3, AN-057) ------------------------------------ //
export interface CostLine {
  node_id: string;
  node_type: string;
  attempt: number;
  provider: string | null;
  model: string | null;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  gpu_seconds: number;
  created_at: string;
}

export interface CostGroup {
  key: string;
  usd: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
}

export interface RunCost {
  run_id: string;
  total_usd: number;
  input_tokens: number;
  output_tokens: number;
  gpu_seconds: number;
  by_node: CostGroup[];
  by_model: CostGroup[];
  by_provider: CostGroup[];
  lines: CostLine[];
}

export interface RetryAttempt {
  node_id: string;
  node_type: string;
  attempt: number;
  error: string | null;
  transient: boolean;
  retry_after_seconds: number | null;
  created_at: string;
}

// --- Human-in-the-loop (Phase 3, AN-064) ---------------------------------- //
export type ApprovalStatus = "waiting" | "approved" | "rejected" | "expired";

export interface Approval {
  id: string;
  run_id: string | null;
  temporal_wf_id: string;
  gate_id: string;
  workflow_name: string | null;
  status: ApprovalStatus;
  prompt: string | null;
  payload: Record<string, unknown> | null;
  requested_at: string;
  expires_at: string | null;
  decided_at: string | null;
  decided_by: string | null;
  comment: string | null;
}

// --- Chaos Lab ------------------------------------------------------------ //
export interface ChaosTarget {
  service: string;
  name: string;
  state: string;
  killable: boolean;
}

export interface ChaosEvent {
  action: string;
  service: string;
  at: number;
  detail: string;
}

export interface ChaosStatus {
  enabled: boolean;
  project: string;
  targets: ChaosTarget[];
  events: ChaosEvent[];
  reason: string | null;
}

// --- Node catalog (Phase 3, AN-058) --------------------------------------- //
export interface NodeType {
  type_name: string;
  version: string;
  summary: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  resources: Record<string, unknown>;
  sandbox: string;
  idempotent: boolean;
  origin: string;
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
  getRunActivities: (id: string, signal?: AbortSignal) =>
    req<RunLive>(`/v1/runs/${id}/activities`, { signal }),
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
  getRunCost: (id: string, signal?: AbortSignal) =>
    req<RunCost>(`/v1/runs/${id}/cost`, { signal }),
  getRunRetries: (id: string, signal?: AbortSignal) =>
    req<RetryAttempt[]>(`/v1/runs/${id}/retries`, { signal }),
  listApprovals: (status = "waiting", signal?: AbortSignal) =>
    req<Approval[]>(`/v1/approvals?status=${encodeURIComponent(status)}`, { signal }),
  decideApproval: (id: string, body: { approved: boolean; comment?: string; decided_by?: string }) =>
    req<Approval>(`/v1/approvals/${id}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listNodeTypes: (signal?: AbortSignal) =>
    req<NodeType[]>("/v1/plugins", { signal }),
  chaosStatus: (signal?: AbortSignal) =>
    req<ChaosStatus>("/v1/chaos", { signal }),
  chaosInject: (action: "kill" | "restart", service: string) =>
    req<ChaosTarget>("/v1/chaos/inject", {
      method: "POST",
      body: JSON.stringify({ action, service }),
    }),
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
  research_agent: {
    summary: "Orchestrates LLM nodes to search, summarize, and synthesize a report with a human-in-the-loop gate.",
    steps: [
      { label: "search", kind: "activity", detail: "LLM agent searches for sources", runsOn: "activity-worker" },
      { label: "summarize (fan-out)", kind: "activity", detail: "parallel summarization of each source", runsOn: "activity-worker" },
      { label: "synthesize", kind: "activity", detail: "synthesize a final report from summaries", runsOn: "activity-worker" },
      { label: "approval gate", kind: "gate", detail: "durably waits for human approval before publishing", runsOn: "workflow-worker" },
      { label: "publish", kind: "activity", detail: "publishes the report if approved", runsOn: "activity-worker" },
    ],
  },
  human_gate: {
    summary: "A gate that expires and escalates — nobody deciding is itself a decision.",
    steps: [
      { label: "approval gate", kind: "gate", detail: "durable timer; expires after N days", runsOn: "workflow-worker" },
      { label: "escalate", kind: "activity", detail: "runs only on the expiry branch", runsOn: "workflow-worker" },
    ],
  },
  durability_demo: {
    summary: "A 3-step pipeline that survives a mid-run worker failure.",
    steps: [
      { label: "ingest_dataset", kind: "activity", detail: "pulls the dataset — the step we must never redo", runsOn: "workflow-worker" },
      { label: "process_records", kind: "activity", detail: "fails once, then recovers on retry", runsOn: "workflow-worker" },
      { label: "export_results", kind: "activity", detail: "writes the finished output", runsOn: "workflow-worker" },
    ],
  },
};
