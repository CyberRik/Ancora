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

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`${path} → ${res.status}`);
  }
  return (await res.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => getJson<HealthStatus>("/healthz", signal),
  version: (signal?: AbortSignal) => getJson<VersionInfo>("/v1/version", signal),
};
