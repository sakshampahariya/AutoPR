import { Dashboard } from "./pages/Dashboard";

export default function App() {
  return (
    <div className="flex min-h-screen flex-col bg-[#0d1117] text-[#e6edf3]">
      <header className="border-b border-[#30363d] bg-[#161b22] px-6 py-4">
        <h1 className="font-mono text-lg font-semibold text-[#3fb950]">
          🤖 Multi-Agent Orchestration System
        </h1>
      </header>
      <main className="min-h-0 flex-1">
        <Dashboard />
      </main>
    </div>
  );
}
