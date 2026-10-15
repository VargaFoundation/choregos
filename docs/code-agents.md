# Agents written in code

A **code agent** is an agent whose behaviour is a Python program, not a set of instructions for a
coding agent. It runs under the same guarantees as any other agent: the runner starts it, speaks
ACP to it, decides its permissions, counts its turns, journals its tool calls, can cancel it, and
reads its result in the contract's shape. The decision and the alternatives are in
[ADR 0063](adr/0063-agents-written-in-code.md).

## Which one do I write?

| You have | Use |
| --- | --- |
| a judgement that instructions and a model can carry | a **prompt agent**: a registry agent version with `instructions` and a coding-agent backend |
| a fixed computation with no model (a backtest, a conversion) | a **`job`** actor ([ADR 0056](adr/0056-a-deterministic-step.md)) |
| a program of your own — a pipeline with a few model calls, a LangGraph graph, existing Python — that must run governed | a **code agent** (this page) |

## Write it

```bash
pip install choregos-agent-sdk      # until it is on PyPI: pip install ./packages/agent-sdk
```

```python
from choregos_agent_sdk import Context, Result, agent


@agent(name="ticket-triager")
async def run(ctx: Context) -> Result:
    ticket = ctx.task                                  # .choregos/task.md: the work item, inputs, expected outputs
    tools = await ctx.tools()                          # the run's MCP servers (choregos-tools, memory, connectors)
    if await tools.has("knowledge_search"):
        hits = await tools.call("knowledge_search", {"query": ticket[:200]})
    reply = await ctx.llm.chat([{"role": "user", "content": ticket}])
    await ctx.write_output("triage.md", reply.text)    # .choregos/outputs/: stored as an artifact
    return Result(summary="triaged", facts={"priority": "P2"})


if __name__ == "__main__":
    run.serve()
```

| In the context | What it is |
| --- | --- |
| `ctx.task`, `ctx.prompt` | the task file; the prompt the runner sent this turn (on a repair turn: the correction asked) |
| `ctx.fields`, `ctx.inputs`, `ctx.skills` | the work item's fields, the declared inputs as files, the agent's skills folder |
| `ctx.llm` | the run's model through the gateway: `await ctx.llm.chat(messages)` (one counted turn), `ctx.llm.client` (an `openai.AsyncOpenAI`), `base_url`, `api_key`, `model` |
| `ctx.tools()` | `names()`, `has(tool)`, `specs()`, `call(tool, args)` |
| `ctx.write_output(name, content)` | a deliverable, after the guard allowed it |
| `ctx.say(text)` | a line in the run's journal |

`Result(summary, status="done", reason=None, facts={}, outputs={}, questions=[])` is validated against
`choregos/StageResult/v1` and written to `.choregos/result.json`. `facts` are named plain values a
gate can read. An exception becomes a `failed` result (`reason=agent_error`); `PermissionDenied` ends
it with `reason=permission_denied`; the step's turn limit with `reason=max_turns`.

## What the SDK guarantees, and what it cannot

The runner governs a coding agent by what the agent *tells* it. The SDK tells it everything it does
through the SDK:

- **Permissions.** `tools.call` and `write_output` announce a `tool_call`, send
  `session/request_permission`, and run the action only if the runner's guard allowed it. A refusal
  raises `PermissionDenied`; the action never happens.
- **Turns.** Model calls (`ctx.llm.chat`) and tool calls count. The runner's turn guard counts them
  from outside (and stops the same call repeated in a loop); the SDK also stops at the step's
  `max_turns` before the call that would pass it.
- **The model.** Only through the environment the backend sets from the run: the gateway URL and the
  **run's own key** (`OPENAI_*`, or `ANTHROPIC_*` for `api_format: anthropic`). No provider key is
  ever in the pod; the key carries the run's budget.
- **Standard output** is the protocol's. `print` goes to standard error.
- **Cancel and limits.** `session/cancel` cancels your function. A code agent does not resume a
  conversation (`session/load` is unsupported in v1): a retry replays the function from its start.

What the SDK cannot do: your function is ordinary Python in the run's pod. Calling an MCP server or
a provider directly (for instance `langchain-mcp-adapters` on the run's servers) works and
**skips the guard**: no permission request, no journal line, no turn count. Use `tools.call`. The
sandbox — RuntimeClass, network policy, the egress proxy, the run's token, no extra secret — bounds
what your code can reach, exactly as it bounds the shell of a coding agent.

## Ship it

1. **Build the image FROM the runner image.** The runner is the entrypoint; your agent is the
   subprocess it starts. Give the agent its own virtualenv so its dependencies never shadow the
   runner's. See `examples/agents/plain/Dockerfile`.
2. **Check it** with the conformance suite (below), then push the image and take its digest.
3. **Name it, as the operator.** Images are never written by a workflow or an agent. The operator
   maps a name to a digest-pinned reference (`global.images.agents` in the chart, see
   [deployment](deployment.md#images-for-jobs-and-for-agents-written-in-code)).
4. **Register the agent.** The version's `runtime` replaces `backend`:

   ```json
   { "spec": { "model": "profile:standard",
               "runtime": { "kind": "code", "image": "ticket-triager",
                            "command": ["/opt/agents/plain/bin/python", "-m", "choregos_agent_examples.plain"],
                            "api_format": "openai" } } }
   ```

   In the console, the agent builder's **Runtime** selector offers "Coding agent (ACP backend)" or
   "Code (image)": the second swaps the backend for an image picker (the names your operator gave;
   one that is not pinned by digest is flagged), a command editor (one argument per line) and the
   model API. The agent's page says `Code · <image>` and whether the image is pinned.

   An unknown image name is refused when the version is published (the known names are in the error);
   `GET /platform/agent-images` lists them for the console. `runtime: code` with `backend` is refused.
5. **Run it** from a workflow like any registry agent (`{type: agent, role: ..., agent: ticket-triager}`).
   An actor that also names a `backend:` is refused. Use an executor that starts a pod per run
   (`k8s_job`): `agent_sandbox` claims from one warm pool built from the runner image and refuses a
   run whose image differs.

## Conformance

A code agent passes the same seven checks as a shipped backend before it is trusted:
`initialize`, `mcp`, `prompt_trivial`, `permission_denied`, `result_valid`, `max_turns`,
`cost_at_gateway` (`tests/conformance/backends/test_code_agents.py`). A code agent is not scripted, so
what is observed is a deliverable the SDK writes through the guard: the agent is prompted with a
ticket and must leave a file in `.choregos/outputs/`; then every write is refused and the file must
not appear; the result file must be valid after both turns; the runner's turn guard must not trip on
the first; and the gateway must see no key but the run's.

```bash
make conformance        # the plain example runs here, in CI
make conformance-agent IMAGE=<ref> COMMAND='["/opt/agents/plain/bin/python","-m","choregos_agent_examples.plain"]'
```

`CHOREGOS_CONFORMANCE_CODE_AGENTS` (`{name: argv}` or `{name: {image, command}}`) names the agents
the suite runs; an image is run with `docker run -i`.

## Examples

- [`examples/agents/plain`](../examples/agents/plain): the ticket triager in plain Python. Passes the
  seven checks in CI.
- [`examples/agents/langgraph`](../examples/agents/langgraph): the same agent as a LangGraph graph.
  LangGraph is heavy and outside the repository's lock file: it is built and checked by a manual
  workflow (`code-agent-langgraph`), not by the blocking CI.
