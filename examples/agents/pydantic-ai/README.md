# Pydantic AI example: the ticket triager

The same agent as `../plain`, on Pydantic AI and the `choregos-agent-sdk`: a typed output (`Triage`) that maps onto the result's facts.

Two things stay governed, each in a few lines: the model goes through `ctx.llm.openai_client()` (the
run's gateway and key; every request counts as a turn against `max_turns`), and the tools come from
the SDK's bridge (`pydantic_ai` bridge over `await ctx.tools()`), not from the framework's own MCP client,
which would call the run's servers directly and skip the runner's permission request.

The framework is heavy, so this example is **not** in the repository's workspace or lock file. It is
built and checked by the nightly and manual workflow `.github/workflows/code-agent-examples.yml`
(non-blocking).

```
docker build -f examples/agents/pydantic-ai/Dockerfile -t pydantic-ai-triager .
make conformance-agent IMAGE=pydantic-ai-triager \
  COMMAND='["/opt/agents/pydantic-ai/bin/python","-m","pydantic_ai_triager"]'
```
