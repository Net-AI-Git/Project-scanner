# Summary API — Nebius Academy Task

Full setup and run instructions: **[summary_api/README.md](summary_api/README.md)**.

**Quick start** (from this directory):

1. `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and set `NEBIUS_API_KEY` (get a key at [Nebius Token Factory](https://tokenfactory.nebius.com/)). Optional: `GITHUB_TOKEN` for higher GitHub API rate limits.
3. Run the server:
   ```bash
   uvicorn summary_api.main:app --host 0.0.0.0 --port 8000
   ```
4. Test: `POST http://localhost:8000/summarize` with body `{"github_url": "https://github.com/owner/repo"}`.

**Run tests:** from the project root: `pytest`.
