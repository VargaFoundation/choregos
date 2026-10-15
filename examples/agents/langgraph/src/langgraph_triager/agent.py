# SPDX-License-Identifier: Apache-2.0
"""The ticket triager as a LangGraph graph.

Three nodes: `lookup` (the knowledge search, as a governed tool call), `read` (the model, through a
LangChain chat model pointed at the run's gateway) and `deliver` (the deliverable). The graph is
ordinary LangGraph; the three things the platform governs are wired through the SDK:

- the model: `ChatOpenAI(base_url=ctx.llm.base_url, api_key=ctx.llm.api_key)` — the run's gateway and
  the run's own key, nothing else; its spend is bounded by the run's budget;
- the tool: `ctx.tools()` — NOT `langchain-mcp-adapters` on the run's MCP servers. The adapters call
  the servers directly, which skips the runner's permission request, the journal and the turn count.
  `tools.call` is the same call with all three, and `as_langchain_tool` below wraps it;
- the deliverable: `ctx.write_output` asks the guard before it writes.
"""

from __future__ import annotations

from typing import Any, TypedDict

from choregos_agent_sdk import Context, LlmError, Result, ToolError, agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    ticket: str
    related: str
    reading: str


def as_langchain_tool(ctx: Context, tools: Any, name: str) -> StructuredTool:
    """A LangChain tool whose every call goes through the SDK's permission-checked `tools.call`."""

    async def call(**arguments: Any) -> str:
        return str((await tools.call(name, arguments)).text)

    return StructuredTool.from_function(coroutine=call, name=name, description=f"The run's `{name}` tool.")


def build_graph(ctx: Context, tools: Any) -> Any:
    async def lookup(state: State) -> State:
        if not await tools.has("knowledge_search"):
            return {"related": ""}
        search = as_langchain_tool(ctx, tools, "knowledge_search")
        try:
            return {"related": str(await search.ainvoke({"query": state["ticket"][:200]}))[:1500]}
        except ToolError as problem:
            await ctx.say(f"knowledge search failed: {problem}")
            return {"related": ""}

    async def read(state: State) -> State:
        if not ctx.llm.available:
            return {"reading": ""}
        model = ChatOpenAI(model=ctx.llm.model, base_url=ctx.llm.base_url, api_key=ctx.llm.api_key)
        try:
            answer = await model.ainvoke(
                [
                    SystemMessage("You triage tickets. Answer in two sentences."),
                    HumanMessage(state["ticket"][:4000]),
                ]
            )
        except Exception as problem:  # the gateway refused or is down: the rule below stands alone
            await ctx.say(f"no model reading: {type(problem).__name__}")
            return {"reading": ""}
        return {"reading": str(answer.content)}

    async def deliver(state: State) -> State:
        lines = ["# Triage", "", state.get("reading") or "(no model reading)", ""]
        if state.get("related"):
            lines += ["## Related", "", state["related"], ""]
        await ctx.write_output("triage.md", "\n".join(lines))
        return {}

    graph = StateGraph(State)
    graph.add_node("lookup", lookup)
    graph.add_node("read", read)
    graph.add_node("deliver", deliver)
    graph.add_edge(START, "lookup")
    graph.add_edge("lookup", "read")
    graph.add_edge("read", "deliver")
    graph.add_edge("deliver", END)
    return graph.compile()


@agent(name="langgraph-triager")
async def run(ctx: Context) -> Result:
    tools = await ctx.tools()
    final = await build_graph(ctx, tools).ainvoke({"ticket": ctx.task})
    used = bool(final.get("reading"))
    return Result(
        summary="Triaged with a model reading" if used else "Triaged without a model reading",
        facts={"model_used": used},
    )


__all__ = ["LlmError", "build_graph", "run"]
