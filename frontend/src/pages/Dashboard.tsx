import { useState } from "react";
import { IssueInputForm } from "../components/IssueInputForm";
import { AgentTerminal } from "../components/AgentTerminal";
import { AgentStatusBar } from "../components/AgentStatusBar";
import { DiffViewer } from "../components/DiffViewer";
import { TestResultPanel } from "../components/TestResultPanel";
import { PRResultCard } from "../components/PRResultCard";
import { useAgentStream } from "../hooks/useAgentStream";

export function Dashboard() {
  const [runId, setRunId] = useState<string | null>(null);
  const {
    logs,
    agentStatuses,
    currentState,
    diff,
    testResult,
    prUrl,
    runStatus,
    isConnected,
  } = useAgentStream(runId);

  const displayDiff =
    diff ??
    (currentState?.file_changes?.length
      ? currentState.file_changes.map((c) => c.diff).join("\n")
      : null);

  const displayTestResult = testResult ?? currentState?.test_result ?? null;
  const displayPrUrl = prUrl ?? currentState?.pr_url ?? null;

  function handleRunCreated(id: string) {
    setRunId(id);
  }

  const isSubmitting =
    runStatus === "connecting" || runStatus === "running";

  return (
    <div className="flex h-full min-h-[calc(100vh-4.5rem)] flex-col lg:flex-row">
      <aside className="w-full border-b border-[#30363d] bg-[#0d1117] p-4 lg:w-[35%] lg:border-b-0 lg:border-r">
        <IssueInputForm
          onRunCreated={handleRunCreated}
          disabled={isSubmitting}
        />
        <div className="mt-6">
          <AgentStatusBar agentStatuses={agentStatuses} />
        </div>
        {currentState && (
          <div className="mt-4 rounded-md border border-[#30363d] bg-[#161b22] p-3">
            <p className="text-xs font-medium uppercase tracking-wide text-[#8b949e]">
              Run
            </p>
            <p className="mt-1 truncate font-mono text-xs text-[#e6edf3]">
              {currentState.run_id}
            </p>
            <p className="mt-1 text-xs text-[#8b949e]">
              {currentState.repo_owner}/{currentState.repo_name} #
              {currentState.issue_number}
            </p>
          </div>
        )}
      </aside>

      <section className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:w-[65%]">
        <AgentTerminal logs={logs} isConnected={isConnected} />

        {displayDiff && <DiffViewer diff={displayDiff} />}

        {displayTestResult && (
          <TestResultPanel testResult={displayTestResult} />
        )}

        {displayPrUrl && <PRResultCard prUrl={displayPrUrl} />}

        {runStatus === "failed" && currentState?.error_message && (
          <div className="rounded-md border border-[#f85149]/40 bg-[#f85149]/10 px-4 py-3 text-sm text-[#ff7b72]">
            {currentState.error_message}
          </div>
        )}
      </section>
    </div>
  );
}
