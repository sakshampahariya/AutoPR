import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { TestResult } from "../types/agent";

type Props = {
  testResult: TestResult;
};

function extractFailedTests(stdout: string): string[] {
  const lines = stdout.split("\n");
  const failed: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (
      trimmed.startsWith("FAILED") ||
      trimmed.includes(" FAILED ") ||
      /^FAILED\s+/.test(trimmed)
    ) {
      failed.push(trimmed);
    }
  }
  return failed.slice(0, 15);
}

export function TestResultPanel({ testResult }: Props) {
  const [stdoutOpen, setStdoutOpen] = useState(testResult.status !== "pass");
  const failedTests = useMemo(
    () => extractFailedTests(testResult.stdout),
    [testResult.stdout]
  );

  const passed = testResult.status === "pass";

  return (
    <div className="rounded-lg border border-[#30363d] bg-[#161b22] p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[#e6edf3]">Test Results</h2>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-bold uppercase ${
            passed
              ? "bg-[#0f2d17] text-[#3fb950]"
              : "bg-[#2d0f0f] text-[#f85149]"
          }`}
        >
          {passed ? "PASS" : "FAIL"}
        </span>
      </div>

      <p className="mb-3 text-sm text-[#8b949e]">
        {testResult.passed} passed | {testResult.failed} failed |{" "}
        {testResult.errors} errors | {testResult.duration_seconds}s duration
      </p>

      {failedTests.length > 0 && (
        <div className="mb-3 rounded-md border border-[#f85149]/30 bg-[#f85149]/5 p-3">
          <p className="mb-2 text-xs font-semibold uppercase text-[#f85149]">
            Failed tests
          </p>
          <ul className="space-y-1 font-mono text-xs text-[#ff7b72]">
            {failedTests.map((name) => (
              <li key={name} className="break-all">
                {name}
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        type="button"
        onClick={() => setStdoutOpen((o) => !o)}
        className="flex w-full items-center gap-2 text-sm text-[#8b949e] hover:text-[#e6edf3]"
      >
        {stdoutOpen ? (
          <ChevronDown className="h-4 w-4" />
        ) : (
          <ChevronRight className="h-4 w-4" />
        )}
        stdout / stderr
      </button>
      {stdoutOpen && (
        <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-[#30363d] bg-[#0d1117] p-3 font-mono text-xs text-[#e6edf3] whitespace-pre-wrap">
          {testResult.stdout}
          {testResult.stderr ? `\n\n--- stderr ---\n${testResult.stderr}` : ""}
        </pre>
      )}
    </div>
  );
}
