"""Context compression and token-based trimming for the summarize workflow.

Implements: .cursor/rules/agents/context-compression-and-optimization.
- Token threshold detection (warning 80%%, compression 85%%, critical 95%%).
- Buffer (10%%) for new content and response.
- FIFO-style trimming at section boundaries; log compression events for debugging.
"""

from __future__ import annotations

import logging
import re

_logger = logging.getLogger(__name__)

# Thresholds per rule: Warning 80%%, Compression 85%%, Critical 95%%
WARNING_RATIO = 0.80
COMPRESSION_RATIO = 0.85
CRITICAL_RATIO = 0.95
BUFFER_RATIO = 0.10

# Fallback when tiktoken not available: ~4 chars per token (English/code)
CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str, encoding_name: str | None = None) -> int:
    """Estimate token count for text. Uses tiktoken when available, else chars//4.

    Per rule: accurate token counting (e.g. tiktoken); model-specific limits apply.
    """
    if not text:
        return 0
    try:
        import tiktoken
        # Prefer cl100k_base (OpenAI) for compatibility with common models
        enc = tiktoken.get_encoding(encoding_name or "cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(0, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _split_key_files_sections(context: str) -> tuple[str, str]:
    """Split context into (structure_part, key_files_part). Structure = up to and including '## Key files\\n\\n'."""
    key_marker = "\n\n## Key files\n\n"
    idx = context.find(key_marker)
    if idx == -1:
        return context, ""
    return context[: idx + len(key_marker)], context[idx + len(key_marker) :]


def _sections_from_key_files(key_files_part: str) -> list[str]:
    """Split '## Key files' body into list of '### path\\n\\ncontent' sections (first may be empty)."""
    if not key_files_part.strip():
        return []
    # Sections start with ### 
    parts = re.split(r"(?=\n### )", key_files_part)
    return [p.strip() for p in parts if p.strip()]


def compress_context(
    context: str,
    max_tokens: int,
    buffer_ratio: float = BUFFER_RATIO,
) -> str:
    """Trim context to fit within max_tokens (FIFO: keep structure + leading key file sections).

    Preserves "## Repository structure" and "## Key files" header; trims from the end
    of key file sections. Per rule: FIFO when recent/leading context is most important.
    """
    if max_tokens <= 0:
        return context
    target_tokens = int(max_tokens * (1 - buffer_ratio))
    structure_part, key_files_part = _split_key_files_sections(context)
    structure_tokens = estimate_tokens(structure_part)
    if structure_tokens >= target_tokens:
        return structure_part + "\n\n(Content omitted: structure alone exceeds limit.)"
    remaining = target_tokens - structure_tokens
    sections = _sections_from_key_files(key_files_part)
    kept: list[str] = []
    used = 0
    for sec in sections:
        sec_tokens = estimate_tokens(sec)
        if used + sec_tokens <= remaining:
            kept.append(sec)
            used += sec_tokens
        else:
            break
    result = structure_part
    if kept:
        result += "\n\n".join(kept)
    result += "\n\n(Additional files omitted due to context limit.)"
    return result


def compress_context_if_needed(
    context: str,
    model_limit_tokens: int,
    buffer_ratio: float = BUFFER_RATIO,
    warning_ratio: float = WARNING_RATIO,
    compression_ratio: float = COMPRESSION_RATIO,
) -> tuple[str, bool, dict]:
    """Apply compression when context exceeds threshold. Return (final_context, was_compressed, stats).

    Stats include token counts and threshold info for logging (transparency per rule).
    """
    tokens = estimate_tokens(context)
    stats: dict = {
        "input_tokens": tokens,
        "model_limit_tokens": model_limit_tokens,
        "warning_at": int(model_limit_tokens * warning_ratio),
        "compression_at": int(model_limit_tokens * compression_ratio),
    }
    if tokens <= int(model_limit_tokens * compression_ratio):
        if tokens >= int(model_limit_tokens * warning_ratio):
            stats["warning"] = True
        return context, False, stats
    compressed = compress_context(context, model_limit_tokens, buffer_ratio)
    out_tokens = estimate_tokens(compressed)
    stats["output_tokens"] = out_tokens
    stats["compressed"] = True
    return compressed, True, stats
