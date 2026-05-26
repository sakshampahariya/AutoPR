/** TypeScript mirrors of backend/core/state.py and WebSocket event payloads. */

export type AgentName = "research" | "coding" | "testing" | "pr" | "system";
export type LogLevel = "info" | "warning" | "error" | "debug";
export type TestResultStatus = "pass" | "fail" | "error" | "pending";
export type FinalStatus = "running" | "success" | "failed";

export type AgentStatus = "idle" | "running" | "complete" | "failed";

/** WebSocket connection / run lifecycle in the UI. */
export type StreamRunStatus =
  | "idle"
  | "connecting"
  | "running"
  | "complete"
  | "failed";

export interface FileChange {
  file_path: string;
  original_content: string;
  patched_content: string;
  diff: string;
  change_description: string;
}

export interface TestResult {
  status: TestResultStatus;
  total_tests: number;
  passed: number;
  failed: number;
  errors: number;
  stdout: string;
  stderr: string;
  exit_code: number;
  duration_seconds: number;
}

export interface AgentLog {
  agent_name: AgentName;
  level: LogLevel;
  message: string;
  timestamp: string;
}

export interface AgentState {
  run_id: string;
  issue_url: string;
  repo_owner: string;
  repo_name: string;
  issue_number: number;
  github_token?: string;
  model_name: string;

  issue_title: string | null;
  issue_body: string | null;
  relevant_files: string[] | null;
  codebase_context: string | null;
  repo_structure: string | null;

  file_changes: FileChange[] | null;
  patch_summary: string | null;

  test_result: TestResult | null;
  retry_count: number;

  branch_name: string | null;
  pr_url: string | null;
  pr_number: number | null;

  logs: AgentLog[];
  current_node: string;
  final_status: FinalStatus;
  error_message: string | null;
}

export interface WebSocketEvent {
  type:
    | "agent_log"
    | "node_start"
    | "node_complete"
    | "state_update"
    | "diff_ready"
    | "test_result"
    | "pr_created"
    | "run_complete"
    | "run_failed"
    | "ping"
    | "error";
  run_id?: string;
  agent?: string;
  node?: string;
  level?: string;
  message?: string;
  state?: AgentState;
  diff?: string;
  result?: TestResult;
  test_result?: TestResult;
  pr_url?: string;
  pr_number?: number;
  error?: string;
  timestamp?: string;
}

export type AgentEvent = WebSocketEvent;

export interface HealthResponse {
  status: string;
  docker_available: boolean;
  timestamp?: string;
}

export interface CreateRunResponse {
  run_id: string;
  status: string;
  websocket_url: string;
}

/** WebSocket event narrowed to an agent log line. */
export type AgentLogEvent = WebSocketEvent & {
  type: "agent_log";
  agent: AgentName | string;
  level: LogLevel | string;
  message: string;
  timestamp?: string;
};

/** WebSocket event carrying a full state snapshot. */
export type StateUpdateEvent = WebSocketEvent & {
  type: "state_update";
  state: AgentState;
};

export function isAgentLog(event: WebSocketEvent): event is AgentLogEvent {
  return event.type === "agent_log";
}

export function isStateUpdate(event: WebSocketEvent): event is StateUpdateEvent {
  return event.type === "state_update" && event.state !== undefined;
}
