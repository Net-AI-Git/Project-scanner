"""LangGraph nodes for the scan workflow: planner, orchestrator, workers, md_writer, synthesizer.

Implements READ→DO→WRITE→CONTROL. Uses ScanState. Fetch and process nodes are shared (see nodes.py).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from summary_api.clients.llm_client import LLMClientError
from summary_api.contracts import ReportSynthesizer, VulnerabilityScanner
from summary_api.core.audit import error_detail_from_exception, log_audit_step
from summary_api.infrastructure.dlq import write_to_dlq
from summary_api.models.schemas import (
    ErrorResponse,
    Section,
    SectionFindings,
)
from summary_api.services.planner_service import plan_scan
from summary_api.services.repo_processor import should_skip_path
from summary_api.services.report_synthesizer import DefaultReportSynthesizer
from summary_api.services.vulnerability_scanner import PydanticAIVulnerabilityScanner
from summary_api.workflows.state import ScanState

try:
    from circuitbreaker import CircuitBreakerError  # type: ignore[import-untyped]
except ImportError:
    CircuitBreakerError = Exception  # noqa: A001


def _build_error_response(status_code: int, message: str, correlation_id: str) -> dict[str, Any]:
    return {
        "status_code": status_code,
        "content": ErrorResponse(status="error", message=message).model_dump(),
        "correlation_id": correlation_id,
    }


def _llm_error_to_status(exc: Exception) -> tuple[int, str]:
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
    return 502, msg


def _file_types_summary(files: list[Any]) -> str:
    """Build a short summary of file extensions for planner (e.g. 'Python (12), JS (5)')."""
    from collections import Counter

    exts: Counter[str] = Counter()
    for f in files or []:
        path = getattr(f, "path", f) if isinstance(f, (list, tuple)) else getattr(f, "path", "")
        if isinstance(path, str) and "." in path:
            ext = path.rsplit(".", 1)[-1].lower()
            exts[ext] += 1
        else:
            exts["other"] += 1
    return ", ".join(f"{k} ({v})" for k, v in exts.most_common(10))


def _section_findings_to_md(section: SectionFindings) -> str:
    """Render one file's findings as a Markdown section."""
    lines = [f"## {section.file_path}", ""]
    if not section.findings:
        lines.append("*No findings.*")
    else:
        for f in section.findings:
            lines.append(f"- **{f.severity}** [{f.category}] {f.description}")
            lines.append(f"  - Line/region: {f.line_or_region}")
            lines.append(f"  - Recommendation: {f.recommendation}")
            lines.append("")
    return "\n".join(lines)


