#!/usr/bin/env python
"""Phase 2 end-to-end smoke check against a running stack.

Exercises the execution runtime through the public API:
  1. an activity worker is registered and live (GET /v1/workers)
  2. the cpu capability queue has a live worker (GET /v1/queues)
  3. the 'pipeline' workflow dispatches a compute activity to the runtime and
     completes with the expected result (POST .../runs → poll GET /v1/runs/{id})

Usage:
    uv run python scripts/e2e_phase2.py [--base-url http://localhost:8080]
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

TERMINAL = {"Completed", "Failed", "Cancelled", "Terminated", "TimedOut"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=10.0) as client:
        # 1. an activity worker is registered + live
        workers = client.get("/v1/workers").raise_for_status().json()
        live = [w for w in workers if w["status"] == "live"]
        print(f"[1] workers: {[(w['worker_id'], w['status'], w['pools']) for w in workers]}")
        assert live, "no live activity worker registered"

        # 2. the cpu queue has a live worker
        queues = {q["queue"]: q for q in client.get("/v1/queues").raise_for_status().json()}
        print(f"[2] queues: {[(q, v['live_worker_count']) for q, v in queues.items()]}")
        assert queues["ancora-cpu"]["live_worker_count"] >= 1, "no live worker on ancora-cpu"

        # 3. run the pipeline workflow (dispatches a compute activity to the runtime)
        resp = client.post(
            "/v1/workflows/pipeline/runs",
            json={"input": {"label": "e2e", "batches": 5, "batch_seconds": 0.1}},
        )
        resp.raise_for_status()
        run_id = resp.json()["run_id"]
        print(f"[3] started pipeline run {run_id}; polling…")

        deadline = time.time() + args.timeout
        status, output = "Queued", None
        while time.time() < deadline:
            run = client.get(f"/v1/runs/{run_id}").raise_for_status().json()
            status = run["status"]
            if status in TERMINAL:
                output = run["output"]
                break
            time.sleep(0.5)
        print(f"    final status: {status}; output: {output}")
        assert status == "Completed", f"expected Completed, got {status}"
        assert output and output.get("compute", {}).get("batches") == 5
        # checksum = 7 * sum(1..5) = 105
        assert output["compute"]["checksum"] == 105, output

    print("\n[PASS] Phase 2 end-to-end smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
