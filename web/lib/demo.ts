import {
  WorkflowDef,
  Run,
  RunLive,
  RunRecovery,
  RunGraph,
  RunCost,
  RetryAttempt,
  Worker,
  Queue,
  Approval,
  NodeType,
  ChaosStatus,
} from "./api";

const nowTime = new Date().getTime();
const d = (secondsAgo: number) => new Date(nowTime - secondsAgo * 1000).toISOString();

const mockWorkflows: WorkflowDef[] = [
  {
    id: "research_agent",
    name: "research_agent",
    latest_version: 3,
    versions: [1, 2, 3],
    created_at: d(86400 * 5),
  },
  {
    id: "durability_demo",
    name: "durability_demo",
    latest_version: 1,
    versions: [1],
    created_at: d(86400 * 2),
  },
  {
    id: "hello",
    name: "hello",
    latest_version: 1,
    versions: [1],
    created_at: d(86400 * 10),
  },
  {
    id: "gated",
    name: "gated",
    latest_version: 1,
    versions: [1],
    created_at: d(86400 * 7),
  },
];

const mockRuns: Run[] = [
  {
    id: "run-research-1",
    workflow_name: "research_agent",
    version: 3,
    temporal_wf_id: "wf-res-1",
    temporal_run_id: "tr-res-1",
    status: "Completed",
    input: { topic: "durable execution", summaries: 2 },
    output: { report: "Durable execution ensures multi-step workloads complete reliably..." },
    error: null,
    started_at: d(3600),
    closed_at: d(3500),
    created_at: d(3605),
  },
  {
    id: "run-research-2",
    workflow_name: "research_agent",
    version: 3,
    temporal_wf_id: "wf-res-2",
    temporal_run_id: "tr-res-2",
    status: "Running",
    input: { topic: "agentic systems", summaries: 3 },
    output: null,
    error: null,
    started_at: d(300),
    closed_at: null,
    created_at: d(305),
  },
  {
    id: "run-durability-1",
    workflow_name: "durability_demo",
    version: 1,
    temporal_wf_id: "wf-dur-1",
    temporal_run_id: "tr-dur-1",
    status: "Completed",
    input: { dataset: "large-events" },
    output: { processed: 5000 },
    error: null,
    started_at: d(7200),
    closed_at: d(6900),
    created_at: d(7205),
  },
  {
    id: "run-hello-1",
    workflow_name: "hello",
    version: 1,
    temporal_wf_id: "wf-hello-1",
    temporal_run_id: "tr-hello-1",
    status: "Completed",
    input: { name: "Alice" },
    output: { greeting: "Hello, Alice! (wrapped) (wrapped)" },
    error: null,
    started_at: d(86400),
    closed_at: d(86395),
    created_at: d(86405),
  },
];

const mockWorkers: Worker[] = [
  {
    worker_id: "activity-worker-a1",
    host: "node-gpu-1",
    pid: 1024,
    pools: ["default", "gpu"],
    task_queues: ["ancora-cpu", "ancora-gpu"],
    resources: { total_cpus: 16, total_gpus: 1, accelerator_type: "A100" },
    status: "live",
    registered_at: d(3600 * 24),
    last_heartbeat_at: d(5),
  },
  {
    worker_id: "workflow-worker-b2",
    host: "node-cpu-1",
    pid: 5042,
    pools: ["default"],
    task_queues: ["ancora-wf"],
    resources: { total_cpus: 8, total_gpus: 0 },
    status: "live",
    registered_at: d(3600 * 24),
    last_heartbeat_at: d(10),
  }
];

const mockQueues: Queue[] = [
  {
    queue: "ancora-cpu",
    capability: "cpu",
    worker_count: 1,
    live_worker_count: 1,
    backlog: 0,
  },
  {
    queue: "ancora-wf",
    capability: "workflow",
    worker_count: 1,
    live_worker_count: 1,
    backlog: 0,
  }
];

