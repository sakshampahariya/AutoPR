import { useCallback, useEffect, useRef, useState } from "react";
import type {
  AgentLog,
  AgentName,
  AgentState,
  AgentStatus,
  LogLevel,
  StreamRunStatus,
  TestResult,
  WebSocketEvent,
} from "../types/agent";

const AGENTS = ["research", "coding", "testing", "pr"] as const;

const INITIAL_STATUSES: Record<string, AgentStatus> = {
  research: "idle",
  coding: "idle",
  testing: "idle",
  pr: "idle",
};

function wsUrlForRun(runId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/${runId}`;
}

function reconnectDelay(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 30000);
}

export function useAgentStream(runId: string | null) {
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [agentStatuses, setAgentStatuses] =
    useState<Record<string, AgentStatus>>(INITIAL_STATUSES);
  const [currentState, setCurrentState] = useState<AgentState | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<TestResult | null>(null);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState<StreamRunStatus>("idle");
  const [isConnected, setIsConnected] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const runStatusRef = useRef<StreamRunStatus>("idle");
  const reconnectAttempt = useRef(0);
  const intentionalClose = useRef(false);

  useEffect(() => {
    runStatusRef.current = runStatus;
  }, [runStatus]);

  const handleEvent = useCallback((data: WebSocketEvent) => {
    switch (data.type) {
      case "agent_log": {
        const agent = (data.agent ?? "system") as AgentName;
        const level = (data.level ?? "info") as LogLevel;
        setLogs((prev) => [
          ...prev,
          {
            agent_name: agent,
            level,
            message: data.message ?? "",
            timestamp: data.timestamp ?? new Date().toISOString(),
          },
        ]);
        if (level === "error") {
          setAgentStatuses((s) => ({ ...s, [agent]: "failed" }));
        }
        break;
      }
      case "node_start": {
        const node = data.node ?? data.agent ?? "system";
        setAgentStatuses((s) => ({ ...s, [node]: "running" }));
        setRunStatus("running");
        break;
      }
      case "node_complete": {
        const node = data.node ?? data.agent ?? "system";
        setAgentStatuses((s) => ({ ...s, [node]: "complete" }));
        break;
      }
      case "state_update":
        if (data.state) {
          setCurrentState(data.state);
          if (data.state.test_result) setTestResult(data.state.test_result);
          if (data.state.pr_url) setPrUrl(data.state.pr_url);
        }
        break;
      case "diff_ready":
        if (data.diff) setDiff(data.diff);
        break;
      case "test_result": {
        const result = data.result ?? data.test_result;
        if (result) setTestResult(result);
        break;
      }
      case "pr_created":
        if (data.pr_url) setPrUrl(data.pr_url);
        break;
      case "run_complete":
        setRunStatus("complete");
        if (data.pr_url) setPrUrl(data.pr_url);
        break;
      case "run_failed":
        setRunStatus("failed");
        setLogs((prev) => [
          ...prev,
          {
            agent_name: "system" as AgentName,
            level: "error" as LogLevel,
            message: data.message ?? data.error ?? "Run failed",
            timestamp: new Date().toISOString(),
          },
        ]);
        break;
      case "error":
        setLogs((prev) => [
          ...prev,
          {
            agent_name: "system" as AgentName,
            level: "error" as LogLevel,
            message: data.message ?? data.error ?? "WebSocket error",
            timestamp: new Date().toISOString(),
          },
        ]);
        break;
      case "ping":
        break;
      default:
        break;
    }
  }, []);

  const connect = useCallback(() => {
    if (!runId) return;

    setRunStatus((s) => (s === "idle" ? "connecting" : s));
    const ws = new WebSocket(wsUrlForRun(runId));
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      reconnectAttempt.current = 0;
      setRunStatus("running");
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const data = JSON.parse(event.data) as WebSocketEvent;
        handleEvent(data);
      } catch {
        /* ignore malformed frames */
      }
    };

    ws.onclose = (ev) => {
      setIsConnected(false);
      wsRef.current = null;

      if (intentionalClose.current) return;
      if (runStatusRef.current === "complete" || runStatusRef.current === "failed") {
        return;
      }
      if (ev.code !== 1000) {
        const delay = reconnectDelay(reconnectAttempt.current);
        reconnectAttempt.current += 1;
        window.setTimeout(connect, delay);
      }
    };

    ws.onerror = () => {
      setIsConnected(false);
    };
  }, [runId, handleEvent]);

  useEffect(() => {
    intentionalClose.current = false;

    if (!runId) {
      setLogs([]);
      setAgentStatuses(INITIAL_STATUSES);
      setCurrentState(null);
      setDiff(null);
      setTestResult(null);
      setPrUrl(null);
      setRunStatus("idle");
      setIsConnected(false);
      return;
    }

    setLogs([]);
    setAgentStatuses(INITIAL_STATUSES);
    setCurrentState(null);
    setDiff(null);
    setTestResult(null);
    setPrUrl(null);
    setRunStatus("connecting");
    connect();

    return () => {
      intentionalClose.current = true;
      wsRef.current?.close(1000);
      wsRef.current = null;
    };
  }, [runId, connect]);

  return {
    logs,
    agentStatuses,
    currentState,
    diff,
    testResult,
    prUrl,
    runStatus,
    isConnected,
  };
}

export { AGENTS };
