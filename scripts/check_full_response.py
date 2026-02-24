"""Call POST /summarize and print the full response (no truncation). Run from repo root with server up."""
import sys
import json
import urllib.request
import urllib.error
from pathlib import Path

# Default repo: same as request-body.json in project root (single source of truth)
def _default_github_url() -> str:
    path = Path(__file__).resolve().parent.parent / "request-body.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("github_url"), str) and data["github_url"].strip():
                return data["github_url"].strip()
    except Exception:
        pass
    return "https://github.com/Net-AI-Git/Project-scanner"

def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    base_url = args[0].rstrip("/") if args else "http://127.0.0.1:8000"
    github_url = args[1] if len(args) > 1 else _default_github_url()
    url = base_url if base_url.endswith("/summarize") else f"{base_url.rstrip('/')}/summarize"
    print("POST", url, "(github_url=%s)" % github_url)
    body = json.dumps({"github_url": github_url}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()
        except Exception:
            pass
        print("Request failed: HTTP", e.code, e.reason)
        print("Response body:", err_body[:500] if err_body else "(none)")
        if e.code == 404 and err_body and "Repository not found or private" in err_body:
            print("\nTip: The repo is not found or private. Set a public repo in request-body.json or pass it as second arg.")
        elif e.code == 404:
            print("\nTip: If your server uses a prefix, pass the full base URL as first argument.")
        sys.exit(1)
    except Exception as e:
        print("Request failed:", e)
        sys.exit(1)

    # Pretty display (default); use --json for raw JSON
    if "--json" in sys.argv:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    summary = data.get("summary") or ""
    technologies = data.get("technologies") or []
    structure = data.get("structure") or ""

    width = 72
    line = "-" * width
    print()
    print("  SUMMARY")
    print(line)
    if summary:
        for paragraph in summary.split("\n"):
            print("  " + paragraph)
    else:
        print("  (none)")
    print()
    print("  TECHNOLOGIES")
    print(line)
    if technologies:
        for t in technologies:
            print("  •", t)
    else:
        print("  (none)")
    print()
    print("  STRUCTURE")
    print(line)
    if structure:
        for paragraph in structure.split("\n"):
            print("  " + paragraph)
    else:
        print("  (none)")
    print(line)
    print("  (summary: %d chars | structure: %d chars)" % (len(summary), len(structure)))


if __name__ == "__main__":
    main()