def make_scan_planner_node():
    """Create planner_node: READ scan_goal + files, DO plan_scan, WRITE strategic_plan."""

    async def planner_node(state: ScanState) -> dict[str, Any]:
        if state.get("error_response"):
            return {}
        files_list = state.get("files") or []
        correlation_id = state["correlation_id"]
        audit_path = state["audit_path"]
        dlq_path = state["dlq_path"]
        scan_goal = state.get("scan_goal") or "Scan repository for security vulnerabilities"
        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        try:
            plan = await plan_scan(
                scan_goal=scan_goal,
                file_count=len(files_list),
                file_types_summary=_file_types_summary(files_list),
                api_key=state["nebius_api_key"],
                base_url=state["nebius_base_url"],
                model=state["nebius_model"],
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            log_audit_step(
                correlation_id,
                "scan_planner",
                "success",
                step_index=3,
                input_summary={"file_count": len(files_list), "scan_goal": scan_goal},
                output_summary={"goals_count": len(plan.get("goals") or [])},
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {"strategic_plan": plan}
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            err_detail = {
                **error_detail_from_exception(e, "summary_api.workflows.scan_nodes.planner_node"),
                "error_classification": "permanent",
            }
            log_audit_step(
                correlation_id,
                "scan_planner",
                "failure",
                step_index=3,
                input_summary={"file_count": len(files_list)},
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            write_to_dlq(
                correlation_id, "scan_planner", request_summary={"scan_goal": scan_goal}, error_detail=err_detail, dlq_path=dlq_path
            )
            status, message = _llm_error_to_status(e)
            return {
                "error_response": _build_error_response(status, message, correlation_id),
                "errors": state.get("errors", []) + [{"step": "planner", "message": message}],
            }

    return planner_node


def make_scan_orchestrator_node():
    """Create orchestrator_node: READ strategic_plan + files, DO create one Section per file (filtered), WRITE sections."""

    def orchestrator_node(state: ScanState) -> dict[str, Any]:
        if state.get("error_response"):
            return {}
        files_list = state.get("files") or []
        correlation_id = state["correlation_id"]
        task_id = correlation_id
        sections: list[dict[str, Any]] = []
        for i, f in enumerate(files_list):
            path = getattr(f, "path", "")
            if should_skip_path(path):
                continue
            content = getattr(f, "content", "")
            section = Section(
                section_id=f"{task_id}_section_{i}",
                task_id=task_id,
                scope="Scan this file for security vulnerabilities",
                inputs={"file_path": path, "content": content},
                constraints=["Output High/Medium/Low severity only", "One section per file"],
                expected_output_shape={"type": "SectionFindings", "fields": ["file_path", "findings"]},
            )
            sections.append(section.model_dump())
        return {"sections": sections}

    return orchestrator_node


def make_scan_workers_node(scanner: VulnerabilityScanner | None = None):
    """Create workers node: run VulnerabilityScanner per section in parallel; put each result to md_queue and worker_results; put sentinel."""

    scanner = scanner or PydanticAIVulnerabilityScanner()

    async def workers_node(state: ScanState) -> dict[str, Any]:
        if state.get("error_response"):
            return {}
        sections_data = state.get("sections") or []
        if not sections_data:
            return {"worker_results": []}
        correlation_id = state["correlation_id"]
        audit_path = state["audit_path"]
        md_queue = state.get("md_queue")
        if not md_queue:
            return {
                "error_response": _build_error_response(
                    500, "Scan workflow: md_queue not initialized", correlation_id
                ),
                "errors": state.get("errors", []) + [{"step": "workers", "message": "md_queue missing"}],
            }
        worker_results: list[dict[str, Any]] = []

        async def scan_one(section_dict: dict[str, Any]) -> SectionFindings:
            inputs = section_dict.get("inputs") or {}
            file_path = inputs.get("file_path", "")
            content = inputs.get("content", "")
            return await scanner.scan(
                file_path=file_path,
                content=content,
                api_key=state["nebius_api_key"],
                base_url=state["nebius_base_url"],
                model=state["nebius_model"],
                max_tokens=state.get("nebius_max_tokens", 4096),
            )

        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        try:
            results = await asyncio.gather(
                *[scan_one(s) for s in sections_data],
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    worker_results.append(
                        SectionFindings(
                            file_path=sections_data[i].get("inputs", {}).get("file_path", "?"),
                            findings=[],
                        ).model_dump()
                    )
                    continue
                sf = r
                worker_results.append(sf.model_dump())
                md_queue.put_nowait(sf)
            md_queue.put_nowait(None)
            duration_ms = (time.perf_counter() - t0) * 1000
            end_time = datetime.now(timezone.utc).isoformat()
            log_audit_step(
                correlation_id,
                "scan_workers",
                "success",
                step_index=4,
                input_summary={"sections_count": len(sections_data)},
                output_summary={"worker_results_count": len(worker_results)},
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=end_time,
                audit_path=audit_path,
            )
            return {"worker_results": worker_results}
        except Exception as e:
            duration_ms = (time.perf_counter() - t0) * 1000
            err_detail = {
                **error_detail_from_exception(e, "summary_api.workflows.scan_nodes.workers_node"),
                "error_classification": "permanent",
            }
            log_audit_step(
                correlation_id,
                "scan_workers",
                "failure",
                step_index=4,
                input_summary={"sections_count": len(sections_data)},
                error_detail=err_detail,
                duration_ms=duration_ms,
                start_timestamp=start_time,
                end_timestamp=datetime.now(timezone.utc).isoformat(),
                audit_path=audit_path,
            )
            status, message = _llm_error_to_status(e)
            return {
                "error_response": _build_error_response(status, message, correlation_id),
                "errors": state.get("errors", []) + [{"step": "workers", "message": message}],
            }

    return workers_node


def make_scan_md_writer_node():
    """Create md_writer node: drain md_queue, append each SectionFindings to MD file, set report_path."""

    async def md_writer_node(state: ScanState) -> dict[str, Any]:
        if state.get("error_response"):
            return {}
        md_queue = state.get("md_queue")
        if not md_queue:
            return {}
        correlation_id = state["correlation_id"]
        scan_reports_dir = state.get("scan_reports_dir") or "reports"
        report_path = str(Path(scan_reports_dir) / f"scan_{correlation_id}.md")
        Path(scan_reports_dir).mkdir(parents=True, exist_ok=True)
        start_time = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        header = f"# Security Scan Report\n\nCorrelation ID: `{correlation_id}`\n\n"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(header)
            while True:
                item = await md_queue.get()
                if item is None:
                    break
                if isinstance(item, SectionFindings):
                    f.write(_section_findings_to_md(item))
                    f.write("\n")
                elif isinstance(item, dict):
                    sf = SectionFindings.model_validate(item)
                    f.write(_section_findings_to_md(sf))
                    f.write("\n")
        duration_ms = (time.perf_counter() - t0) * 1000
        log_audit_step(
            correlation_id,
            "scan_md_writer",
            "success",
            step_index=5,
            input_summary={},
            output_summary={"report_path": report_path},
            duration_ms=duration_ms,
            start_timestamp=start_time,
            end_timestamp=datetime.now(timezone.utc).isoformat(),
            audit_path=state["audit_path"],
        )
        return {"report_path": report_path}

    return md_writer_node


def make_scan_synthesizer_node(synthesizer: ReportSynthesizer | None = None):
    """Create synthesizer_node: READ worker_results + report_path, DO ReportSynthesizer.synthesize, WRITE result."""

    synthesizer = synthesizer or DefaultReportSynthesizer()

    def synthesizer_node(state: ScanState) -> dict[str, Any]:
        if state.get("error_response"):
            return {}
        worker_results_raw = state.get("worker_results") or []
        report_path = state.get("report_path") or ""
        correlation_id = state["correlation_id"]
        worker_results = [SectionFindings.model_validate(r) for r in worker_results_raw]
        result_dict = synthesizer.synthesize(worker_results, report_path)
        return {"result": result_dict, "errors": [], "ERROR_COUNT": 0}

    return synthesizer_node
