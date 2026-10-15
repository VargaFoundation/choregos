# LangGraph example: the ticket triager as a graph

The same agent as `../plain`, written as a LangGraph graph of three nodes (`lookup`, `read`,
`deliver`) on the `choregos-agent-sdk`.

Three things stay governed, and the example shows how:

| What | How | Why not the direct way |
| --- | --- | --- |
| The model | `ChatOpenAI(base_url=ctx.llm.base_url, api_key=ctx.llm.api_key)` | the run's gateway and the run's own key: its spend is the run's budget, no provider key is in the pod |
| MCP tools | `ctx.tools().call(...)`, wrapped by `as_langchain_tool` | `langchain-mcp-adapters` calls the servers directly: no permission request, no journal line, no turn count. The runner's guard would never see the call |
| Deliverables | `ctx.write_output(...)` | asks the guard before it writes |

Calls made on a LangChain model are not counted as turns by the SDK (only `ctx.llm.chat` is); the
run's key bounds their spend, and the runner still counts every tool call.

LangGraph and LangChain are heavy, so this example is **not** in the repository's workspace or lock
file. It is built and checked by the manual workflow `.github/workflows/code-agent-langgraph.yml`
(`workflow_dispatch`), which builds the image and runs `make conformance-agent` against it. It is
not part of the blocking CI.

```
docker build -f examples/agents/langgraph/Dockerfile -t langgraph-triager .
make conformance-agent IMAGE=langgraph-triager \
  COMMAND='["/opt/agents/langgraph/bin/python","-m","langgraph_triager"]'
```
