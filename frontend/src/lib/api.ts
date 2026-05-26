import type { AgentState, CreateRunResponse, HealthResponse } from "../types/agent";

export function sanitizeForLogging(token: string): string {
  if (!token) return "";
  return `${token.slice(0, 4)}****`;
}

async function parseError(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    }
    if (body.message) return String(body.message);
    return JSON.stringify(body);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

export async function createRun(
  issueUrl: string,
  githubToken: string,
  modelName = "claude-sonnet-4-20250514"
): Promise<CreateRunResponse> {
  const payload: Record<string, string> = {
    issue_url: issueUrl,
    model_name: modelName,
  };

  if (githubToken.trim()) {
    payload.github_token = githubToken.trim();
  }

  const res = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    throw new Error(await parseError(res));
  }

  return res.json() as Promise<CreateRunResponse>;
}

export async function getRun(runId: string): Promise<AgentState> {
  const res = await fetch(`/api/runs/${runId}`);
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.json() as Promise<AgentState>;
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch("/api/health");
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  return res.json() as Promise<HealthResponse>;
}
