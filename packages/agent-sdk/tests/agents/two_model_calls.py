"""Test agent: two model calls; the second passes a max_turns of one."""

from choregos_agent_sdk import Context, Result, agent


@agent(name="two-calls")
async def run(ctx: Context) -> Result:
    await ctx.llm.chat([{"role": "user", "content": "one"}])
    await ctx.llm.chat([{"role": "user", "content": "two"}])
    return Result(summary="did two calls")


if __name__ == "__main__":
    run.serve()
