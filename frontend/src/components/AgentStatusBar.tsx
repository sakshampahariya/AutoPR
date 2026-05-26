import type { AgentStatus } from "../types/agent";

const AGENT_META: Record<
  string,
  { label: string; icon: string }
> = {
  research: { label: "Research", icon: "🔍" },
  coding: { label: "Coding", icon: "📝" },
  testing: { label: "Testing", icon: "🧪" },
  pr: { label: "PR", icon: "🔀" },
};

const STATUS_STYLES: Record<
  AgentStatus,
  string
> = {
  idle: "bg-[#21262d] text-[#8b949e] border-[#30363d]",
  running: "bg-[#3d2e00] text-[#d29922] border-[#9e6a03] animate-pulse",
  complete: "bg-[#0f2d17] text-[#3fb950] border-[#238636]",
  failed: "bg-[#2d0f0f] text-[#f85149] border-[#da3633]",
};

type Props = {
  agentStatuses: Record<string, string>;
};

export function AgentStatusBar({ agentStatuses }: Props) {
  const agents = ["research", "coding", "testing", "pr"];

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-[#8b949e]">
        Agents
      </p>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {agents.map((key) => {
          const meta = AGENT_META[key];
          const status = (agentStatuses[key] ?? "idle") as AgentStatus;
          return (
            <div
              key={key}
              className={`flex flex-col items-center rounded-lg border px-2 py-2 text-center ${STATUS_STYLES[status]}`}
            >
              <span className="text-lg" aria-hidden>
                {meta.icon}
              </span>
              <span className="text-xs font-medium">{meta.label}</span>
              <span className="mt-0.5 text-[10px] uppercase tracking-wider opacity-80">
                {status}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