const mockApprovals: Approval[] = [
  {
    id: "app-res-2",
    run_id: "run-research-2",
    temporal_wf_id: "wf-res-2",
    gate_id: "gate-pub-1",
    workflow_name: "research_agent",
    status: "waiting",
    prompt: "Approve publication of 'Agentic Systems Report'?",
    payload: { preview: "Agentic systems represent the next phase..." },
    requested_at: d(120),
    expires_at: null,
    decided_at: null,
    decided_by: null,
    comment: null,
  }
];

const mockNodes: NodeType[] = [
  {
    type_name: "llm",
    version: "1.0",
    summary: "Chat/completion across providers",
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    resources: {},
    sandbox: "none",
    idempotent: false,
    origin: "built-in",
  },
  {
    type_name: "http",
    version: "1.0",
    summary: "REST call with templating",
    input_schema: { type: "object" },
    output_schema: { type: "object" },
    resources: {},
    sandbox: "none",
    idempotent: true,
    origin: "built-in",
  }
];

const getRunGraph = (id: string): RunGraph => {
  if (id === "run-research-2") {
    return {
      run_id: id,
      workflow_name: "research_agent",
      status: "Running",
      now: d(0),
      nodes: [
        {
          id: "n-search", label: "search", kind: "activity", node_type: "llm",
          activity_type: "activity-search", activity_id: "act-1",
          layer: 0, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(300), ended_at: d(290), duration_seconds: 10,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-sum-1", label: "summarize (fan-out)", kind: "activity", node_type: "llm",
          activity_type: "activity-sum", activity_id: "act-2",
          layer: 1, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(285), ended_at: d(270), duration_seconds: 15,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-sum-2", label: "summarize (fan-out)", kind: "activity", node_type: "llm",
          activity_type: "activity-sum", activity_id: "act-3",
          layer: 1, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(285), ended_at: d(260), duration_seconds: 25,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-sum-3", label: "summarize (fan-out)", kind: "activity", node_type: "llm",
          activity_type: "activity-sum", activity_id: "act-4",
          layer: 1, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(285), ended_at: d(265), duration_seconds: 20,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-synth", label: "synthesize", kind: "activity", node_type: "llm",
          activity_type: "activity-synth", activity_id: "act-5",
          layer: 2, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(250), ended_at: d(130), duration_seconds: 120,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-gate", label: "approval gate", kind: "gate", node_type: null,
          activity_type: null, activity_id: null,
          layer: 3, state: "waiting", attempts: 1, lost_attempts: 0,
          worker: "workflow-worker-b2", queue: "ancora-wf", priority: "default",
          started_at: d(120), ended_at: null, duration_seconds: null,
          failure: null, approved: null, decided_by: null, timed_out: false, note: "waiting for user"
        }
      ],
      edges: [
        { source: "n-search", target: "n-sum-1", done: true },
        { source: "n-search", target: "n-sum-2", done: true },
        { source: "n-search", target: "n-sum-3", done: true },
        { source: "n-sum-1", target: "n-synth", done: true },
        { source: "n-sum-2", target: "n-synth", done: true },
        { source: "n-sum-3", target: "n-synth", done: true },
        { source: "n-synth", target: "n-gate", done: true },
      ],
      completed: 5,
      total: 6,
    };
  }
  
  if (id === "run-durability-1") {
    return {
      run_id: id,
      workflow_name: "durability_demo",
      status: "Completed",
      now: d(0),
      nodes: [
        {
          id: "n-ingest", label: "ingest_dataset", kind: "activity", node_type: "http",
          activity_type: "activity-ingest", activity_id: "act-dur-1",
          layer: 0, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(7200), ended_at: d(7180), duration_seconds: 20,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        {
          id: "n-proc", label: "process_records", kind: "activity", node_type: "python",
          activity_type: "activity-proc", activity_id: "act-dur-2",
          layer: 1, state: "completed", attempts: 2, lost_attempts: 1,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(7000), ended_at: d(6950), duration_seconds: 50,
          failure: null, approved: null, decided_by: null, timed_out: false, note: "recovering from crash"
        },
        {
          id: "n-export", label: "export_results", kind: "activity", node_type: "http",
          activity_type: "activity-export", activity_id: "act-dur-3",
          layer: 2, state: "completed", attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(6940), ended_at: d(6900), duration_seconds: 40,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        }
      ],
      edges: [
        { source: "n-ingest", target: "n-proc", done: true },
        { source: "n-proc", target: "n-export", done: true },
      ],
      completed: 3,
      total: 3,
    };
  }

  return {
    run_id: id,
    workflow_name: "unknown",
    status: "Completed",
    now: d(0),
    nodes: [],
    edges: [],
    completed: 0,
    total: 0,
  };
};

const getRunRecovery = (id: string): RunRecovery => {
  if (id === "run-durability-1") {
    return {
      run_id: id,
      status: "Completed",
      now: d(0),
      workers: ["activity-worker-a1", "activity-worker-dead"],
      spans: [
        {
          activity_id: "act-dur-2", node_id: "n-proc", activity_type: "activity-proc",
          attempt: 1, worker: "activity-worker-dead", outcome: "lost",
          started_at: d(7170), ended_at: d(7020), failure: "Worker process lost",
          lost_attempts: 1, approximate: true
        },
        {
          activity_id: "act-dur-2", node_id: "n-proc", activity_type: "activity-proc",
          attempt: 2, worker: "activity-worker-a1", outcome: "completed",
          started_at: d(7000), ended_at: d(6950), failure: null,
          lost_attempts: 0, approximate: false
        }
      ],
      markers: [
        { at: d(7020), kind: "worker_lost", label: "Worker Disconnected", detail: "SIGKILL" }
      ],
      windows: [],
      replayed_activities: 1,
      handoffs: 1,
    };
  }
  return {
    run_id: id,
    status: "Running",
    now: d(0),
    workers: [], spans: [], markers: [], windows: [], replayed_activities: 0, handoffs: 0
  };
};

export const mockReq = async (path: string, init?: RequestInit): Promise<any> => {
  // Add an artificial delay to simulate network latency
  await new Promise((res) => setTimeout(res, 200));

  if (path === "/healthz") return { status: "ok", checks: {} };
  if (path === "/v1/version") return { service: "api", version: "0.1.0-demo", environment: "demo" };
  if (path === "/v1/workflows") return mockWorkflows;
  if (path === "/v1/runs") return mockRuns;
  
  if (path.startsWith("/v1/runs/")) {
    const parts = path.split("/");
    const id = parts[3];
    const sub = parts[4];

    if (!sub) return mockRuns.find(r => r.id === id) || mockRuns[0];
    if (sub === "activities") return { run_id: id, status: "Running", status_note: null, activities: [] };
    if (sub === "recovery") return getRunRecovery(id);
    if (sub === "graph") return getRunGraph(id);
    if (sub === "cost") return { run_id: id, total_usd: 0.15, input_tokens: 15000, output_tokens: 2000, gpu_seconds: 0, by_node: [], by_model: [], by_provider: [], lines: [] };
    if (sub === "retries") return [];
  }

  if (path === "/v1/workers") return mockWorkers;
  if (path === "/v1/queues") return mockQueues;
  
  if (path.startsWith("/v1/approvals")) {
    if (init?.method === "POST") {
      return { ...mockApprovals[0], status: "approved" };
    }
    return mockApprovals;
  }

  if (path === "/v1/plugins") return mockNodes;
  
  if (path === "/v1/chaos") return { enabled: true, project: "demo", targets: [], events: [], reason: null };
  if (path === "/v1/chaos/inject") return { service: "activity-worker", name: "worker", state: "dead", killable: true };

  // Fallback for Start Run, cancel run etc.
  if (init?.method === "POST" && path.includes("/runs")) {
     return { run_id: "new-run-123", temporal_wf_id: "wf-123", status: "Running", links: { self: "", stream: "" }};
  }

  throw new Error(`Demo Mode: Path ${path} is not mocked.`);
};
