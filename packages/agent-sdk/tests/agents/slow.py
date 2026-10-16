"""Test agent: waits long enough to be cancelled."""

import asyncio

from choregos_agent_sdk import Context, Result, agent


@agent(name="slow")
async def run(ctx: Context) -> Result:
    await ctx.say("working")
    await asyncio.sleep(60)
    return Result(summary="never gets here")


if __name__ == "__main__":
    run.serve()
