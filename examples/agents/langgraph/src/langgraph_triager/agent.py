# SPDX-License-Identifier: Apache-2.0
"""The ticket triager as a LangGraph graph.

Three nodes: `lookup` (the knowledge search), `read` (the model) and `deliver` (the deliverable). The
graph is ordinary LangGraph; what the platform governs is wired in two lines each:

- the model: `ChatOpenAI(..., http_async_client=ctx.llm.http_client())` — the run's gateway and the
  run's own key, and every request counts as a turn against `max_turns`;
- the tools: `langchain.tools(await ctx.tools())` — NOT `langchain-mcp-adapters` on the run's MCP
  servers, which would call them directly and skip the runner's permission request.
"""

from __future__ import annotations

from typing import Any, TypedDict

from choregos_agent_sdk import Context, Result, ToolError, agent
from choregos_agent_sdk.errors import sdk_error_in
from choregos_agent_sdk.integrations import langchain as bridge
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph


class State(TypedDict, total=False):
    ticket: str
    related: str
    reading: str


async def build_graph(ctx: Context, tools: Any) -> Any:
    by_name = {tool.name: tool for tool in await bridge.tools(tools)}

    async def lookup(state: State) -> State:
        search = by_name.get("knowledge_search")
        if search is None:
            return {"related": ""}
        try:
            return {"related": str(await search.ainvoke({"query": state["ticket"][:200]}))[:1500]}
        except ToolError as problem:
            await ctx.say(f"knowledge search failed: {problem}")
            return {"related": ""}

    async def read(state: State) -> State:
        if not ctx.llm.available:
            return {"reading": ""}
        model = ChatOpenAI(
            model=ctx.llm.model,
            base_url=ctx.llm.base_url,
            api_key=ctx.llm.api_key,
            http_async_client=ctx.llm.http_client(),
            max_retries=0,
        )
        try:
            answer = await model.ainvoke(
                [
                    SystemMessage("You triage tickets. Answer in two sentences."),
                    HumanMessage(state["ticket"][:4000]),
                ]
            )
        except Exception as problem:  # the gateway refused or is down: the rule below stands alone
            if sdk_error_in(problem) is not None:
                raise  # the turn limit or the guard: the step ends, it is not a missing reading
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
    final = await (await build_graph(ctx, tools)).ainvoke({"ticket": ctx.task})
    used = bool(final.get("reading"))
    return Result(
        summary="Triaged with a model reading" if used else "Triaged without a model reading",
        facts={"model_used": used},
    )


__all__ = ["build_graph", "run"]
