# LangGraph example: the ticket triager as a graph

The same agent as `../plain`, written as a LangGraph graph of three nodes (`lookup`, `read`,
`deliver`) on the `choregos-agent-sdk`.

Two things stay governed, each in a few lines:

| What | How | Why not the direct way |
| --- | --- | --- |
| The model | `ChatOpenAI(..., http_async_client=ctx.llm.http_client())` | the run's gateway and key, and every request counts as a turn against `max_turns`. A `ChatOpenAI` built from the URL and the key alone is not counted |
| MCP tools | `langchain.tools(await ctx.tools())` | `langchain-mcp-adapters` calls the servers directly: no permission request, no journal line, no turn count. The runner's guard would never see the call |

The deliverable is `ctx.write_output(...)`: it asks the guard before it writes.

LangGraph and LangChain are heavy, so this example is **not** in the repository's workspace or lock
file. It is built and checked by the nightly and manual workflow `.github/workflows/code-agent-examples.yml`
(non-blocking), which builds the image and runs `make conformance-agent` against it. It is
not part of the blocking CI.

```
docker build -f examples/agents/langgraph/Dockerfile -t langgraph-triager .
make conformance-agent IMAGE=langgraph-triager \
  COMMAND='["/opt/agents/langgraph/bin/python","-m","langgraph_triager"]'
```
