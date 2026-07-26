"use client";

/**
 * Chaos experiments — injection that asserts.
 *
 * The kill buttons prove recovery *happens*. An experiment proves it happened
 * *correctly*: it starts a run, SIGKILLs a worker mid-flight, waits out the real
 * recovery, then checks the invariants (no lost state, no re-executed activity,
 * no double-fired effect) and measures the recovery time. The verdict is a
 * pass/fail with a number, not a vibe — a regression test you can watch.
 */

import { useCallback, useEffect, useState } from "react";
import { Activity, Check, FlaskConical, Loader2, X } from "lucide-react";
import {
  api,
  type ChaosExperimentResult,
  type ChaosExperiments as ChaosExperimentsData,
  type InvariantResult,
} from "@/lib/api";
import { Alert, Button, Card, Chip } from "@/components/ui";
import { cn } from "@/lib/utils";

export function ChaosExperiments() {
  const [data, setData] = useState<ChaosExperimentsData | null>(null);
  const [running, setRunning] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [results, setResults] = useState<Record<string, ChaosExperimentResult>>({});
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((signal?: AbortSignal) => {
    api
      .chaosExperiments(signal)
      .then(setData)
      .catch(() => {
        /* the parent page already surfaces a down API */
      });
  }, []);

  useEffect(() => {
    const c = new AbortController();
    load(c.signal);
    return () => c.abort();
  }, [load]);

  // Elapsed-time ticker while an experiment runs (it waits out real recovery).
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [running]);

  async function run(name: string) {
    setRunning(name);
    setElapsed(0);
    setError(null);
    try {
      const r = await api.runChaosExperiment(name);
      setResults((prev) => ({ ...prev, [name]: r }));
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "experiment failed");
    } finally {
      setRunning(null);
    }
  }

  if (!data) return null;
  if (!data.enabled) {
    return (
      <section className="space-y-3">
        <SectionHeader />
        <Alert title="Experiments need the Docker socket">
          {data.reason ?? "Chaos is disabled in this deployment."}
        </Alert>
      </section>
    );
  }

  return (
    <section className="space-y-4">
      <SectionHeader />
      {error && <Alert title="Experiment error">{error}</Alert>}
      <div className="grid gap-3 lg:grid-cols-2">
        {data.scenarios.map((s) => {
          const result = results[s.name];
          const isRunning = running === s.name;
          return (
            <Card key={s.name} className="flex flex-col gap-3 p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h4 className="text-sm font-medium">{s.title}</h4>
                  <p className="mt-1 text-xs leading-snug text-muted-foreground">
                    {s.description}
                  </p>
                </div>
                {result && (
                  <span
                    className={cn(
                      "shrink-0 rounded-full px-2 py-0.5 font-mono text-[10px] font-semibold uppercase tracking-wider",
                      result.passed
                        ? "bg-success/15 text-success"
                        : "bg-danger/15 text-danger",
                    )}
                  >
                    {result.passed ? "Pass" : "Fail"}
                  </span>
                )}
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <Chip tone="flow">{s.workflow}</Chip>
                {s.invariants.map((inv) => (
                  <Chip key={inv} tone="muted">
                    {inv}
                  </Chip>
                ))}
              </div>

              {result ? (
                <ResultPanel result={result} />
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  {s.fault}
                  {s.expected_rto_seconds != null && (
                    <> · target recovery ≤ {s.expected_rto_seconds}s</>
                  )}
                </p>
              )}

              <Button
                onClick={() => run(s.name)}
                disabled={running !== null}
                className="mt-auto w-full justify-center"
              >
                {isRunning ? (
                  <span className="flex items-center gap-2">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Killing a worker & waiting for recovery… {elapsed}s
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <FlaskConical className="h-3.5 w-3.5" />
                    {result ? "Run again" : "Run experiment"}
                  </span>
                )}
              </Button>
            </Card>
          );
        })}
      </div>
    </section>
  );
}

function SectionHeader() {
  return (
    <div>
      <h3 className="flex items-center gap-2 text-sm font-medium">
        <FlaskConical className="h-4 w-4 text-accent" />
        Experiments — chaos that asserts
      </h3>
      <p className="mt-1 max-w-2xl text-xs text-muted-foreground">
        A button that kills a worker proves recovery <em>happens</em>. An experiment
        proves it happened <em>correctly</em>: it injects the fault mid-run, waits out
        the real recovery, then asserts the invariants and measures the recovery time.
      </p>
    </div>
  );
}

function ResultPanel({ result }: { result: ChaosExperimentResult }) {
  const rto = result.rto_seconds;
  const expected = result.expected_rto_seconds;
  const withinTarget = rto != null && expected != null && rto <= expected;

  return (
    <div className="space-y-2 rounded-lg border bg-elevated/40 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
        <span className="text-muted-foreground">
          killed <span className="font-mono text-foreground">{result.killed ?? "—"}</span> ·{" "}
          {result.final_status}
        </span>
        <span className="flex items-center gap-1.5 font-mono">
          <Activity className="h-3 w-3 text-accent" />
          <span
            className={cn(
              "tabular-nums font-semibold",
              withinTarget ? "text-success" : "text-warning",
            )}
          >
            RTO {rto != null ? `${rto}s` : "—"}
          </span>
          {expected != null && (
            <span className="text-muted-foreground">/ ≤{expected}s</span>
          )}
        </span>
      </div>
      <ul className="space-y-1">
        {result.invariants.map((inv) => (
          <InvariantRow key={inv.name} inv={inv} />
        ))}
      </ul>
      {result.note && <p className="text-[11px] text-warning">{result.note}</p>}
    </div>
  );
}

function InvariantRow({ inv }: { inv: InvariantResult }) {
  return (
    <li className="flex items-start gap-2 text-[11px]">
      {inv.passed ? (
        <Check className="mt-0.5 h-3 w-3 shrink-0 text-success" />
      ) : (
        <X className="mt-0.5 h-3 w-3 shrink-0 text-danger" />
      )}
      <span className="min-w-0">
        <span className={cn("font-mono", inv.passed ? "text-foreground" : "text-danger")}>
          {inv.name}
        </span>
        <span className="text-muted-foreground"> — {inv.detail}</span>
      </span>
    </li>
  );
}
