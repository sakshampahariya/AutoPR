# 🤖 AutoPR — Autonomous Pull Request Generator

An autonomous AI platform that takes a **GitHub issue URL** and delivers a **tested pull request** — no human intervention needed.

Specialized agents research the codebase, generate a fix, validate it inside an isolated Docker sandbox, and open a PR. Every step streams live to a React dashboard via WebSockets.

---

## How It Works

```
GitHub Issue URL
       │
       ▼
  [Research Agent]  →  Reads issue + repo, identifies root cause
       │
       ▼
  [Coding Agent]    →  Generates minimal code patch
       │
       ▼
  [Testing Agent]   →  Runs pytest inside Docker sandbox
       │
       ├── PASS ──→  [PR Agent]  →  Opens Pull Request ✅
       │
       └── FAIL ──→  [Coding Agent]  →  Retries with error logs
                     (up to 3 attempts, then fails gracefully)
```

The system uses a **LangGraph stateful graph** with a self-correction loop — if tests fail, the Coding Agent gets the failure output and tries again, just like a real engineer would.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Orchestration | LangGraph (stateful cyclic graph) |
| Backend | FastAPI + WebSockets |
| AI Models | Claude (Anthropic) / GPT-4 (OpenAI) |
| Sandbox | Docker (network-isolated, read-only) |
| GitHub Integration | PyGithub |
| Frontend | React 18 + Vite + TailwindCSS |
| Terminal UI | xterm.js |

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- Docker Desktop (must be running)
- GitHub PAT with `repo:read` and `pull_requests:write` scopes

---

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/guptadhruv780/Multi-Agent-Orchestration-System
cd Multi-Agent-Orchestration-System
```

**2. Configure environment**
```bash
cd backend
cp .env.example .env
# Edit .env and fill in your API keys
```

Required variables in `.env`:
```
ANTHROPIC_API_KEY=your_key_here
GITHUB_TOKEN=your_pat_here
DEFAULT_MODEL=claude-sonnet-4-20250514
```

**3. Install backend dependencies**
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**4. Install frontend dependencies**
```bash
cd ../frontend
npm install
```

**5. Start both servers**

Linux/macOS:
```bash
chmod +x run_dev.sh && ./run_dev.sh
```

Windows:
```powershell
.\run_dev.ps1
```

Or manually:
```bash
# Terminal 1
cd backend && uvicorn main:app --reload --port 8000

# Terminal 2
cd frontend && npm run dev
```

- **Dashboard:** http://localhost:5173
- **API Docs:** http://localhost:8000/docs

---

## Usage

1. Open the dashboard at `http://localhost:5173`
2. Paste a GitHub issue URL (e.g. `https://github.com/owner/repo/issues/42`)
3. Enter your GitHub PAT
4. Choose a model and click **Run Agent**
5. Watch live logs, diffs, test results, and the final PR link

---

## Agents

| Agent | What it does |
|---|---|
| **Research** | Reads the issue and repo, finds relevant files, builds a context brief |
| **Coding** | Generates a minimal patch; retries with test failure logs if needed |
| **Testing** | Runs `pytest` inside a hardened Docker container (no network, read-only fs) |
| **PR** | Creates a branch, commits files, opens a pull request |

---

## Docker Sandbox Security

Tests run in an isolated container with:
- `network_mode: none` — no internet access
- Read-only filesystem
- 512MB memory cap, 0.5 CPU cap
- All Linux capabilities dropped

---

## Running Tests

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```

---

## License

MIT
