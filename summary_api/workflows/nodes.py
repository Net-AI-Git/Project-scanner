"""LangGraph nodes for fetch → process → summarize workflow.

Implements: .cursor/rules/agents/langgraph-architecture-and-nodes (READ→DO→WRITE→CONTROL).
Summarize node uses injected Summarizer (PydanticAI) per agentic-logic-and-tools and agent-component-interfaces.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from summary_api.clients.github_client import GitHubClientError
from summary_api.clients.llm_client import LLMClientError
from summary_api.contracts import ContextBuilder, RepoFetcher, Summarizer
from summary_api.core.audit import (
    error_detail_from_exception,
    log_audit_step,
)
from summary_api.core.context_compression import compress_context_if_needed
from summary_api.core.scratchpad import append_scratchpad
from summary_api.infrastructure.dlq import write_to_dlq
from summary_api.models.schemas import ErrorResponse, SummarizeResponse
from summary_api.services.summarizer import DEFAULT_TIMEOUT
from summary_api.workflows.state import SummarizeState

try:
    from circuitbreaker import CircuitBreakerError  # type: ignore[import-untyped]
except ImportError:
    CircuitBreakerError = Exception  # noqa: A001


def _github_error_to_status_and_message(exc: GitHubClientError) -> tuple[int, str]:
    """Map GitHubClientError to (status_code, message) for error response."""
    msg = exc.message or str(exc)
    if "Invalid GitHub URL" in msg:
        return 400, msg
    if "not found" in msg.lower() or "private" in msg.lower():
        return 404, msg
    if "timed out" in msg.lower() or "Network error" in msg:
        return 502, msg
    if "rate limit" in msg.lower() or "403" in msg:
        return 503, msg
    return 502, msg


def _llm_error_to_status_and_message(exc: Exception) -> tuple[int, str]:
    """Map LLM-related exceptions to (status_code, message)."""
    msg = str(getattr(exc, "message", exc) or exc)
    if isinstance(exc, LLMClientError):
        if getattr(exc, "is_transient", False) and "rate limit" in msg.lower():
            return 429, msg
        if "authentication" in msg.lower() or "API key" in msg or "401" in msg:
            return 401, msg
        if "rate limit" in msg.lower() or "429" in msg:
            return 429, msg
    if "timed out" in msg.lower() or "network" in msg.lower():
        return 502, msg
    if "server error" in msg.lower() or "500" in msg:
        return 502, msg
    return 502, msg


def _build_error_response(
    status_code: int,
    message: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Build error_response dict for state (main will build JSONResponse with X-Correlation-ID)."""
    return {
        "status_code": status_code,
        "content": ErrorResponse(status="error", message=message).model_dump(),
        "correlation_id": correlation_id,
    }


