# Summary API — Nebius Academy Task

A small API service that accepts a public GitHub repository URL and returns a human-readable summary: what the project does, which technologies it uses, and how it is structured. Built with FastAPI; uses GitHub API for repo contents and Nebius Token Factory for the LLM summary.

This README provides the **step-by-step setup and run instructions**, **model choice and rationale**, and **repository processing approach** required for submission (AI Performance Engineering 2026).

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

5. **Run the server** — from the **project root** (see [Run the server](#run-the-server) for the command). After it starts, the `POST /summarize` endpoint is available at `http://localhost:8000/summarize`, and health probes at `GET /health/live` and `GET /health/ready`.

---

## Run the server

Run the API with **Uvicorn** only (do not start the server with `python main.py`). From the **project root**:

**Development** (with auto-reload; do not use `--workers` in development):

```bash
uv run uvicorn summary_api.main:app --reload --host 127.0.0.1 --port 8000
```

**Production** (use multiple workers; do not use `--reload` in production):

```bash
uv run uvicorn summary_api.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Adjust the `--workers` value (e.g. 4) per environment and load. After the server starts:

- **POST /summarize** — main endpoint for repo summarization.
- **GET /health/live** — liveness probe (e.g. for Kubernetes).
- **GET /health/ready** — readiness probe (cached ~5s).

---

## Test the endpoint

As in the task specification, you can test with:

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/psf/requests"}'
```

You should get HTTP 200 and a JSON body with `summary`, `technologies`, and `structure`.

**PowerShell (alternative):**

```powershell
(Invoke-WebRequest -Method Post -Uri "http://localhost:8000/summarize" -ContentType "application/json" -Body '{"github_url": "https://github.com/psf/requests"}' -UseBasicParsing).Content
```

---

## Model choice

**Nebius Token Factory** with **Llama-3.3-70B-Instruct** — chosen for good quality and structured output for repo summarization; the API is OpenAI-compatible and keys are available from Nebius. The prompt asks the LLM for a single JSON object with `summary`, `technologies`, and `structure`; the response is parsed (with a fallback for plain text) so the API always returns the specified format.

---

## Repository processing (what we include, what we skip, and why)

The service does **not** send the whole repo to the LLM. It filters and prioritizes content and enforces a **context size limit** (~60k characters by default) so we stay within typical context windows.

### What we skip (and why)

- **Directories:** `node_modules`, `__pycache__`, `.git`, `venv`, `.venv`, `dist`, `build`, `.eggs`, `.tox`, cache dirs (e.g. `.pytest_cache`, `.mypy_cache`), `vendor`, `pods`, IDE folders (`.idea`, `.vscode`), coverage output, etc.  
  **Why:** Generated or tooling artifacts; not useful for "what does this project do."
- **Files:** Lock files (`package-lock.json`, `yarn.lock`, `poetry.lock`, `Cargo.lock`, etc.), minified files (`.min.js`, `.min.css`), source maps (`.map`).  
  **Why:** Lock files are large and low signal; minified/map files are not human-readable.

Binary detection is not exhaustive; the focus is on skipping the above and on size/priority limits.

### What we include (and in what order)

1. **Repository structure** — A compact directory tree (paths only) so the LLM sees layout.
2. **High priority:** README-style files (e.g. `README`, `CONTRIBUTING`, `CHANGELOG`), `LICENSE`, and key config files (`package.json`, `pyproject.toml`, `requirements.txt`, `setup.py`, `Cargo.toml`, `go.mod`, `Dockerfile`, `Makefile`, `tsconfig.json`, etc.).
3. **Other files** — Sorted by depth and type; root and shallow files come before deep ones.

Single files are truncated if they are very large; total context is capped so the request does not exceed the limit. If the repo is huge, lower-priority content is dropped and a short "omitted due to context limit" note is added.

This keeps the prompt small enough for the model while giving it README, structure, and config so it can summarize purpose, technologies, and layout.

---

## Error responses

On error, the API returns an appropriate HTTP status (e.g. 400, 404, 502) and a body in the form:

```json
{"status": "error", "message": "Description of what went wrong"}
```

Examples: invalid or non-GitHub URL (400), repo not found or private (404), LLM or network failure (502).

---

## Project layout

```
summary_api/
├── main.py          # FastAPI app, POST /summarize, error handling
├── config.py        # Settings from env (no keys in code)
├── schemas.py       # Pydantic: request/response/error
├── github_client.py # Fetch repo file list and contents from GitHub API
├── repo_processor.py# Filter, prioritize, and build context for the LLM
├── llm_client.py    # Call Nebius Token Factory, parse summary JSON
└── audit.py         # Audit logging for requests and errors
```

Dependencies are managed with **UV**: see `pyproject.toml` and `uv.lock` in the project root. Use `uv sync` to install (see setup steps above).
