"use client";

import { useEffect, useState } from "react";
import { Inbox, ServerCog } from "lucide-react";
import { api, type Queue, type Worker } from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  Alert,
  Card,
  Chip,
  EmptyState,
  PageHeader,
  Section,
  Skeleton,
  SkeletonCards,
} from "@/components/ui";

const STATUS_STYLES: Record<Worker["status"], string> = {
  live: "border-success/30 bg-success/10 text-success",
  stale: "border-danger/30 bg-danger/10 text-danger",
  unknown: "border-border-strong bg-muted text-muted-foreground",
};

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

export default function WorkersPage() {
  const [workers, setWorkers] = useState<Worker[] | null>(null);
  const [queues, setQueues] = useState<Queue[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const c = new AbortController();
    const load = () => {
      Promise.all([api.listWorkers(c.signal), api.listQueues(c.signal)])
        .then(([w, q]) => {
          setWorkers(w);
          setQueues(q);
          setError(null);
        })
        .catch((e) => {
          if (!c.signal.aborted) setError(e instanceof Error ? e.message : "failed to load");
        });
    };
    load();
    const t = setInterval(load, 3000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, []);

  const liveCount = workers?.filter((w) => w.status === "live").length ?? 0;

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Fleet"
        title="Workers"
        description={
          <>
            Activity workers and the capability queues they serve. Health is a Redis liveness TTL
            refreshed on each heartbeat — a worker goes{" "}
            <span className="font-medium text-danger">stale</span> when its TTL lapses, which is
            exactly what you see after a kill.
          </>
        }
        live={liveCount > 0}
      />

      {error && (
        <Alert title="Can't reach the control plane">
          {error}. Check the API is up on{" "}
          <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">:8080</code>.
        </Alert>
      )}

      <Section
        title="Queues"
        description="Work is routed by capability, not by hostname, so any worker advertising a pool can serve it."
      >
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {queues === null && !error && <SkeletonCards count={4} />}
          {queues?.length === 0 && (
            <EmptyState
              className="sm:col-span-2 lg:col-span-4"
              icon={Inbox}
              title="No queues registered"
              description="Queues appear once a worker starts and advertises the pools it can serve."
            />
          )}
          {queues?.map((q) => (
            <Card key={q.queue} className="p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-sm">{q.queue}</span>
                {q.capability && <Chip tone="flow">{q.capability}</Chip>}
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <div>
                  <div
                    data-numeric
                    className={cn(
                      "text-2xl font-semibold leading-none",
                      q.live_worker_count > 0 ? "text-success" : "text-danger",
                    )}
                  >
                    {q.live_worker_count}
                    <span className="text-sm font-normal text-muted-foreground">
                      /{q.worker_count}
                    </span>
                  </div>
                  <div className="eyebrow mt-1.5">Live</div>
                </div>
                <div>
                  <div
                    data-numeric
                    className={cn(
                      "text-2xl font-semibold leading-none",
                      q.backlog > 0 ? "text-warning" : "text-foreground",
                    )}
                  >
                    {q.backlog}
                  </div>
                  <div className="eyebrow mt-1.5">Backlog</div>
                </div>
              </div>
            </Card>
          ))}
        </div>
      </Section>

      <Section title="Registered workers">
        {workers === null && !error && (
          <div className="grid gap-3 md:grid-cols-2">
            {Array.from({ length: 2 }).map((_, i) => (
              <Card key={i} className="space-y-3 p-4">
                <div className="flex justify-between">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-12" />
                </div>
                <Skeleton className="h-4 w-24" />
                <Skeleton className="h-16 w-full" />
              </Card>
            ))}
          </div>
        )}

        {workers?.length === 0 && (
          <EmptyState
            icon={ServerCog}
            title="No workers registered"
            description={
              <>
                Nothing is polling for work right now. Start one with{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">
                  ancora-activity-worker
                </code>
                , or bring the whole stack up with{" "}
                <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">
                  make up
                </code>
                .
              </>
            }
          />
        )}

        <div className="grid gap-3 md:grid-cols-2">
          {workers?.map((w) => (
            <Card key={w.worker_id} className="p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-sm">{w.worker_id}</span>
                <span
                  className={cn(
                    "shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] font-medium uppercase tracking-wider",
                    STATUS_STYLES[w.status],
                  )}
                >
                  {w.status}
                </span>
              </div>

              {w.pools.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5">
                  {w.pools.map((p) => (
                    <Chip key={p} tone="flow">
                      {p}
                    </Chip>
                  ))}
                </div>
              )}

              <dl className="mt-4 space-y-1.5 border-t pt-3 text-xs">
                <Row label="CPUs" value={w.resources.total_cpus ?? "—"} />
                <Row
                  label="GPUs"
                  value={`${w.resources.total_gpus ?? 0}${
                    w.resources.accelerator_type ? ` · ${w.resources.accelerator_type}` : ""
                  }`}
                />
                <Row
                  label="Host / PID"
                  value={`${w.host ?? "—"}${w.pid ? ` · ${w.pid}` : ""}`}
                  mono
                />
                <Row label="Heartbeat" value={relTime(w.last_heartbeat_at)} />
              </dl>
            </Card>
          ))}
        </div>
      </Section>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd
        data-numeric
        className={cn("truncate text-right text-foreground", mono && "font-mono text-[11px]")}
      >
        {value}
      </dd>
    </div>
  );
}
