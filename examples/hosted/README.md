# Hosted agents: one ticket triager on five frameworks

Each directory is the same agent, a ticket triager that reads the ticket through an MCP tool and
answers `{"status": "done", "summary": ...}`, written on one framework and run as a **hosted agent**
(ADR 0080, `docs/hosted-agents.md`). None of them imports anything from Choregos. Each one:

- serves Foundry's hosted-agent contract with `responses_server.py`: `GET /readiness`,
  `POST /responses` (with server-sent events when the request asks for `stream: true`) and
  `POST /invocations`, on `$PORT`, stopping cleanly on SIGTERM. Every example carries an identical
  copy of this file and imports no shared library (ADR 0063); a test refuses a copy that differs;
- reads the model from `OPENAI_BASE_URL` / `OPENAI_API_KEY` (`ANTHROPIC_*` for Claude) and the
  MCP servers from `CHOREGOS_MCP_SERVERS` and `CHOREGOS_MCP_URL<name>`. The platform points these
  variables at the runner's loopback proxies, so the container never holds a secret;
- writes one function, `answer(text)`, in `agent.py`.

| Directory | Framework (pinned in `requirements.txt`) | Wiring |
| --- | --- | --- |
| `reference` | none, standard library only | a chat-completions tool loop and a JSON-RPC MCP client |
| `agent-framework` | `agent-framework-core` 1.22.0 + `agent-framework-openai` 1.17.0 | `OpenAIChatClient(base_url=…)`, `MCPStreamableHTTPTool` |
| `langgraph` | `langgraph` 1.2.16, `langchain-openai`, `langchain-mcp-adapters` | `ChatOpenAI(base_url=…)`, `MultiServerMCPClient`, `create_react_agent` |
| `openai-agents` | `openai-agents` 0.24.0 | `OpenAIChatCompletionsModel` on `AsyncOpenAI(base_url=…)`, `MCPServerStreamableHttp`, tracing off |
| `claude-agent-sdk` | `claude-agent-sdk` 0.2.168 (it bundles its CLI) | `ANTHROPIC_BASE_URL`, HTTP `mcp_servers`, no built-in tools, the CLI's own traffic off |
| `copilot-sdk` | `github-copilot-sdk` 1.0.24rc0 (a prerelease) | BYOK `provider` (`openai`, chat completions) with no GitHub account, HTTP `mcp_servers`, MCP tools only, runtime fetched at build time |

Two settings are not optional, and the conformance suite fails without them:

- the SDK's own traffic must be off (OpenAI Agents' tracing, Claude Code's telemetry and error
  reports). A step's network refuses it, and `no_egress` sees the attempt;
- the built-in tools of the Claude and Copilot runtimes (shell, files, web) are switched off. The
  triager reads tickets, and the only tools it gets are the step's MCP servers.

## Run the conformance suite

The eight checks (`readiness`, `answer`, `model_at_loopback`, `mcp_at_loopback`, `result_valid`,
`max_turns`, `no_egress`, `sigterm`) run against the real loopback proxies of the runner, a scripted
model and a scripted MCP server (`tests/conformance/hosted/`):

```
# as a process, with an interpreter that has the example's requirements
make conformance-hosted AGENT=langgraph PYTHON=/path/to/venv/bin/python
# as an image
docker build -t langgraph-triager examples/hosted/langgraph
make conformance-hosted IMAGE=langgraph-triager
```

The blocking CI runs the reference. `.github/workflows/hosted-agent-examples.yml` builds each image
and runs the suite against it, nightly and on demand, without blocking a merge: the frameworks are
heavy and stay out of the repository's lock file.
