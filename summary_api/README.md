# Summary API

A small API service that accepts a public GitHub repository URL and returns a human-readable summary: what the project does, which technologies it uses, and how it is structured. Built with FastAPI; uses GitHub API for repo contents and an LLM (Google AI Studio or Nebius Token Factory) for the summary.

---

## Requirements

- **Python 3.10+**
- An API key for at least one LLM provider (see below).

---

## Setup (step-by-step)

1. **Clone or unpack the project** so that the `summary_api` folder and the project root (with `requirements.txt`) are available.

2. **Create a virtual environment** (recommended):
   ```bash
   python -m venv .venv
   ```
   - On Windows: `.venv\Scripts\activate`
   - On macOS/Linux: `source .venv/bin/activate`

3. **Install dependencies** from the project root:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables.**  
   From the project root, copy the example env file and edit as needed:
   ```bash
   copy .env.example .env
   ```
   Then set at least one LLM key in `.env`:
   - **Option A — Google AI Studio (Gemini):**  
     Get a key at [Google AI Studio](https://aistudio.google.com/apikey) and set:
     ```bash
     GOOGLE_API_KEY=your_key_here
     ```
   - **Option B — Nebius Token Factory:**  
     Set (e.g. for evaluators or if you prefer Nebius):
     ```bash
     NEBIUS_API_KEY=your_key_here
     ```
   If both are set, the app uses Google first. **Do not commit `.env` or put API keys in code.**

   Optional: `GITHUB_TOKEN` for higher GitHub API rate limits (see `.env.example`).

---

## Run the server

From the **project root** (parent of `summary_api`), start the server on **port 8000**:

```bash
uvicorn summary_api.main:app --host 0.0.0.0 --port 8000
```

Or from inside `summary_api`:

```bash
cd summary_api
uvicorn main:app --host 0.0.0.0 --port 8000
```

The `POST /summarize` endpoint will be available at `http://localhost:8000/summarize`.

---

## Test the endpoint

```bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d "{\"github_url\": \"https://github.com/Net-AI-Git/Project-scanner\"}"
```

You should get HTTP 200 and a JSON body with `summary`, `technologies`, and `structure`.

---

## Model choice

- **Default:** **Google Gemini 2.0 Flash** (when `GOOGLE_API_KEY` is set). Chosen for good speed and quality for short summarization and structured output.
- **Fallback:** **Nebius Token Factory** with **Meta-Llama-3.1-70B-Instruct** (when `NEBIUS_API_KEY` is set and Google is not). Used so evaluators can run the app with Nebius as required.

The prompt asks the LLM for a single JSON object with `summary`, `technologies`, and `structure`; the response is parsed (with a fallback for plain text) so the API always returns the specified format.

---

## Repository processing (what we include and skip)

The service does **not** send the whole repo to the LLM. It filters and prioritizes content and enforces a **context size limit** (~60k characters by default) so we stay within typical context windows.

### What we skip (and why)

- **Directories:** `node_modules`, `__pycache__`, `.git`, `venv`, `.venv`, `dist`, `build`, `.eggs`, `.tox`, cache dirs (e.g. `.pytest_cache`, `.mypy_cache`), `vendor`, `pods`, IDE folders (`.idea`, `.vscode`), coverage output, etc.  
  **Why:** Generated or tooling artifacts; not useful for “what does this project do.”
- **Files:** Lock files (`package-lock.json`, `yarn.lock`, `poetry.lock`, `Cargo.lock`, etc.), minified files (`.min.js`, `.min.css`), source maps (`.map`).  
  **Why:** Lock files are large and low signal; minified/map files are not human-readable.

Binary detection is not exhaustive; the focus is on skipping the above and on size/priority limits.

### What we include (and in what order)

1. **Repository structure** — A compact directory tree (paths only) so the LLM sees layout.
2. **High priority:** README-style files (e.g. `README`, `CONTRIBUTING`, `CHANGELOG`), `LICENSE`, and key config files (`package.json`, `pyproject.toml`, `requirements.txt`, `setup.py`, `Cargo.toml`, `go.mod`, `Dockerfile`, `Makefile`, `tsconfig.json`, etc.).
3. **Other files** — Sorted by depth and type; root and shallow files come before deep ones.

Single files are truncated if they are very large; total context is capped so the request does not exceed the limit. If the repo is huge, lower-priority content is dropped and a short “omitted due to context limit” note is added.

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
├── llm_client.py    # Call Gemini or Nebius, parse summary JSON
├── requirements.txt # (or use project root requirements.txt)
└── README.md        # This file
```

Dependencies are listed in the root `requirements.txt` (e.g. `fastapi`, `uvicorn`, `pydantic-settings`, `httpx`). Use that file for `pip install -r requirements.txt` as in the setup steps above.
