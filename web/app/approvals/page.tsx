"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Check, Clock, ExternalLink, Hourglass, X } from "lucide-react";
import { api, type Approval, type ApprovalStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const TABS: { value: string; label: string }[] = [
  { value: "waiting", label: "Waiting" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
  { value: "all", label: "All" },
];

const STATUS_STYLES: Record<ApprovalStatus, string> = {
  waiting: "bg-warning/15 text-warning",
  approved: "bg-success/15 text-success",
  rejected: "bg-danger/15 text-danger",
  expired: "bg-muted text-muted-foreground",
};

function relTime(iso: string): string {
  const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`;
  return `${Math.round(secs / 86400)}d ago`;
}

function expiresIn(iso: string | null): string | null {
  if (!iso) return null;
  const secs = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  if (secs <= 0) return "expired";
  if (secs < 3600) return `expires in ${Math.round(secs / 60)}m`;
  if (secs < 86400) return `expires in ${Math.round(secs / 3600)}h`;
  return `expires in ${Math.round(secs / 86400)}d`;
}

function GateCard({
  gate,
  onDecided,
}: {
  gate: Approval;
  onDecided: () => void;
}) {
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const decide = async (approved: boolean) => {
    setBusy(approved ? "approve" : "reject");
    setError(null);
    try {
      await api.decideApproval(gate.id, { approved, comment });
      onDecided();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to submit decision");
    } finally {
      setBusy(null);
    }
  };

  const waiting = gate.status === "waiting";
  const expiry = expiresIn(gate.expires_at);

  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm">{gate.gate_id}</span>
        <span
          className={cn(
            "rounded px-1.5 py-0.5 text-[10px] font-medium uppercase",
            STATUS_STYLES[gate.status],
          )}
        >
          {gate.status}
        </span>
        {gate.workflow_name && (
          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            {gate.workflow_name}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
          <Clock className="h-3 w-3" />
          {relTime(gate.requested_at)}
          {expiry && <span className="text-warning"> · {expiry}</span>}
        </span>
      </div>

      {gate.prompt && <p className="mt-2 text-sm">{gate.prompt}</p>}

      {gate.payload && Object.keys(gate.payload).length > 0 && (
        <pre className="mt-3 max-h-48 overflow-auto rounded bg-muted/50 p-3 text-[11px] leading-relaxed">
          {JSON.stringify(gate.payload, null, 2)}
        </pre>
      )}

      <div className="mt-3 flex items-center gap-2 text-[11px] text-muted-foreground">
        <span className="font-mono">{gate.temporal_wf_id}</span>
        {gate.run_id && (
          <Link
            href={`/runs/${gate.run_id}`}
            className="inline-flex items-center gap-1 text-accent hover:underline"
          >
            open run <ExternalLink className="h-3 w-3" />
          </Link>
        )}
      </div>

      {waiting ? (
        <div className="mt-4 space-y-2">
          <input
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Comment (optional) — recorded with the decision"
            className="w-full rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
          />
          <div className="flex gap-2">
            <button
              onClick={() => decide(true)}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-md bg-success/15 px-3 py-1.5 text-sm text-success transition-colors hover:bg-success/25 disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
            <button
              onClick={() => decide(false)}
              disabled={busy !== null}
              className="inline-flex items-center gap-1.5 rounded-md bg-danger/15 px-3 py-1.5 text-sm text-danger transition-colors hover:bg-danger/25 disabled:opacity-50"
            >
              <X className="h-4 w-4" />
              {busy === "reject" ? "Rejecting…" : "Reject"}
            </button>
          </div>
          {error && <p className="text-xs text-danger">{error}</p>}
        </div>
      ) : (
        <div className="mt-3 border-t pt-3 text-xs text-muted-foreground">
          {gate.decided_at ? (
            <>
              {gate.status} {relTime(gate.decided_at)}
              {gate.decided_by ? ` by ${gate.decided_by}` : ""}
              {gate.comment ? ` — “${gate.comment}”` : ""}
            </>
          ) : (
            "no decision recorded"
          )}
        </div>
      )}
    </div>
  );
}

export default function ApprovalsPage() {
  const [status, setStatus] = useState("waiting");
  const [gates, setGates] = useState<Approval[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(
    (signal?: AbortSignal) => {
      api
        .listApprovals(status, signal)
        .then((g) => {
          setGates(g);
          setError(null);
        })
        .catch((e) => {
          if (!signal?.aborted) setError(e instanceof Error ? e.message : "failed to load");
        });
    },
    [status],
  );

  useEffect(() => {
    const c = new AbortController();
    setGates(null);
    load(c.signal);
    const t = setInterval(() => load(c.signal), 5000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [load]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold tracking-tight">Approvals</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Runs parked at a human gate. A waiting workflow consumes no compute — it
          is a durable condition in Temporal, not a held process, so it survives
          worker restarts and can wait for days. Approving here sends the signal
          that resumes it; the decision that counts lives in the workflow&apos;s
          history, and this list is only the index that makes it findable.
        </p>
      </div>

      <div className="flex gap-1">
        {TABS.map((t) => (
          <button
            key={t.value}
            onClick={() => setStatus(t.value)}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm transition-colors",
              status === t.value
                ? "bg-accent/15 text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="rounded-lg border border-danger/40 bg-card p-3 text-sm text-muted-foreground">
          API error: {error}. Is the stack running?
        </div>
      )}

      {gates === null && !error && <div className="text-sm text-muted-foreground">Loading…</div>}

      {gates?.length === 0 && (
        <div className="flex items-center gap-2 rounded-lg border bg-card p-4 text-sm text-muted-foreground">
          <Hourglass className="h-4 w-4" />
          Nothing {status === "all" ? "recorded" : status} right now. Start the{" "}
          <code className="rounded bg-muted px-1">research_agent</code> or{" "}
          <code className="rounded bg-muted px-1">human_gate</code> workflow to
          create one.
        </div>
      )}

      <div className="grid gap-3 lg:grid-cols-2">
        {gates?.map((g) => (
          <GateCard key={g.id} gate={g} onDecided={() => load()} />
        ))}
      </div>
    </div>
  );
}