def make_fetch_node(fetcher: RepoFetcher):
    """Create fetch_node that uses the injected RepoFetcher (READ→DO→WRITE→CONTROL)."""

    async def fetch_node(state: SummarizeState) -> dict[str, Any]:
        """READ: github_url, github_token, github_api_base, audit_path, dlq_path, http_client.
        DO: fetch via RepoFetcher. WRITE: files or error_response + errors.
        """
        if state.get("error_response"):
            return {}
        correlation_id = state["correlation_id"]
        audit_path = state["audit_path"]
        dlq_path = state["dlq_path"]
        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        req_summary = {
            "github_url": state["github_url"],
            "has_token": bool(state.get("github_token")),
        }
        try:
            files_list = await fetcher.fetch(
                state["github_url"],
                api_base=state["github_api_base"],
                token=state.get("github_token"),
                client=state.get("http_client"),
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            if not files_list:
                log_audit_step(
                    correlation_id,
                    "fetch_repo_files",
                    "failure",
                    step_index=1,
                    input_summary=req_summary,
                    output_summary={"file_count": 0},
                    error_detail={
                        "message": "Repository is empty or has no readable files",
                        "where": "summary_api.workflows.nodes.fetch_node",
                        "error_classification": "permanent",
                    },
                    duration_ms=duration_ms,
                    start_timestamp=start_time,
                    end_timestamp=end_time,
                    audit_path=audit_path,
                )
                return {
                    "error_response": _build_error_response(
                        404,
                        "Repository is empty or has no readable files",
                        correlation_id,
                    ),
                    "errors": state.get("errors", []) + [{"step": "fetch", "message": "empty repo"}],
                }
            log_audit_step(
                correlation_id,
                "fetch_repo_files",
                "success",
                step_index=1,
                input_summary=req_summary,
                output_summary={"file_count": len(files_list)},
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {"files": files_list, "errors": [], "ERROR_COUNT": 0}
        except GitHubClientError as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            err_detail = error_detail_from_exception(
                e, "summary_api.workflows.nodes.fetch_node"
            )
            err_detail["error_classification"] = "transient" if getattr(e, "is_transient", False) else "permanent"
            log_audit_step(
                correlation_id,
                "fetch_repo_files",
                "failure",
                step_index=1,
                input_summary=req_summary,
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id,
                "fetch_repo_files",
                request_summary=req_summary,
                error_detail=err_detail,
                dlq_path=dlq_path,
            )
            status, message = _github_error_to_status_and_message(e)
            return {
                "error_response": _build_error_response(status, message, correlation_id),
                "errors": state.get("errors", []) + [{"step": "fetch", "message": message}],
            }
        except CircuitBreakerError as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            log_audit_step(
                correlation_id,
                "fetch_repo_files",
                "failure",
                step_index=1,
                input_summary=req_summary,
                error_detail={
                    "message": "Service temporarily unavailable (circuit open)",
                    "where": "summary_api.workflows.nodes.fetch_node",
                    "error_classification": "transient",
                },
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id,
                "fetch_repo_files",
                request_summary=req_summary,
                error_detail={"message": str(e), "error_classification": "transient"},
                dlq_path=dlq_path,
            )
            return {
                "error_response": _build_error_response(
                    503,
                    "Service temporarily unavailable. Try again later.",
                    correlation_id,
                ),
                "errors": state.get("errors", []) + [{"step": "fetch", "message": str(e)}],
            }

    return fetch_node


def make_process_node(processor: ContextBuilder):
    """Create process_node that uses the injected ContextBuilder (READ→DO→WRITE→CONTROL)."""

    def process_node(state: SummarizeState) -> dict[str, Any]:
        """READ: files, correlation_id, audit_path. DO: ContextBuilder. WRITE: context or error_response."""
        if state.get("error_response"):
            return {}
        files_list = state.get("files")
        if not files_list:
            return {}
        correlation_id = state["correlation_id"]
        audit_path = state["audit_path"]
        max_chars = state.get("max_context_chars", 60_000)
        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        try:
            context = processor.build_context(files_list, max_chars=max_chars)
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            log_audit_step(
                correlation_id,
                "process_repo_files",
                "success",
                step_index=2,
                input_summary={"file_count": len(files_list)},
                output_summary={"context_length": len(context)},
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {"context": context}
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            err_detail = {
                **error_detail_from_exception(e, "summary_api.workflows.nodes.process_node"),
                "error_classification": "permanent",
            }
            log_audit_step(
                correlation_id,
                "process_repo_files",
                "failure",
                step_index=2,
                input_summary={"file_count": len(files_list)},
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {
                "error_response": _build_error_response(
                    500,
                    str(e),
                    correlation_id,
                ),
                "errors": state.get("errors", []) + [{"step": "process", "message": str(e)}],
            }

    return process_node


def make_summarize_node(summarizer: Summarizer):
    """Create summarize_node that uses the injected Summarizer (READ→DO→WRITE→CONTROL)."""

    async def summarize_node(state: SummarizeState) -> dict[str, Any]:
        """READ: context, nebius_*. DO: Summarizer. WRITE: result or error_response."""
        if state.get("error_response"):
            return {}
        context = state.get("context") or ""
        if not context.strip():
            return {}
        correlation_id = state["correlation_id"]
        audit_path = state["audit_path"]
        dlq_path = state["dlq_path"]
        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        input_summary = {
            "context_length": len(context),
            "provider": "nebius",
        }
        append_scratchpad(
            f"summarize_node input context_length={len(context)}",
            correlation_id=correlation_id,
            step="summarize_node",
        )
        # Context compression (context-compression-and-optimization): trim when over threshold
        context_limit_tokens = state.get("context_limit_tokens") or 128_000
        context_to_use, was_compressed, compression_stats = compress_context_if_needed(
            context,
            model_limit_tokens=context_limit_tokens,
        )
        if was_compressed:
            append_scratchpad(
                f"context compressed: input_tokens={compression_stats.get('input_tokens')} "
                f"output_tokens={compression_stats.get('output_tokens')}",
                correlation_id=correlation_id,
                step="summarize_node",
            )
        try:
            out = await summarizer.summarize(
                context_to_use,
                api_key=state["nebius_api_key"],
                base_url=state["nebius_base_url"],
                model=state["nebius_model"],
                max_tokens=state.get("nebius_max_tokens", 4096),
                timeout=DEFAULT_TIMEOUT,
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            result_dict = out.model_dump()
            log_audit_step(
                correlation_id,
                "summarize_repo",
                "success",
                step_index=3,
                input_summary=input_summary,
                output_summary={
                    "summary_length": len(result_dict.get("summary", "") or ""),
                    "technologies_count": len(result_dict.get("technologies") or []),
                    "structure_length": len(result_dict.get("structure", "") or ""),
                },
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {"result": result_dict, "errors": [], "ERROR_COUNT": 0}
        except LLMClientError as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            err_detail = error_detail_from_exception(
                e, "summary_api.workflows.nodes.summarize_node"
            )
            err_detail["error_classification"] = "transient" if getattr(e, "is_transient", False) else "permanent"
            log_audit_step(
                correlation_id,
                "summarize_repo",
                "failure",
                step_index=3,
                input_summary=input_summary,
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id,
                "summarize_repo",
                request_summary=input_summary,
                error_detail=err_detail,
                dlq_path=dlq_path,
            )
            status, message = _llm_error_to_status_and_message(e)
            return {
                "error_response": _build_error_response(status, message, correlation_id),
                "errors": state.get("errors", []) + [{"step": "summarize", "message": message}],
            }
        except CircuitBreakerError as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            log_audit_step(
                correlation_id,
                "summarize_repo",
                "failure",
                step_index=3,
                input_summary=input_summary,
                error_detail={
                    "message": "Service temporarily unavailable (circuit open)",
                    "where": "summary_api.workflows.nodes.summarize_node",
                    "error_classification": "transient",
                },
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id,
                "summarize_repo",
                request_summary=input_summary,
                error_detail={"message": str(e), "error_classification": "transient"},
                dlq_path=dlq_path,
            )
            return {
                "error_response": _build_error_response(
                    503,
                    "Service temporarily unavailable. Try again later.",
                    correlation_id,
                ),
                "errors": state.get("errors", []) + [{"step": "summarize", "message": str(e)}],
            }
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            err_detail = {
                **error_detail_from_exception(e, "summary_api.workflows.nodes.summarize_node"),
                "error_classification": "permanent",
            }
            log_audit_step(
                correlation_id,
                "summarize_repo",
                "failure",
                step_index=3,
                input_summary=input_summary,
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id,
                "summarize_repo",
                request_summary=input_summary,
                error_detail=err_detail,
                dlq_path=dlq_path,
            )
            status, message = _llm_error_to_status_and_message(e)
            return {
                "error_response": _build_error_response(status, message, correlation_id),
                "errors": state.get("errors", []) + [{"step": "summarize", "message": str(e)}],
            }

    return summarize_node
