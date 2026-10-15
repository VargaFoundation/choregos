"""Test agent: calls one MCP tool, one model, writes one deliverable."""

from choregos_agent_sdk import Context, Result, agent


@agent(name="calls-tool")
async def run(ctx: Context) -> Result:
    print("this goes to standard error, not to the protocol")
    tools = await ctx.tools()
    found = await tools.call("knowledge_search", {"query": "refund policy"})
    reply = await ctx.llm.chat([{"role": "user", "content": "say hi"}])
    await ctx.write_output("note.md", f"{found.text} / {reply.text}")
    return Result(summary=f"tool said: {found.text}", facts={"n": 1, "ok": True})


if __name__ == "__main__":
    run.serve()
