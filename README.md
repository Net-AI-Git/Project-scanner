# Scan API

An API service that accepts a public GitHub repository URL and runs a **security vulnerability scan**, returning a Markdown report path. Built with FastAPI; uses GitHub API for repo contents and Nebius Token Factory (LLM) for the scan workflow (planner, workers, synthesizer).

---

## Requirements

- **Python 3.12** (the project was developed and tested with 3.12)
- **UV** for dependency and run management ([install UV](https://docs.astral.sh/uv/getting-started/installation/) — e.g. `pip install uv` or use the official installer)
- A Nebius Token Factory API key (see below).

---

## Setup and run (step-by-step)

1. **Clone or unpack the project** so that the `summary_api` folder and the project root (with `pyproject.toml`) are available.  
   If Python is not installed, install **Python 3.12** from [python.org](https://www.python.org/downloads/) and tick "Add Python to PATH".

2. **Install UV** (if not already installed):
   ```bash
   pip install uv
   ```

3. **Install dependencies** from the project root (UV creates and uses a virtual environment automatically):
   ```bash
   uv sync
   ```
   This reads `pyproject.toml` and `uv.lock` and installs all dependencies. To regenerate the lock file after changing dependencies, run `uv lock`.

4. **Configure environment variables.**  
   The LLM is configured via the **`NEBIUS_API_KEY`** environment variable (we do not hardcode API keys). Get a key at [Nebius Token Factory](https://tokenfactory.nebius.com/) (API keys: [project/api-keys](https://tokenfactory.nebius.com/project/api-keys)).

   **Option A — PowerShell (Windows):** set the key in the current session before running the server. Use quotes around the key:
   ```powershell
   $env:NEBIUS_API_KEY = "your_key_here"
   ```
   This applies only to this PowerShell window. Run it again in each new terminal, or add it to your [PowerShell profile](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_profiles) if you want it in every session.

   **Option B — .env file (any OS):** copy the example file, then edit `.env` and set `NEBIUS_API_KEY=your_key_here`.
   - **Windows (PowerShell):** `Copy-Item .env.example .env`
   - **Windows (CMD):** `copy .env.example .env`
   - **macOS/Linux:** `cp .env.example .env`  
   The app reads `.env` automatically when the server starts. Do not commit `.env` or put API keys in code.

   **GitHub token (recommended):** Without it, the GitHub API allows about **60 requests/hour**; you may get **503 "GitHub API rate limit or access denied"**. With `GITHUB_TOKEN` you get 5000 requests/hour. Create a token at [GitHub → Personal access tokens](https://github.com/settings/tokens) (no scopes needed for public repos).

   **Option A — PowerShell (Windows):** set the token in the current session:
   ```powershell
   $env:GITHUB_TOKEN = "ghp_your_token_here"
   ```

   **Option B — .env file (any OS):** in the same `.env` file where you set `NEBIUS_API_KEY`, add:
   ```
   GITHUB_TOKEN=ghp_your_token_here
   ```
   Restart the server after changing `.env`.

5. **Run the server** — from the **project root** (see [Run the server](#run-the-server) for the command). After it starts, the `POST /scan` endpoint is available at `http://localhost:8000/scan`, and health probes at `GET /health/live` and `GET /health/ready`.

---

## Run the server

Run the API with **Uvicorn** only. Do **not** run the server with `python main.py` or `python -m summary_api.main`; use the uvicorn commands below. From the **project root**:

**Development** (with auto-reload; do not use `--workers` in development):

```bash
uv run uvicorn summary_api.main:app --reload --host 127.0.0.1 --port 8000
```

**Production** (use multiple workers; do not use `--reload` in production):

```bash
uv run uvicorn summary_api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Adjust the `--workers` value (e.g. 4) per environment and load. After the server starts:

- **POST /scan** — main endpoint for security scan (returns `report_path` to the saved Markdown report).
- **GET /health/live** — liveness probe (e.g. for Kubernetes).
- **GET /health/ready** — readiness probe (cached ~5s).

---

## Test the endpoint

```bash
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/psf/requests"}'
```

You should get HTTP 200 and a JSON body with `report_path` (path to the generated Markdown report).

**PowerShell (alternative):**

```powershell
(Invoke-WebRequest -Method Post -Uri "http://localhost:8000/scan" -ContentType "application/json" -Body '{"github_url": "https://github.com/psf/requests"}' -UseBasicParsing).Content
```

---

## Scan workflow

The scan uses a LangGraph workflow: **fetch** (GitHub) → **process** (filter/prioritize context) → **planner** → **orchestrator** → **workers** (per-file vulnerability scan via LLM) → **md_writer** → **synthesizer**. The LLM (Nebius Token Factory, Llama-3.3-70B-Instruct) is used for planning and for analyzing file content for security findings.

---

## Error responses

On error, the API returns an appropriate HTTP status (e.g. 400, 404, 502) and a body in the form:

```json
{"status": "error", "message": "Description of what went wrong"}
```

Examples: invalid or non-GitHub URL (400), repo not found or private (404), LLM or network failure (502).

---

## Infrastructure and observability

- **Logging:** Logs are emitted as JSON to stdout when `LOG_FORMAT=json`; audit and DLQ write to `AUDIT.jsonl` and `DLQ.jsonl` for local or external collection.
- **Tenancy:** The service is **single-tenant**. For multi-tenant deployments, apply `tenant_id` and isolation per the multi-tenancy rule.

### LangSmith observability (optional)

The app can send **traces and metrics** for LangGraph/LLM runs to [LangSmith](https://smith.langchain.com/) when configured. Audit logs remain in `AUDIT.jsonl`; LangSmith is used only for traces and agent/LLM observability.

**Environment variables** (copy into `.env` or set in the environment):

| Variable | Description |
|----------|-------------|
| `LANGCHAIN_TRACING_V2` | Set to `true` to enable tracing (default: `false`). |
| `LANGCHAIN_API_KEY` | Your LangSmith API key (get it at [smith.langchain.com](https://smith.langchain.com/) or [eu.smith.langchain.com](https://eu.smith.langchain.com) for EU). Not logged. |
| `LANGCHAIN_PROJECT` | Project name in LangSmith (default: `summary-api`). |
| `LANGSMITH_ENDPOINT` | **Required for EU region.** Set to `https://eu.api.smith.langchain.com` if your project is on [eu.smith.langchain.com](https://eu.smith.langchain.com). Omit for US. |

**Where to see data:** In the [LangSmith dashboard](https://smith.langchain.com/) (or [EU](https://eu.smith.langchain.com/)), open your project to view runs, latency, errors, and token usage. Each request is associated with a `correlation_id` in run metadata so you can correlate traces with logs and audit.

**Alerts:** In LangSmith you can configure alerts (e.g. errored runs, latency thresholds) and notifications (e.g. webhooks, PagerDuty). See [LangSmith Alerts](https://docs.langchain.com/langsmith/alerts).

---

## Project layout

```
summary_api/
├── api/main.py      # FastAPI app, POST /scan, error handling
├── core/config.py   # Settings from env (no keys in code)
├── models/schemas.py# Pydantic: ScanRequest, ErrorResponse, Finding, etc.
├── workflows/       # LangGraph: graph, nodes (fetch, process), scan_nodes
├── clients/         # GitHub fetcher; llm_client (LLMClientError only)
├── services/        # repo_processor, planner, vulnerability_scanner, report_synthesizer
└── core/audit.py    # Audit logging for requests and errors
```

Dependencies are managed with **UV**: see `pyproject.toml` and `uv.lock` in the project root. Use `uv sync` to install (see setup steps above).
