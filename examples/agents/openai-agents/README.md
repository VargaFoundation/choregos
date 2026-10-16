# the OpenAI Agents SDK example: the ticket triager

The same agent as `../plain`, on the OpenAI Agents SDK and the `choregos-agent-sdk`: set_tracing_disabled(True) (its tracing would send run data to OpenAI).

Two things stay governed, each in a few lines: the model goes through `ctx.llm.openai_client()` (the
run's gateway and key; every request counts as a turn against `max_turns`), and the tools come from
the SDK's bridge (`openai_agents` bridge over `await ctx.tools()`), not from the framework's own MCP client,
which would call the run's servers directly and skip the runner's permission request.

The framework is heavy, so this example is **not** in the repository's workspace or lock file. It is
built and checked by the nightly and manual workflow `.github/workflows/code-agent-examples.yml`
(non-blocking).

```
docker build -f examples/agents/openai-agents/Dockerfile -t openai-agents-triager .
make conformance-agent IMAGE=openai-agents-triager \
  COMMAND='["/opt/agents/openai-agents/bin/python","-m","openai_agents_triager"]'
```
