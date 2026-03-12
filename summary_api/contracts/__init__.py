"""API contracts and interfaces for swappable agent components.

Implements: .cursor/rules/agents/agent-component-interfaces.
Contract scope (contract-scope-and-boundaries): explicit contracts only at
boundaries—RepoFetcher, ContextBuilder. Internal helpers in
implementations stay implicit; add ABCs only when replaceability is required.
"""

from summary_api.contracts.interfaces import (
    ContextBuilder,
    RepoFetcher,
    ReportSynthesizer,
    VulnerabilityScanner,
)

__all__ = [
    "ContextBuilder",
    "RepoFetcher",
    "ReportSynthesizer",
    "VulnerabilityScanner",
]
