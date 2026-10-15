# choregos-agent-sdk

Write a [Choregos](https://github.com/VargaFoundation/choregos) agent in Python. An agent is one
async function; the SDK speaks the Agent Client Protocol (ACP) to the platform's runner, so the
agent runs under the same guarantees as any coding agent: its tool calls and writes go through the
runner's permission path, its turns are counted, it is journaled, it can be cancelled, and its
result has the contract's shape.

```python
from choregos_agent_sdk import Context, Result, agent


@agent(name="ticket-triager")
async def run(ctx: Context) -> Result:
    ticket = ctx.task  # the work item, the inputs, the expected outputs
    tools = await ctx.tools()  # the run's MCP servers
    if await tools.has("knowledge_search"):
        hits = await tools.call("knowledge_search", {"query": ticket[:200]})  # asks the guard first
    reply = await ctx.llm.chat([{"role": "user", "content": ticket}])  # the run's gateway key
    await ctx.write_output("triage.md", reply.text)  # a deliverable, after the guard allowed it
    return Result(summary="triaged", facts={"priority": "P2"})


if __name__ == "__main__":
    run.serve()
```

Package it in an image built `FROM` the Choregos runner image, give the image a name in the
platform's `CHOREGOS_AGENT_IMAGES`, and register the agent with
`runtime: {kind: code, image: <name>, command: [python, -m, my_agent]}`. The full guide is
[docs/code-agents.md](https://github.com/VargaFoundation/choregos/blob/main/docs/code-agents.md).

## What the SDK does for you

- `initialize`, `session/new` and `session/prompt` of ACP; `session/cancel` cancels your function.
- `ctx.tools().call(...)` and `ctx.write_output(...)` announce the call, send
  `session/request_permission`, and run only if the runner's guard allowed it. A refusal raises
  `PermissionDenied`.
- `ctx.llm` reads the model from the run's environment (the gateway, the run's own key).
  `ctx.llm.chat` counts as a turn; at the step's `max_turns` the agent stops with `reason=max_turns`.
- What you `return` is validated against `choregos/StageResult/v1` and written to
  `.choregos/result.json`; an exception becomes a `failed` result that says why.
- Standard output belongs to the protocol: `print` goes to standard error.

## What it does not do

Your function is ordinary Python in the run's pod: the SDK governs what goes through it, the
sandbox (RuntimeClass, network policy, egress proxy, the run's token) bounds the rest. Calling an
MCP server or a provider directly works and skips the guard: do not.
