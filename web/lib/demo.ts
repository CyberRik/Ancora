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
  GraphNodeKind,
  GraphNodeState,
} from "./api";

const d = (secondsAgo: number) => new Date(Date.now() - secondsAgo * 1000).toISOString();

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

const mockChaosStatus: ChaosStatus = {
  enabled: true,
  project: "demo",
  targets: [
    { service: "worker", name: "ancora-worker-1", state: "running", killable: true },
    { service: "activity-worker", name: "ancora-activity-1", state: "running", killable: true },
    { service: "scheduler", name: "ancora-scheduler-1", state: "running", killable: true },
  ],
  events: [],
  reason: null,
};

const mockRecoveries: Record<string, RunRecovery> = {};

const getRunGraph = (id: string): RunGraph => {
  const run = mockRuns.find((r) => r.id === id);
  if (run?.workflow_name === "research_agent") {
    const isCompleted = run?.status === "Completed";
    
    return {
      run_id: id,
      workflow_name: "research_agent",
      status: run?.status || "Running",
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
          layer: 3, state: isCompleted ? "completed" : "waiting", attempts: 1, lost_attempts: 0,
          worker: "workflow-worker-b2", queue: "ancora-wf", priority: "default",
          started_at: d(120), ended_at: isCompleted ? d(5) : null, duration_seconds: isCompleted ? 115 : null,
          failure: null, approved: isCompleted ? true : null, decided_by: isCompleted ? "demo-user" : null, timed_out: false, note: isCompleted ? "Approved" : "waiting for user"
        },
        ...(isCompleted ? [{
          id: "n-publish", label: "publish", kind: "activity" as GraphNodeKind, node_type: "http",
          activity_type: "activity-publish", activity_id: "act-6",
          layer: 4, state: "completed" as GraphNodeState, attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: d(5), ended_at: d(0), duration_seconds: 5,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        }] : [])
      ],
      edges: [
        { source: "n-search", target: "n-sum-1", done: true },
        { source: "n-search", target: "n-sum-2", done: true },
        { source: "n-search", target: "n-sum-3", done: true },
        { source: "n-sum-1", target: "n-synth", done: true },
        { source: "n-sum-2", target: "n-synth", done: true },
        { source: "n-sum-3", target: "n-synth", done: true },
        { source: "n-synth", target: "n-gate", done: true },
        ...(isCompleted ? [{ source: "n-gate", target: "n-publish", done: true }] : [])
      ],
      completed: isCompleted ? 7 : 5,
      total: isCompleted ? 7 : 6,
    };
  }
  
  if (run?.workflow_name === "durability_demo") {
    const isDynamic = id !== "run-durability-1";
    let elapsed = isDynamic ? (Date.now() - new Date(run.started_at!).getTime()) / 1000 : 1000;
    
    // For the static run or completed dynamic run
    if (run.status === "Completed") {
      elapsed = 1000;
    }

    return {
      run_id: id,
      workflow_name: "durability_demo",
      status: run.status,
      now: d(0),
      nodes: [
        {
          id: "n-ingest", label: "ingest_dataset", kind: "activity" as GraphNodeKind, node_type: "http",
          activity_type: "activity-ingest", activity_id: "act-dur-1",
          layer: 0, state: elapsed >= 3 ? "completed" as GraphNodeState : "waiting" as GraphNodeState, attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: isDynamic ? run.started_at : d(7200), ended_at: elapsed >= 3 ? (isDynamic ? d(0) : d(7180)) : null, duration_seconds: 20,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        },
        ...(elapsed >= 3 ? [{
          id: "n-proc", label: "process_records", kind: "activity" as GraphNodeKind, node_type: "python",
          activity_type: "activity-proc", activity_id: "act-dur-2",
          layer: 1, state: elapsed >= 14 ? "completed" as GraphNodeState : (elapsed >= 6 && elapsed < 11 ? "waiting" as GraphNodeState : "running" as GraphNodeState), 
          attempts: elapsed >= 11 ? 2 : 1, lost_attempts: elapsed >= 6 ? 1 : 0,
          worker: elapsed >= 11 ? "activity-worker-a2" : "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: isDynamic ? d(0) : d(7000), ended_at: elapsed >= 14 ? (isDynamic ? d(0) : d(6950)) : null, duration_seconds: 50,
          failure: elapsed >= 6 && elapsed < 11 ? "Worker process lost (SIGKILL)" : null, approved: null, decided_by: null, timed_out: false, note: elapsed >= 6 ? "recovering from crash" : null
        }] : []),
        ...(elapsed >= 14 ? [{
          id: "n-export", label: "export_results", kind: "activity" as GraphNodeKind, node_type: "http",
          activity_type: "activity-export", activity_id: "act-dur-3",
          layer: 2, state: elapsed >= 16 ? "completed" as GraphNodeState : "running" as GraphNodeState, attempts: 1, lost_attempts: 0,
          worker: "activity-worker-a1", queue: "ancora-cpu", priority: "default",
          started_at: isDynamic ? d(0) : d(6940), ended_at: elapsed >= 16 ? (isDynamic ? d(0) : d(6900)) : null, duration_seconds: 40,
          failure: null, approved: null, decided_by: null, timed_out: false, note: null
        }] : [])
      ],
      edges: [
        ...(elapsed >= 3 ? [{ source: "n-ingest", target: "n-proc", done: elapsed >= 14 }] : []),
        ...(elapsed >= 14 ? [{ source: "n-proc", target: "n-export", done: elapsed >= 16 }] : []),
      ],
      completed: elapsed >= 16 ? 3 : (elapsed >= 14 ? 2 : (elapsed >= 3 ? 1 : 0)),
      total: elapsed >= 14 ? 3 : (elapsed >= 3 ? 2 : 1),
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
  if (mockRecoveries[id]) {
    const rec = mockRecoveries[id];
    if (rec.windows && rec.windows.length > 0) {
      const window = rec.windows[0];
      const startMs = new Date(window.started_at!).getTime();
      const nowMs = Date.now();
      const elapsedSec = (nowMs - startMs) / 1000;
      
      if (elapsedSec >= window.timeout_seconds!) {
        // Expired! Mutate permanently
        rec.windows = [];
        const span2 = rec.spans.find(s => s.attempt === 2);
        if (span2) {
          span2.started_at = window.deadline_at;
          span2.outcome = "running";
        }
      }
    }
    
    // Create a copy to return, so we can adjust it for the current tick
    const out = JSON.parse(JSON.stringify(rec)) as RunRecovery;
    out.now = new Date().toISOString(); // Anchor the clock to now
    
    // If still detecting, attempt 2 doesn't exist yet
    if (out.windows && out.windows.length > 0) {
      out.spans = out.spans.filter(s => s.attempt !== 2);
    }
    
    return out;
  }
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
  
  if (init?.method === "POST" && path.match(/^\/v1\/workflows\/(.+)\/runs$/)) {
    const wf = path.split("/")[3];
    const id = `new-run-${Date.now()}`;
    const newRun: Run = {
      id,
      workflow_name: wf,
      version: 1,
      temporal_wf_id: `wf-${id}`,
      temporal_run_id: `tr-${id}`,
      status: "Running",
      input: { demo: true },
      output: null,
      error: null,
      started_at: new Date().toISOString(),
      closed_at: null,
      created_at: new Date().toISOString(),
    };
    mockRuns.push(newRun);
    return { run_id: id, temporal_wf_id: newRun.temporal_wf_id, status: "Running", links: { self: "", stream: "" }};
  }
  
  if (path.startsWith("/v1/runs/")) {
    const parts = path.split("/");
    const id = parts[3];
    const sub = parts[4];
    
    let run = mockRuns.find(r => r.id === id);
    if (!run) run = mockRuns[0];
    
    let simStatus = run.status;
    let simNote = null;
    let simActivities: any[] = [];
    
    if (run.workflow_name === "durability_demo" && run.status === "Running") {
      const elapsed = (Date.now() - new Date(run.started_at!).getTime()) / 1000;
      if (elapsed < 3) {
        simNote = "ingesting";
      } else if (elapsed < 6) {
        simNote = "processing";
        simActivities = [{ activity_id: "act-proc", activity_type: "process", state: "Started", attempt: 1, maximum_attempts: 10, last_failure: null, last_worker_identity: "worker-1" }];
      } else if (elapsed < 11) {
        simNote = "processing";
        simActivities = [{ activity_id: "act-proc", activity_type: "process", state: "Scheduled", attempt: 2, maximum_attempts: 10, last_failure: "Worker process lost (SIGKILL)", last_worker_identity: "worker-1" }];
      } else if (elapsed < 14) {
        simNote = "processing";
        simActivities = [{ activity_id: "act-proc", activity_type: "process", state: "Started", attempt: 2, maximum_attempts: 10, last_failure: "Worker process lost (SIGKILL)", last_worker_identity: "worker-2" }];
      } else if (elapsed < 16) {
        simNote = "exporting";
      } else {
        run.status = "Completed";
        run.closed_at = new Date().toISOString();
        run.output = { process: { recovered_on_attempt: 2 } };
        simStatus = "Completed";
      }
    }

    if (!sub) return run;
    if (sub === "activities") return { run_id: id, status: simStatus, status_note: simNote, activities: simActivities };
    if (sub === "recovery") return getRunRecovery(id);
    if (sub === "graph") return getRunGraph(id);
    if (sub === "cost") return { run_id: id, total_usd: 0.15, input_tokens: 15000, output_tokens: 2000, gpu_seconds: 0, by_node: [], by_model: [], by_provider: [], lines: [] };
    if (sub === "retries") return [];
    
    if (sub === "signals") {
      const run = mockRuns.find(r => r.id === id);
      if (run) {
        run.status = "Completed";
        run.closed_at = d(0);
      }
      return run;
    }
  }

  if (path === "/v1/workers") return mockWorkers;
  if (path === "/v1/queues") return mockQueues;
  
  if (path.startsWith("/v1/approvals")) {
    if (init?.method === "POST") {
      const p = path.split("/");
      const pk = p[3];
      const gate = mockApprovals.find(a => a.id === pk || a.gate_id === pk);
      if (gate) gate.status = "approved";
      return { ...mockApprovals[0], status: "approved" };
    }
    return mockApprovals.filter(a => a.status === "waiting");
  }

  if (path === "/v1/plugins") return mockNodes;
  
  if (path === "/v1/chaos") return mockChaosStatus;
  
  if (path === "/v1/chaos/inject") {
    const body = JSON.parse(init?.body as string);
    const target = mockChaosStatus.targets.find(t => t.service === body.service);
    if (target) {
      if (body.action === "kill") target.state = "dead";
      else if (body.action === "restart") target.state = "running";
      
      mockChaosStatus.events.unshift({
        action: body.action,
        service: body.service,
        at: Date.now() / 1000,
        detail: body.action === "kill" ? "SIGKILL" : "Container restarted"
      });
      
      if (body.action === "kill") {
         const latestRun = mockRuns.find(r => r.workflow_name === "research_agent" && r.status === "Running");
         if (latestRun) {
            mockRecoveries[latestRun.id] = {
               run_id: latestRun.id,
               status: "Running",
               now: d(0),
               workers: [target.name, "ancora-activity-2"],
               spans: [
                 { activity_id: "act-1", node_id: "n-search", activity_type: "activity-search", attempt: 1, worker: target.name, outcome: "lost", started_at: d(20), ended_at: d(0), failure: "Worker process lost", lost_attempts: 1, approximate: true },
                 { activity_id: "act-1", node_id: "n-search", activity_type: "activity-search", attempt: 2, worker: "ancora-activity-2", outcome: "running", started_at: d(0), ended_at: null, failure: null, lost_attempts: 0, approximate: false }
               ],
               markers: [
                 { at: d(0), kind: "worker_lost", label: "Worker Disconnected", detail: "SIGKILL" }
               ],
               windows: [
                 {
                   activity_id: "act-1",
                   node_id: "n-search",
                   kind: "detecting",
                   clock: "start-to-close",
                   attempt: 1,
                   worker: target.name,
                   worker_state: "gone",
                   queue: "ancora-cpu",
                   queue_has_worker: true,
                   started_at: d(20),
                   deadline_at: d(-40),
                   timeout_seconds: 60,
                   elapsed_seconds: 20,
                   remaining_seconds: 40,
                   heartbeat_at: null,
                   heartbeat_timeout_seconds: null,
                   reason: "Waiting for start-to-close timeout to expire. The server cannot distinguish a dead worker from a slow one without a heartbeat."
                 }
               ],
               replayed_activities: 3,
               handoffs: 1
            };
         }
      }
    }
    return target || {};
  }

  if (init?.method === "POST" && path.includes("/signals/")) {
    const parts = path.split("/");
    const id = parts[3];
    const signalName = parts[5];
    const run = mockRuns.find((r) => r.id === id);
    if (run) {
      if (signalName === "submit_decision" || signalName === "approve") {
        run.status = "Completed";
        run.closed_at = d(0);
        run.output = {
          status: "published",
          report: "Agentic systems represent the next phase of durable AI workflows...",
          sources: "Various synthetic sources",
          summaries: 3,
          cost_usd: 0.15,
        };
      }
      return run;
    }
  }

  // Fallback for cancel run etc.
  if (init?.method === "POST" && path.includes("/runs")) {
     return { run_id: "fallback-run-123", temporal_wf_id: "wf-123", status: "Running", links: { self: "", stream: "" }};
  }

  throw new Error(`Demo Mode: Path ${path} is not mocked.`);
};
