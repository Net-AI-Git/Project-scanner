"""Agent scratchpad: internal log excluded from LLM context.

Implements: .cursor/rules/agents/agentic-logic-and-tools (Scratchpad).
Maintain an internal scratchpad for each agent, saved to a long LOG file,
but excluded from the context window passed to the model.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

# Default scratchpad log under project root; override via SCRATCHPAD_LOG_PATH if needed.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SCRATCHPAD_PATH = _PROJECT_ROOT / "SCRATCHPAD.log"

_logger = logging.getLogger(__name__)


def append_scratchpad(
    message: str,
    *,
    correlation_id: str | None = None,
    step: str | None = None,
    scratchpad_path: str | Path | None = None,
) -> None:
    """Append a line to the agent scratchpad log. Not sent to the LLM.

    Use for intermediate reasoning, tool results, or state that must be
    available for debugging but must not be included in the context window.

    Args:
        message: Text to append (one line; newlines are replaced with spaces).
    Keyword Args:
        correlation_id: Request/correlation ID for trace lookup.
        step: Optional step name (e.g. fetch_node, summarize_node).
        scratchpad_path: Override log file path (default: project root / SCRATCHPAD.log).
    """
    path = Path(scratchpad_path) if scratchpad_path else _DEFAULT_SCRATCHPAD_PATH
    ts = datetime.now(timezone.utc).isoformat()
    line = message.replace("\n", " ").strip()
    prefix = f"{ts}"
    if correlation_id:
        prefix += f" [{correlation_id}]"
    if step:
        prefix += f" [{step}]"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{prefix} {line}\n")
    except OSError as e:
        _logger.warning("Scratchpad append failed: %s", e)
