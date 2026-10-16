# SPDX-License-Identifier: Apache-2.0
"""The ticket triager on the OpenAI Agents SDK.

The gateway is OpenAI-compatible, not necessarily OpenAI, so the example:

- gives the framework a client on the run's gateway, counted against `max_turns`
  (`ctx.llm.openai_client()`), on the chat-completions API, with the model named explicitly (a name
  like `platform/standard` would otherwise be read as a provider prefix);
- turns the framework's tracing OFF: it would send run data to OpenAI's tracing service;
- takes the run's tools from `openai_agents.tools(...)`, not from the framework's own MCP client
  (which would call the servers directly and skip the runner's permission request).
"""

from __future__ import annotations

from agents import Agent, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from choregos_agent_sdk import Context, Result, agent
from choregos_agent_sdk.errors import sdk_error_in
from choregos_agent_sdk.integrations import openai_agents as bridge


@agent(name="openai-agents-triager")
async def run(ctx: Context) -> Result:
    set_tracing_disabled(True)
    reading = ""
    if ctx.llm.available:
        triager = Agent(
            name="Triager",
            instructions="You triage tickets. Use knowledge_search when it helps. Answer in two sentences.",
            model=OpenAIChatCompletionsModel(
                model=ctx.llm.model or "", openai_client=ctx.llm.openai_client()
            ),
            tools=await bridge.tools(await ctx.tools()),
        )
        try:
            outcome = await Runner.run(triager, ctx.task[:4000], max_turns=ctx.max_turns or 10)
            reading = str(outcome.final_output)
        except Exception as problem:  # the model failed: the deliverable says so
            if sdk_error_in(problem) is not None:
                raise  # the turn limit or the guard ends the step
            await ctx.say(f"no model reading: {type(problem).__name__}")
    await ctx.write_output("triage.md", f"# Triage\n\n{reading or '(no model reading)'}\n")
    return Result(
        summary="Triaged with a model reading" if reading else "Triaged without a model reading",
        facts={"model_used": bool(reading)},
    )
