# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on Pydantic AI, with a typed output.

- the model: `OpenAIProvider(openai_client=ctx.llm.openai_client())` — the run's gateway and key,
  every request counted against `max_turns`;
- the tools: `pydantic_ai.toolset(...)` — not the framework's own MCP client, which would call the
  run's servers directly and skip the runner's permission request;
- the typed output (`Triage`) maps onto the result's `facts`.
"""

from __future__ import annotations

from choregos_agent_sdk import Context, Result, agent
from choregos_agent_sdk.errors import sdk_error_in
from choregos_agent_sdk.integrations import pydantic_ai as bridge
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


class Triage(BaseModel):
    priority: str  # P1, P2 or P3
    reading: str


@agent(name="pydantic-ai-triager")
async def run(ctx: Context) -> Result:
    triage: Triage | None = None
    if ctx.llm.available:
        model = OpenAIChatModel(
            ctx.llm.model or "", provider=OpenAIProvider(openai_client=ctx.llm.openai_client())
        )
        triager = Agent(
            model,
            output_type=Triage,
            instructions="You triage tickets: a priority P1, P2 or P3 and a two-sentence reading.",
            toolsets=[await bridge.toolset(await ctx.tools())],
        )
        try:
            triage = (await triager.run(ctx.task[:4000])).output
        except Exception as problem:  # the model failed or answered off-schema: the deliverable says so
            if sdk_error_in(problem) is not None:
                raise  # the turn limit or the guard ends the step
            await ctx.say(f"no model reading: {type(problem).__name__}")
    body = (
        f"# Triage: {triage.priority}\n\n{triage.reading}\n" if triage else "# Triage\n\n(no model reading)\n"
    )
    await ctx.write_output("triage.md", body)
    return Result(
        summary=f"Triaged as {triage.priority}" if triage else "Triaged without a model reading",
        facts={"model_used": triage is not None, **({"priority": triage.priority} if triage else {})},
    )
