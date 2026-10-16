"""Test agent: raises, and returns a bad fact."""

from choregos_agent_sdk import Context, Result, agent


@agent(name="raises")
async def run(ctx: Context) -> Result:
    if "bad-fact" in ctx.prompt:
        return Result(summary="x", facts={"nested": {"a": 1}})  # type: ignore[dict-item]
    raise RuntimeError("boom")


if __name__ == "__main__":
    run.serve()
