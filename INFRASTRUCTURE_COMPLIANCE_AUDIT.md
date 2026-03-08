# Infrastructure Rules Compliance Audit

This document is the result of a systematic file-by-file, rule-by-rule comparison of the project against the six infrastructure rules under `.cursor/rules/infrastructure`. Every file in scope is compared against every applicable rule.

**Audit date:** 2026-03-08  
**Scope:** summary_api application code, requirements.txt, README.md, scripts/debug_repo_flow.py.

---

## 1. Full compliance matrix (File × Rule)

For each cell: **Compliant** / **Partial** / **Gap** / **N/A**.

| File | R1 Deployment | R2 Monitoring | R3 Multi-tenancy | R4 Performance | R5 Rate/Queue | R6 Uvicorn |
|------|----------------|---------------|------------------|----------------|---------------|------------|
| summary_api/main.py | Partial | Partial | N/A | Compliant | Compliant | N/A |
| summary_api/config.py | Compliant | N/A | N/A | N/A | N/A | N/A |
| summary_api/audit.py | Partial | Compliant | N/A | N/A | N/A | N/A |
| summary_api/dlq.py | N/A | Compliant | N/A | N/A | Compliant | N/A |
| summary_api/github_client.py | N/A | Partial | N/A | Partial | Compliant | N/A |
| summary_api/llm_client.py | N/A | Partial | N/A | Partial | Compliant | N/A |
| summary_api/repo_processor.py | N/A | Partial | N/A | Compliant | N/A | N/A |
| summary_api/schemas.py | N/A | N/A | N/A | N/A | N/A | N/A |
| requirements.txt | Compliant | N/A | N/A | N/A | N/A | Compliant |
| README.md | Partial | Partial | N/A | N/A | N/A | Partial |
| scripts/debug_repo_flow.py | N/A | Compliant | N/A | N/A | N/A | N/A |

**Legend:**
- **Compliant:** Meets the rule for this file.
- **Partial:** Some requirements met; gaps listed in Section 3.
- **Gap:** Requirement not met (see Gaps list).
- **N/A:** Rule does not apply to this file.

---

## 2. Checklist per rule (R1–R6)

### R1 — deployment-and-infrastructure

| Check | main.py | audit.py | config | README | Dockerfile/CI/K8s | Status |
|-------|---------|----------|--------|--------|-------------------|--------|
| Logs to stdout/stderr | Yes (StreamHandler when LOG_FORMAT=json) | Audit writes to file; app logs to stdout | — | — | — | Partial (audit to file is acceptable for audit trail; app logs to stdout) |
| Health probes `/health/live`, `/health/ready` | **Missing** | — | — | Not documented | — | **Gap** |
| Uvicorn with `--workers` in production | — | — | — | Not documented | No Dockerfile | **Gap** |
| Docker: non-root, no secrets in image | — | — | — | — | No Dockerfile | **Gap** |
| No direct prod deploy; CI/CD pipeline | — | — | — | — | No CI/CD | **Gap** |

**Relevant files:** main.py, README.md, (future) Dockerfile, CI config, K8s manifests.

---

### R2 — monitoring-and-observability

| Check | main.py | audit.py | dlq.py | github_client | llm_client | repo_processor | Status |
|-------|---------|----------|--------|---------------|------------|----------------|--------|
| Log: timestamp, correlation_id, operation_name | Yes (JSON formatter) | Yes (timestamp, correlation_id) | Yes (timestamp, correlation_id) | No structured log/spans | No structured log/spans | No timing in module | Partial |
| Operations: start_timestamp, end_timestamp, duration_ms | duration_ms only; no start/end in log | duration_ms in step metadata | — | — | — | — | **Partial** |
| Splunk HEC ingestion | Not implemented | — | — | — | — | — | **Gap** (or out-of-scope) |
| Health endpoints /health/live, /health/ready | **Missing** | — | — | — | — | — | **Gap** |
| JSON structured logs | Yes when LOG_FORMAT=json | Yes (AUDIT.jsonl) | Yes (DLQ.jsonl) | — | — | — | Compliant |

**Relevant files:** main.py, audit.py, dlq.py, github_client.py, llm_client.py, repo_processor.py.

---

### R3 — multi-tenancy-and-isolation

| Check | audit.py | schemas | Other | Status |
|-------|----------|--------|-------|--------|
| Tenant model present | tenant_id: None | No tenant in request/response | No tenant in flow | N/A |
| If tenants: filter by tenant_id, validation | — | — | — | N/A |

