"""A code agent that uses a framework-style model client: `ctx.llm.openai_client()`.

Not a test: `test_code_agents.py` runs it. It wants five model calls after its deliverable; the step allows
three turns, so the counting client must refuse the third call. Set BARE=1 for the same agent on a
bare `AsyncOpenAI`
(what a framework does when it is only given the URL and the key): nothing counts its calls.
"""

import os

from choregos_agent_sdk import Context, Result, agent
from openai import AsyncOpenAI


@agent(name="framework-like")
async def run(ctx: Context) -> Result:
    if os.environ.get("BARE"):
        client = AsyncOpenAI(base_url=ctx.llm.base_url, api_key=ctx.llm.api_key)
    else:
        client = ctx.llm.openai_client()
    await ctx.write_output("note.md", "done")  # the deliverable first: it is a turn too
    for i in range(5):
        try:
            await client.chat.completions.create(
                model=ctx.llm.model, messages=[{"role": "user", "content": str(i)}]
            )
        except Exception:  # the counting client refuses past max_turns
            break
    return Result(summary="did its calls")


if __name__ == "__main__":
    run.serve()
