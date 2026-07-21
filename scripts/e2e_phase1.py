#!/usr/bin/env python
"""Phase 1 end-to-end smoke check against a running stack.

Exercises the durable path through the public API:
  1. the worker has registered workflows (GET /v1/workflows)
  2. starting a run executes and completes (POST .../runs → poll GET /v1/runs/{id})
  3. idempotency: the same Idempotency-Key returns the same run

Usage:
    uv run python scripts/e2e_phase1.py [--base-url http://localhost:8080]
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
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    with httpx.Client(base_url=base, timeout=10.0) as client:
        # 1. workflows registered
        defs = client.get("/v1/workflows").raise_for_status().json()
        names = {d["name"] for d in defs}
        print(f"[1] registered workflows: {sorted(names)}")
        assert "hello" in names, "worker did not register the 'hello' workflow"

        # 2. start a run and wait for completion
        resp = client.post(
            "/v1/workflows/hello/runs", json={"input": {"name": "Ada"}}
        )
        resp.raise_for_status()
        run_id = resp.json()["run_id"]
        print(f"[2] started run {run_id}; polling…")

        deadline = time.time() + args.timeout
        status = "Queued"
        output = None
        while time.time() < deadline:
            run = client.get(f"/v1/runs/{run_id}").raise_for_status().json()
            status = run["status"]
            if status in TERMINAL:
                output = run["output"]
                break
            time.sleep(0.5)
        print(f"    final status: {status}; output: {output}")
        assert status == "Completed", f"expected Completed, got {status}"
        assert output and output.get("message") == "Hello, Hello, Hello, Ada!!!"
        assert output.get("steps") == 3

        # 3. idempotency
        key = "e2e-idem-key-123"
        r1 = client.post(
            "/v1/workflows/hello/runs",
            json={"input": {"name": "Bob"}},
            headers={"Idempotency-Key": key},
        ).raise_for_status().json()
        r2 = client.post(
            "/v1/workflows/hello/runs",
            json={"input": {"name": "Bob"}},
            headers={"Idempotency-Key": key},
        ).raise_for_status().json()
        print(f"[3] idempotency: {r1['run_id']} == {r2['run_id']}")
        assert r1["run_id"] == r2["run_id"], "idempotency key did not dedupe"

    print("\n✅ Phase 1 end-to-end smoke passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
