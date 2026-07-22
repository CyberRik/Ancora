import { chromium } from "@playwright/test";
const OUT = process.argv[2] || ".";
const BASE = "http://localhost:3100";
const state = { phase: "reschedule" };

function runObj(status, output = null) {
  return { id: "demo-1", workflow_name: "durability_demo", version: 3, temporal_wf_id: "durability_demo-abc", temporal_run_id: "r1", status, input: { message: "start" }, output, error: null, started_at: new Date(Date.now() - 6000).toISOString(), closed_at: status === "Completed" ? new Date().toISOString() : null, created_at: new Date(Date.now() - 6000).toISOString() };
}
function live() {
  if (state.phase === "reschedule") return { run_id: "demo-1", status: "Running", status_note: "Processing records (a worker will fail here)...", activities: [{ activity_id: "2", activity_type: "process_records", state: "Scheduled", attempt: 2, maximum_attempts: 5, last_failure: "Worker failure: out-of-memory while processing records", last_worker_identity: "worker@node1" }] };
  return { run_id: "demo-1", status: "Completed", status_note: null, activities: [] };
}
const browser = await chromium.launch();
async function mk() {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1250 }, colorScheme: "dark" });
  await page.route("**/v1/**", (route) => {
    const url = route.request().url(), method = route.request().method();
    let body = {};
    if (method === "POST" && url.endsWith("/runs")) body = { run_id: "demo-1", temporal_wf_id: "durability_demo-abc", status: "Running", links: { self: "", stream: "" } };
    else if (url.match(/\/v1\/runs\/demo-1\/activities$/)) body = live();
    else if (url.match(/\/v1\/runs\/demo-1$/)) body = state.phase === "done" ? runObj("Completed", { ingest: { status: "ingested", records: 1000000, size: "5 GB" }, process: { status: "processed", records: 1000000, recovered_on_attempt: 2 }, export: { status: "exported", location: "s3://ancora-demo/results.parquet" }, message: "Pipeline finished despite a mid-run worker failure." }) : runObj("Running");
    else if (url.endsWith("/v1/runs")) body = [runObj(state.phase === "done" ? "Completed" : "Running")];
    else body = {};
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  return page;
}
async function shot(page, file) {
  await page.goto(`${BASE}/demo`, { waitUntil: "networkidle" }).catch(() => {});
  await page.getByRole("button", { name: /Run the pipeline|Run it again/i }).click().catch((e) => console.log("click", e.message));
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${OUT}/${file}`, fullPage: true });
  console.log("shot", file);
}
const p1 = await mk(); state.phase = "reschedule"; await shot(p1, "demo-reschedule.png");
await browser.close(); console.log("done");
