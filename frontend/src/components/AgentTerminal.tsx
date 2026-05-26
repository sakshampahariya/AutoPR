import { useEffect, useRef } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import type { AgentLog } from "../types/agent";

const AGENT_COLORS: Record<string, string> = {
  research: "\x1b[36m",
  coding: "\x1b[33m",
  testing: "\x1b[35m",
  pr: "\x1b[32m",
  system: "\x1b[90m",
};

type Props = {
  logs: AgentLog[];
  isConnected: boolean;
};

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

export function AgentTerminal({ logs, isConnected }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const lastIndexRef = useRef(0);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const term = new Terminal({
      theme: {
        background: "#0d1117",
        foreground: "#e6edf3",
        cursor: "#58a6ff",
      },
      fontSize: 13,
      fontFamily: '"JetBrains Mono", Consolas, "Courier New", monospace',
      convertEol: true,
      scrollback: 5000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    term.writeln("\x1b[90m[system]\x1b[0m Agent terminal ready.");
    fit.fit();

    termRef.current = term;
    fitRef.current = fit;
    lastIndexRef.current = 0;

    const ro = new ResizeObserver(() => fit.fit());
    ro.observe(el);

    return () => {
      ro.disconnect();
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      lastIndexRef.current = 0;
    };
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (!term) return;

    for (let i = lastIndexRef.current; i < logs.length; i++) {
      const log = logs[i];
      const ts = formatTimestamp(log.timestamp);
      const agent = String(log.agent_name);
      const color =
        log.level === "error"
          ? "\x1b[31m"
          : AGENT_COLORS[agent] ?? "\x1b[37m";
      const agentLabel = agent.toUpperCase();
      term.writeln(
        `\r\n\x1b[90m[${ts}]\x1b[0m ${color}[${agentLabel}]\x1b[0m ${log.message}`
      );
    }
    lastIndexRef.current = logs.length;
    term.scrollToBottom();
  }, [logs]);

  return (
    <div className="relative min-h-[280px] flex-1 rounded-lg border border-[#30363d] bg-[#0d1117]">
      <div
        className={`absolute right-3 top-3 z-10 flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs ${
          isConnected
            ? "border-[#238636] bg-[#0f2d17] text-[#3fb950]"
            : "border-[#da3633] bg-[#2d0f0f] text-[#f85149]"
        }`}
      >
        <span
          className={`h-2 w-2 rounded-full ${
            isConnected ? "bg-[#3fb950]" : "bg-[#f85149]"
          }`}
        />
        {isConnected ? "Connected" : "Disconnected"}
      </div>
      <div ref={containerRef} className="h-[280px] w-full p-1 pt-8" />
    </div>
  );
}