**Conclusion:** No tenant model; all files **N/A**. Document as “single-tenant; if multi-tenant is added, apply R3 (tenant_id, validation, isolation).”

---

### R4 — performance-optimization

| Check | main.py | github_client | llm_client | repo_processor | Status |
|-------|---------|---------------|------------|----------------|--------|
| duration_ms with time.perf_counter() | Yes | — | — | — | Compliant |
| HTTP connection reuse / pooling | — | New AsyncClient per call | New AsyncClient per call | — | **Partial** (no shared pool) |
| Batch / context limits | — | max_files limit | — | DEFAULT_MAX_CONTEXT_CHARS | Compliant |
| Connection pool (DB) | — | — | — | No DB | N/A |

**Relevant files:** main.py, github_client.py, llm_client.py, repo_processor.py.

---

### R5 — rate-limiting-and-queue-management

| Check | main.py | github_client | llm_client | dlq.py | Status |
|-------|---------|---------------|------------|--------|--------|
| Handle 429 with backoff | Returns 503/429 to client | Retry + circuit breaker | Retry + circuit breaker | — | Compliant |
| DLQ for failed requests | Calls write_to_dlq on failure | — | — | Implements DLQ | Compliant |
| API key from env only | — | Token from param (main passes from config) | Key from param (main passes from config) | — | Compliant |
| Redis / distributed rate limiting | Not implemented | — | — | — | N/A (single-instance) or **Gap** if multi-instance |

**Relevant files:** main.py, github_client.py, llm_client.py, dlq.py.

---

### R6 — uvicorn-asgi-server

| Check | requirements.txt | README | Dockerfile (future) | Status |
|-------|-------------------|--------|----------------------|--------|
| uvicorn[standard] in deps | Yes | — | — | Compliant |
| Dev: --reload, no --workers | — | Not stated; only host/port | — | **Partial** |
| Prod: --workers N, no --reload | — | Not stated | Not present | **Gap** (when Dockerfile exists) |
| Do not run as python main.py server | — | Uses uvicorn | — | Compliant |

**Relevant files:** requirements.txt, README.md, (future) Dockerfile.

---

## 3. Gaps list (prioritized)

| # | Rule | File(s) | Description | Priority |
|---|------|---------|-------------|----------|
| G1 | R1, R2 | main.py | Add GET `/health/live` and GET `/health/ready` (with optional ~5s cache for ready). Required for K8s and monitoring. | High |
| G2 | R1, R6 | README.md | Document: development run with `--reload`; production run with `--workers N`; do not use `python main.py` as server. | High |
| G3 | R1 | (new) Dockerfile | Add Dockerfile: multi-stage build, non-root USER, CMD with uvicorn and `--workers`, no secrets in image. | High (when containerizing) |
| G4 | R2 | main.py (and optionally audit/llm) | Add start_timestamp and end_timestamp to operation logs (in addition to duration_ms) for full R2 compliance. | Medium |
| G5 | R2 | (optional) | Splunk HEC: send logs/spans to Splunk. Document as out-of-scope if not using Splunk. | Low / Optional |
| G6 | R4 | github_client.py, llm_client.py | Reuse HTTP client (e.g. shared AsyncClient or connection limits) instead of creating a new client per request for connection pooling. | Medium |
| G7 | R1 | (future) | CI/CD pipeline and K8s manifests when moving to production. | When deploying |

---

## 4. Summary

- **R1 (Deployment):** Main gaps: no health endpoints, no Dockerfile/CI/K8s, README does not specify prod vs dev run.
- **R2 (Monitoring):** Good: correlation_id, timestamp, duration_ms, JSON logs. Gaps: no health endpoints, no start/end timestamps in operation logs, no Splunk HEC.
- **R3 (Multi-tenancy):** N/A (single-tenant).
- **R4 (Performance):** Good: perf_counter, batch/context limits. Partial: no HTTP connection reuse in github/llm clients.
- **R5 (Rate/Queue):** Good: retry, circuit breaker, DLQ, 429/503 handling. Redis/distributed rate limiting N/A for single-instance.
- **R6 (Uvicorn):** Good: uvicorn[standard] in requirements. Partial: README does not distinguish dev (--reload) vs prod (--workers).

**Recommended next steps:** Address G1 (health endpoints) and G2 (README) first; then G4 and G6 if improving observability and performance; add Dockerfile and CI/K8s when containerizing and deploying.

---

**Audit completion:** All files in scope were compared against all six infrastructure rules. The matrix and checklists above reflect the verified state; gaps are numbered and prioritized for remediation.
