import { useEffect, useRef, useState } from "react";
import { API_URL } from "./utils";

/** A single frame from the run stream. `event` frames drive the live DAG. */
export interface StreamFrame {
  type: "hello" | "event" | "heartbeat" | "workers";
  id?: string;
  kind?: string;
  node_id?: string | null;
  activity_id?: string | null;
  activity_type?: string | null;
  attempt?: number;
  worker_id?: string | null;
  status?: string | null;
  error?: string | null;
  ts?: string;
  wf_id?: string;
  workers?: unknown[];
}

export type StreamState = "connecting" | "open" | "closed";

function wsUrl(path: string): string {
  // http(s)://host → ws(s)://host, same origin as the REST API.
  return `${API_URL.replace(/^http/, "ws")}${path}`;
}

/**
 * Subscribe to a run's live event stream.
 *
 * Reconnect is transparent and gap-free: the last stream id seen is remembered
 * and replayed on the next connect (`?last_id=`), so a dropped socket costs no
 * events. `onEvent` fires once per lifecycle event — the caller decides what to
 * refetch. The socket is torn down when `enabled` goes false (e.g. the run
 * reached a terminal state), so a finished run holds no open connection.
 */
export function useRunStream(
  runId: string,
  onEvent: (frame: StreamFrame) => void,
  enabled = true,
): StreamState {
  const [state, setState] = useState<StreamState>("connecting");
  // Kept in refs so reconnects read the latest without re-subscribing the effect.
  const lastIdRef = useRef<string>("0");
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    if (!enabled) {
      setState("closed");
      return;
    }
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let stopped = false;

    const connect = () => {
      if (stopped) return;
      setState("connecting");
      const url = wsUrl(
        `/v1/stream/runs/${runId}?last_id=${encodeURIComponent(lastIdRef.current)}`,
      );
      socket = new WebSocket(url);

      socket.onopen = () => {
        attempts = 0;
        setState("open");
      };
      socket.onmessage = (msg) => {
        let frame: StreamFrame;
        try {
          frame = JSON.parse(msg.data as string) as StreamFrame;
        } catch {
          return;
        }
        if (frame.type === "event" && frame.id) lastIdRef.current = frame.id;
        if (frame.type === "event") onEventRef.current(frame);
      };
      socket.onclose = () => {
        setState("closed");
        if (stopped) return;
        // Capped exponential backoff; reconnect replays from lastId.
        attempts += 1;
        const delay = Math.min(1000 * 2 ** (attempts - 1), 15000);
        retry = setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      stopped = true;
      if (retry) clearTimeout(retry);
      socket?.close();
    };
  }, [runId, enabled]);

  return state;
}
