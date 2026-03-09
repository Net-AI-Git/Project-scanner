"""PydanticAI-based Summarizer implementation.

Implements: .cursor/rules/agents/agent-component-interfaces and agentic-logic-and-tools.
Config (api_key, base_url, model) injected per call; no hardcoding.
"""

from __future__ import annotations

from importlib.resources import files

from circuitbreaker import circuit
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from summary_api.clients.llm_client import LLMClientError
from summary_api.contracts import Summarizer
from summary_api.models.schemas import SummarizeResponse

RETRY_ATTEMPTS = 3
RETRY_MIN_WAIT = 1
RETRY_MAX_WAIT = 60
DEFAULT_TIMEOUT = 120.0

_SYSTEM_PROMPT = (
    files("summary_api.prompts").joinpath("system.j2").read_text(encoding="utf-8")
).strip()


def _is_llm_transient(exc: BaseException) -> bool:
    """True if the exception is a transient LLM error (retryable)."""
    return isinstance(exc, LLMClientError) and getattr(exc, "is_transient", False)


class PydanticAISummarizer(Summarizer):
    """Summarizer implementation using PydanticAI with OpenAI-compatible API (e.g. Nebius).

    Returns validated SummarizeResponse. Retry and circuit breaker applied to summarize().
    """

    async def summarize(
        self,
        context: str,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 4096,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> SummarizeResponse:
        """Run PydanticAI agent to summarize context; implements Summarizer contract."""
        return await _run_with_resilience(
            context,
            api_key=api_key,
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            timeout=timeout,
        )


async def _run_agent(
    context: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
    timeout: float = DEFAULT_TIMEOUT,
) -> SummarizeResponse:
    """Run PydanticAI agent (no retry/circuit)."""
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        timeout=timeout,
    )
    provider = OpenAIProvider(openai_client=openai_client)
    llm_model = OpenAIChatModel(model, provider=provider)
    agent = Agent(
        llm_model,
        output_type=SummarizeResponse,
        system_prompt=_SYSTEM_PROMPT,
    )
    result = await agent.run(context)
    out = result.output
    if not isinstance(out, SummarizeResponse):
        raise LLMClientError(
            "LLM did not return a valid summary structure.",
            is_transient=False,
        )
    return out


@circuit(failure_threshold=5, recovery_timeout=60, expected_exception=LLMClientError)
@retry(
    retry=retry_if_exception(_is_llm_transient),
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_random_exponential(multiplier=1, min=RETRY_MIN_WAIT, max=RETRY_MAX_WAIT),
    reraise=True,
)
async def _run_with_resilience(
    context: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
    timeout: float = DEFAULT_TIMEOUT,
) -> SummarizeResponse:
    """Run agent with retry and circuit breaker."""
    return await _run_agent(
        context,
        api_key=api_key,
        base_url=base_url,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
    )
