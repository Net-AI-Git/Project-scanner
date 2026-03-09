"""ReportSynthesizer implementation: merge worker SectionFindings into VulnerabilityReport.

Implements ReportSynthesizer contract. Pure aggregation; no LLM. Deduplicates and sorts by severity.
"""

from __future__ import annotations

from summary_api.contracts import ReportSynthesizer
from summary_api.models.schemas import Finding, SectionFindings, VulnerabilityReport

_SEVERITY_ORDER = {"High": 0, "Medium": 1, "Low": 2}


class DefaultReportSynthesizer(ReportSynthesizer):
    """Merges list of SectionFindings into one VulnerabilityReport (report_path + flat findings)."""

    def synthesize(
        self,
        worker_results: list[SectionFindings],
        report_path: str,
    ) -> dict:
        """Merge per-section findings, dedupe by (file_path, line_or_region, description), sort by severity."""
        seen: set[tuple[str, str, str]] = set()
        findings: list[Finding] = []
        for section in worker_results:
            for f in section.findings:
                key = (f.file_path, f.line_or_region, f.description)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(f)
        findings.sort(key=lambda x: (_SEVERITY_ORDER.get(x.severity, 99), x.file_path, x.line_or_region))
        report = VulnerabilityReport(report_path=report_path, findings=findings)
        return report.model_dump()
