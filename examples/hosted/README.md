# Hosted agents: one ticket triager on five frameworks and three languages

Each directory is the same agent, a ticket triager that reads the ticket through an MCP tool and
answers `{"status": "done", "summary": ...}`, written on one framework and run as a **hosted agent**
(ADR 0080, `docs/hosted-agents.md`). None of them imports anything from Choregos. Each one:

- serves Foundry's hosted-agent contract with `responses_server.py` (`responses_server.mjs` in
  Node, `Program.cs` in .NET): `GET /readiness`, `POST /responses` (with server-sent events when the
  request asks for `stream: true`) and `POST /invocations`, on `$PORT`, stopping cleanly on
  SIGTERM. Every example of a language carries an identical copy of its server and imports no
  shared library (ADR 0063); a test refuses a copy that differs;
- reads the model from `OPENAI_BASE_URL` / `OPENAI_API_KEY` (`ANTHROPIC_*` for Claude) and the
  MCP servers from `CHOREGOS_MCP_SERVERS` and `CHOREGOS_MCP_URL<name>`. The platform points these
  variables at the runner's loopback proxies, so the container never holds a secret;
- writes one function, `answer(text)`, in `agent.py` (`agent.mjs`, or `Agent.Answer` in `Agent.cs`).

| Directory | Framework (pinned in `requirements.txt`) | Wiring |
| --- | --- | --- |
| `reference` | none, standard library only | a chat-completions tool loop and a JSON-RPC MCP client |
| `agent-framework` | `agent-framework-core` 1.22.0 + `agent-framework-openai` 1.17.0 | `OpenAIChatClient(base_url=…)`, `MCPStreamableHTTPTool` |
| `agent-framework-foundry` | as `agent-framework`, + `agent-framework-foundry` 1.15.0, `agent-framework-foundry-hosting` 1.0.0b261015, `azure-identity` 1.26.0 | written for Foundry: `FoundryChatClient` with `DefaultAzureCredential`, served by Foundry's `ResponsesHostServer` (no `responses_server.py`) |
| `langgraph` | `langgraph` 1.2.16, `langchain-openai`, `langchain-mcp-adapters` | `ChatOpenAI(base_url=…)`, `MultiServerMCPClient`, `create_react_agent` |
| `openai-agents` | `openai-agents` 0.24.0 | `OpenAIChatCompletionsModel` on `AsyncOpenAI(base_url=…)`, `MCPServerStreamableHttp`, tracing off |
| `claude-agent-sdk` | `claude-agent-sdk` 0.2.168 (it bundles its CLI) | `ANTHROPIC_BASE_URL`, HTTP `mcp_servers`, no built-in tools, the CLI's own traffic off |
| `copilot-sdk` | `github-copilot-sdk` 1.0.24rc0 (a prerelease) | BYOK `provider` (`openai`, chat completions) with no GitHub account, HTTP `mcp_servers`, MCP tools only, runtime fetched at build time |
| `reference-node` | none, Node 22's standard library only | the same loop as `reference`, over `fetch` |
| `claude-agent-sdk-node` | `@anthropic-ai/claude-agent-sdk` 0.3.304 (its CLI comes from a platform package) | as `claude-agent-sdk`; `env` replaces the CLI's environment, so it starts from `process.env` |
| `copilot-sdk-node` | `@github/copilot-sdk` 1.0.23 (its runtime comes from a platform package) | as `copilot-sdk`, nothing fetched at start |
| `agent-framework-dotnet` | `Microsoft.Agents.AI.OpenAI` 1.25.0 + `ModelContextProtocol.Core` 2.2.0 | `ChatClient.AsAIAgent(…)` on `OpenAIClient(endpoint)`, one `McpClient` per server, on ASP.NET minimal APIs |

The Python examples carry a Dockerfile. The Node and .NET ones are **zips**, as a user would upload
them (`docs/hosted-agents.md`): no Dockerfile, a `choregos.json` saying what a version would declare
(`runtime`, `entry_point`, `dependency_resolution`), and the lock file (`package-lock.json`) or the
project (`Triager.csproj`) the platform builds from. The platform writes their Dockerfile:

```
uv run python tools/hosted_dockerfile.py examples/hosted/claude-agent-sdk-node   # prints it
docker build -t claude-node-triager -f <(uv run python tools/hosted_dockerfile.py examples/hosted/claude-agent-sdk-node) examples/hosted/claude-agent-sdk-node
```

`agent-framework-foundry` is the agent as a Foundry deployment holds it, run without a change (S41-19). It reads `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL`, and asks a managed identity for its token (`IDENTITY_ENDPOINT`, `IDENTITY_HEADER`). The platform points all of them at the runner's loopback model, and the token it gets back is the loopback key. The blocking CI runs it (`hosted-foundry` job).

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
# a Node example runs `node responses_server.mjs` (after `npm ci` in its directory)
make conformance-hosted AGENT=reference-node [NODE=/path/to/node]
# as an image
docker build -t langgraph-triager examples/hosted/langgraph
make conformance-hosted IMAGE=langgraph-triager
```

The blocking CI runs `reference` and `reference-node` (skipped where `node` is not installed), and
`agent-framework-foundry` in a venv of its own.
`.github/workflows/hosted-agent-examples.yml` builds each image (a zip example from the Dockerfile
the platform writes) and runs the suite against it, nightly and on demand, without blocking a merge:
the frameworks are heavy and stay out of the repository's lock file. The .NET example is compiled
there only: nothing in the blocking CI has the .NET SDK.
