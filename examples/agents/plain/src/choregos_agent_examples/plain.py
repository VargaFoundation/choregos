# SPDX-License-Identifier: Apache-2.0
"""A ticket triage agent in plain Python.

It reads the ticket, looks the knowledge bases up when the run offers the tool, asks the model for
a one-paragraph reading (and falls back on a rule when the run has no gateway), and delivers
`triage.md`. Every step the platform governs goes through the SDK: the tool call and the write ask
the runner's guard first, the model call counts as a turn.

Run it by hand against a runner, or see `README.md` for the image.
"""

from __future__ import annotations

from choregos_agent_sdk import Context, LlmError, Result, ToolError, agent

URGENT = ("outage", "down", "data loss", "breach", "security", "cannot log in", "payment")
SOON = ("error", "bug", "fails", "slow", "wrong", "broken")


def priority_of(ticket: str) -> str:
    """The rule the agent falls back on: P1 for what stops people, P2 for what hurts, else P3."""
    lowered = ticket.lower()
    if any(word in lowered for word in URGENT):
        return "P1"
    if any(word in lowered for word in SOON):
        return "P2"
    return "P3"


@agent(name="ticket-triager")
async def run(ctx: Context) -> Result:
    ticket = ctx.task
    priority = priority_of(ticket)

    # One governed tool call, when the run offers the knowledge search (ADR 0051).
    related = ""
    tools = await ctx.tools()
    if await tools.has("knowledge_search"):
        found = await tools.call("knowledge_search", {"query": ticket[:200]})
        related = found.text[:1500]

    # One model call through the run's gateway; without one, the rule above stands alone.
    reading = ""
    if ctx.llm.available:
        try:
            reply = await ctx.llm.chat(
                [
                    {"role": "system", "content": "You triage tickets. Answer in two sentences."},
                    {"role": "user", "content": ticket[:4000]},
                ]
            )
            reading = reply.text.strip()
        except LlmError as problem:
            await ctx.say(f"no model reading: {problem}")

    lines = [f"# Triage: {priority}", ""]
    if reading:
        lines += [reading, ""]
    if related:
        lines += ["## Related", "", related, ""]
    await ctx.write_output("triage.md", "\n".join(lines))
    return Result(
        summary=f"Triaged as {priority}" + (" with a model reading" if reading else " by rule"),
        facts={"priority": priority, "model_used": bool(reading)},
        outputs={"priority": priority},
    )


# `ToolError` is what a tool that answers with an error raises; this agent lets it end the step.
__all__ = ["ToolError", "priority_of", "run"]

if __name__ == "__main__":
    run.serve()
