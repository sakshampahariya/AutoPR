import { FormEvent, useState } from "react";
import { Loader2 } from "lucide-react";
import { createRun } from "../lib/api";

const ISSUE_URL_PATTERN =
  /^https:\/\/github\.com\/[a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+\/issues\/\d+$/;
const TOKEN_PATTERN = /^[a-zA-Z0-9]{40,}$/;

const MODELS = [
  { value: "deepseek-ai/deepseek-v4-pro", label: "DeepSeek v4 Pro" },
  { value: "claude-sonnet-4-20250514", label: "Claude Sonnet 4" },
  { value: "claude-opus-4-20250514", label: "Claude Opus 4" },
  { value: "gpt-4o", label: "GPT-4o" },
  { value: "gemini-2.5-pro", label: "Gemini 2.5 Pro" },
  { value: "gemini-1.5-pro", label: "Gemini 1.5 Pro" },
  { value: "gemini-1.5-flash", label: "Gemini 1.5 Flash" },
] as const;

type Props = {
  onRunCreated: (runId: string) => void;
  disabled?: boolean;
};

export function IssueInputForm({ onRunCreated, disabled }: Props) {
  const [issueUrl, setIssueUrl] = useState("");
  const [githubToken, setGithubToken] = useState("");
  const [modelName, setModelName] = useState("deepseek-ai/deepseek-v4-pro");
  const [urlError, setUrlError] = useState<string | null>(null);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  function validateUrl(value: string) {
    if (!value.trim()) {
      setUrlError(null);
      return;
    }
    if (!ISSUE_URL_PATTERN.test(value.trim())) {
      setUrlError("Use format: https://github.com/owner/repo/issues/123");
    } else {
      setUrlError(null);
    }
  }

  function validateToken(value: string) {
    if (!value.trim()) {
      setTokenError(null);
      return;
    }
    if (!TOKEN_PATTERN.test(value.trim())) {
      setTokenError("Token must be 40+ alphanumeric characters.");
      return;
    }
    setTokenError(null);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitError(null);
    if (!ISSUE_URL_PATTERN.test(issueUrl.trim())) {
      setUrlError("Use format: https://github.com/owner/repo/issues/123");
      return;
    }
    setUrlError(null);

    if (githubToken.trim() && !TOKEN_PATTERN.test(githubToken.trim())) {
      setTokenError("Token must be 40+ alphanumeric characters.");
      return;
    }
    setTokenError(null);

    setLoading(true);
    try {
      const res = await createRun(issueUrl.trim(), githubToken, modelName);
      onRunCreated(res.run_id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Failed to start run");
    } finally {
      setLoading(false);
    }
  }

  const inputClass =
    "w-full rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2 text-sm text-[#e6edf3] placeholder-[#6e7681] focus:border-[#3fb950] focus:outline-none focus:ring-1 focus:ring-[#3fb950] disabled:opacity-50";

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-[#30363d] bg-[#161b22] p-4 shadow-sm"
    >
      <h2 className="mb-4 font-mono text-base font-semibold text-[#3fb950]">
        Start New Run
      </h2>

      <div className="flex flex-col gap-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-sm text-[#8b949e]">GitHub Issue URL</span>
          <input
            className={inputClass}
            value={issueUrl}
            onChange={(e) => setIssueUrl(e.target.value)}
            onBlur={() => validateUrl(issueUrl)}
            placeholder="https://github.com/owner/repo/issues/123"
            required
            disabled={disabled || loading}
          />
          {urlError && (
            <span className="text-xs text-[#f85149]">{urlError}</span>
          )}
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm text-[#8b949e]">GitHub Token (PAT)</span>
          <input
            type="password"
            className={inputClass}
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
            onBlur={() => validateToken(githubToken)}
            autoComplete="off"
            disabled={disabled || loading}
          />
          {tokenError && (
            <span className="text-xs text-[#f85149]">{tokenError}</span>
          )}
          <span className="text-xs text-[#6e7681]">
            Optional if the backend has GITHUB_TOKEN configured. Otherwise requires
            repo:read and pull_requests:write scopes.
          </span>
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-sm text-[#8b949e]">Model</span>
          <select
            className={inputClass}
            value={modelName}
            onChange={(e) => setModelName(e.target.value)}
            disabled={disabled || loading}
          >
            {MODELS.map((m) => (
              <option key={m.value} value={m.value}>
                {m.label}
              </option>
            ))}
          </select>
        </label>

        {submitError && (
          <p className="rounded-md border border-[#f85149]/30 bg-[#f85149]/10 px-3 py-2 text-sm text-[#ff7b72]">
            {submitError}
          </p>
        )}

        <button
          type="submit"
          className="flex w-full items-center justify-center gap-2 rounded-md bg-[#238636] px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-[#2ea043] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={disabled || loading}
        >
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          Run Agent
        </button>
      </div>
    </form>
  );
}
