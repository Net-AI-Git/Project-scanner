"""Planner service: LLM-based strategic plan for security scans."""

from __future__ import annotations

from importlib.resources import files

from jinja2 import Template
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from summary_api.clients.llm_client import LLMClientError
from summary_api.models.schemas import StrategicPlan

_SYSTEM_PROMPT = (
    files("summary_api.prompts")
    .joinpath("planner_system_scan.j2")
    .read_text(encoding="utf-8")
).strip()
_USER_TEMPLATE = Template(
    files("summary_api.prompts")
    .joinpath("planner_user_scan.j2")
    .read_text(encoding="utf-8")
)


async def plan_scan(
    scan_goal: str,
    file_count: int,
    file_types_summary: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 60.0,
) -> dict:
    """Produce strategic plan (goals, risk_focus, strategy) for the security scan.

    Returns:
        Dict suitable for state.strategic_plan (StrategicPlan.model_dump()).
    """
    if not (api_key or "").strip():
        raise LLMClientError(
            "LLM API key is not configured. Set NEBIUS_API_KEY in the environment.",
            is_transient=False,
        )
    user_message = _USER_TEMPLATE.render(
        scan_goal=scan_goal,
        file_count=file_count,
        file_types_summary=file_types_summary or "unknown",
    )
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        timeout=timeout,
    )
    provider = OpenAIProvider(openai_client=client)
    llm_model = OpenAIChatModel(model, provider=provider)
    agent = Agent(
        llm_model,
        output_type=StrategicPlan,
        system_prompt=_SYSTEM_PROMPT,
    )
    result = await agent.run(user_message)
    out = result.output
    if not isinstance(out, StrategicPlan):
        raise LLMClientError("LLM did not return a valid StrategicPlan.", is_transient=False)
    return out.model_dump()
