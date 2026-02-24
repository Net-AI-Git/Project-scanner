# Summary API — Nebius Academy Task

A small API service that accepts a public GitHub repository URL and returns a human-readable summary: what the project does, which technologies it uses, and how it is structured. Built with FastAPI; uses GitHub API for repo contents and Nebius Token Factory for the LLM summary.

---

## Requirements

- **Python 3.12** (the project was developed and tested with 3.12)
- A Nebius Token Factory API key (see below).

---

## Setup (step-by-step)

1. **Clone or unpack the project** so that the `summary_api` folder and the project root (with `requirements.txt`) are available.  
   If Python is not installed, install **Python 3.12** from [python.org](https://www.python.org/downloads/) and tick "Add Python to PATH". On Windows, `py -3.12 -m venv .venv` creates a venv with 3.12 if you have multiple versions.

2. **Create a virtual environment** (recommended):
   - **Windows** (use the Python Launcher; `-3.12` picks Python 3.12 if you have several versions):
     ```powershell
     py -3.12 -m venv .venv
     ```
   - **macOS/Linux** (use `python3.12` if you have multiple versions):
     ```bash
     python3.12 -m venv .venv
     ```

3. **Activate the virtual environment** (required before installing packages and running the server):
   - **Windows** (PowerShell or CMD):
     ```powershell
     .venv\Scripts\activate
     ```
   - **macOS/Linux**:
     ```bash
     source .venv/bin/activate
     ```
   After activation, the prompt will show `(.venv)` at the end of the line.

4. **Install dependencies** from the project root:
   ```bash
   pip install -r requirements.txt
   ```
   On Windows, if `pip` is not found: `py -m pip install -r requirements.txt`.

5. **Configure environment variables.**  
   Get a key at [Nebius Token Factory](https://tokenfactory.nebius.com/) (API keys: [project/api-keys](https://tokenfactory.nebius.com/project/api-keys)).

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

---

## Run the server

Start the server on **port 8000**. From the **project root**:

```bash
cd summary_api
uvicorn main:app --host 0.0.0.0 --port 8000
```

If you are already inside `summary_api`, run only: `uvicorn main:app --host 0.0.0.0 --port 8000`

The `POST /summarize` endpoint will be available at `http://localhost:8000/summarize`.

---

## Test the endpoint

**Recommended — readable output (full summary, no truncation):**

From the project root, with the server running:

```bash
python scripts/check_full_response.py
```

This uses the repo in `request-body.json` by default. To use another repo:

```bash
python scripts/check_full_response.py "http://127.0.0.1:8000" "https://github.com/psf/requests"
```

You get a clear SUMMARY, TECHNOLOGIES (bullets), and STRUCTURE. Use `--json` at the end for raw JSON instead.

---

**One-liner (output may be truncated in the console):**

**PowerShell:**

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/summarize" -ContentType "application/json" -Body '{"github_url": "https://github.com/psf/requests"}'
```

To see the full response in PowerShell, use:

```powershell
(Invoke-WebRequest -Method Post -Uri "http://localhost:8000/summarize" -ContentType "application/json" -Body '{"github_url": "https://github.com/psf/requests"}').Content
```

**Linux/macOS/Git Bash (curl):**

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/psf/requests"}'
```

You should get HTTP 200 and a JSON body with `summary`, `technologies`, and `structure`.

---

## Model choice

- **Nebius Token Factory** with **Llama-3.3-70B-Instruct**. Chosen for good quality and structured output for repo summarization; API is OpenAI-compatible and keys are available from Nebius.

The prompt asks the LLM for a single JSON object with `summary`, `technologies`, and `structure`; the response is parsed (with a fallback for plain text) so the API always returns the specified format.

---

## Repository processing (what we include and skip)

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

Dependencies are in the project root `requirements.txt` (e.g. `fastapi`, `uvicorn`, `pydantic-settings`, `httpx`). Use that file for `pip install -r requirements.txt` as in the setup steps above.
