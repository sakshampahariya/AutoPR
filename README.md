# Multi-Agent Orchestration System.

An autonomous AI software engineering platform that takes a **GitHub issue URL** and produces a **pull request** with a tested fix. Specialized agents research the repository, generate patches, validate them in an isolated Docker sandbox, and open a PR—while the React dashboard streams every step over WebSockets.

The system uses a **stateful LangGraph** workflow with a self-correction loop: if tests fail, control returns to the Coding Agent with failure logs until tests pass or retry limits are hit.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     CLIENT (React + Vite)                        │
│  Issue form · Agent terminal · Diff viewer · Test / PR panels     │
└────────────────────────────┬────────────────────────────────────┘
                             │ REST POST /api/runs  +  WS /ws/{id}
┌────────────────────────────▼────────────────────────────────────┐
│                     FastAPI API Gateway                          │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│              LangGraph StateGraph (AgentState)                     │
│                                                                  │
│   research ──► coding ──► testing ──┬── pass ──► pr ──► END     │
│                      ▲              ├── retry ──► coding         │
│                      └──────────────└── fail ──► END             │
└────────────┬───────────────────────┬────────────────────────────┘
             │                       │
     ┌───────▼───────┐       ┌───────▼───────┐       ┌──────────────┐
     │  GitHub API   │       │  LLM (Claude/ │       │ Docker Engine │
     │  (PyGithub)   │       │   OpenAI)     │       │  (sandbox)    │
     └───────────────┘       └───────────────┘       └──────────────┘
```

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Node.js | 18+ |
| Docker Desktop | Running daemon (for Testing Agent) |
| GitHub PAT | `repo` read + `pull_requests` write |

## Setup

### 1. Clone and configure backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env` with your API keys (see table below).

### 2. Install frontend

```bash
cd frontend
npm install
```

### 3. Start both services

**Linux / macOS:**

```bash
chmod +x run_dev.sh
./run_dev.sh
```

**Windows (PowerShell):**

```powershell
.\run_dev.ps1
```

**Or manually:**

```bash
# Terminal 1
cd backend && python main.py

# Terminal 2
cd frontend && npm run dev
```

- Dashboard: http://localhost:5173  
- API docs: http://localhost:8000/docs  
- Health: http://localhost:8000/api/health  

## Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key (Claude models) | — |
| `OPENAI_API_KEY` | OpenAI API key (GPT models) | — |
| `GITHUB_TOKEN` | Default PAT (optional; can pass per run) | — |
| `DEFAULT_MODEL` | Default LLM id | `claude-sonnet-4-20250514` |
| `AGENT_MAX_RETRIES` | Max coding retries after test failure | `3` |
| `DOCKER_BASE_IMAGE` | Test container image | `python:3.11-slim` |
| `BACKEND_HOST` | API bind host | `0.0.0.0` |
| `BACKEND_PORT` | API port | `8000` |
| `FRONTEND_URL` | CORS origin | `http://localhost:5173` |

## Usage

1. Open the dashboard at http://localhost:5173  
2. Paste a **GitHub issue URL** (e.g. `https://github.com/owner/repo/issues/1`)  
3. Enter a **GitHub PAT** with access to that repo  
4. Choose a model and click **Run Agent**  
5. Watch live logs, diffs, test results, and the PR link when complete  

## Agent flow

| Agent | Role |
|-------|------|
| **Research** | Reads issue + repo tree, searches code, builds a context brief |
| **Coding** | Generates minimal patches (retries with test logs on failure) |
| **Testing** | Runs `pytest` in a hardened Docker container (`network_mode: none`) |
| **PR** | Creates branch, commits files, opens pull request |

## Tests

```bash
cd backend
source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v
```

## Project layout

```
backend/          FastAPI, LangGraph, agents, tools
frontend/         React dashboard
run_dev.sh        Start both servers (Unix)
run_dev.ps1       Start both servers (Windows)
```

## License

MIT (adjust as needed for your deployment).
