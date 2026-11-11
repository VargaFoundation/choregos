---
title: Glossary
description: The words Choregos uses, in one line each.
sidebar_position: 5
---

# Glossary

| Word | Means |
| --- | --- |
| **A2A door** | the endpoint that serves workflows as skills of the A2A protocol to applications and other agents ([guide](../integrate/a2a.md)) |
| **Action** | a governed change in an external system (an account created, a pull request merged): proposed, approved when required, done once, compensable |
| **Actor** | who moves a work item: an agent, a person (`human`), the platform (`system`), a `job`, a `decision` model |
| **Agent** | an entry of the agent registry: instructions, a model or code, skills, tools, limits and budget, in versions ([Build an agent](../build/agents.md)) |
| **Application** | an account an application uses to call the A2A door under its own name, on one project; it never decides |
| **Artefact** | a document or file a step produced, stored with its digest |
| **Backend** | the coding agent that runs a step (`claude-code`, `codex`, `gemini-cli`, …) |
| **Budget** | what a run, a work item, a project or an agent may spend, in USD, enforced at the model gateway |
| **Connector** | Choregos's link to an external system: a tracker, a repository, CI, a directory, an MCP server |
| **Decision** | a person's choice at a human step (approve, reject, answer), taken in the console, never by a token |
| **Egress** | the network a run or the platform may reach; opened host by host ([Tools](../build/tools.md#network)) |
| **Gate** | a check a transition requires, read from evidence rather than from what an agent says ([list](workflow-yaml.md#gates)) |
| **Integrations page** | the console page that gives each client (assistants, CLI, REST, A2A) its exact setup |
| **MCP door** | the endpoint that serves Choregos to AI assistants over the Model Context Protocol ([guide](../integrate/mcp.md)) |
| **MCP server** | a server of tools an agent may call, added by the organisation and governed tool by tool |
| **Organisation** | the tenant: its members, projects, agents, skills and connectors, isolated from the others |
| **Plugin** | a versioned package of agents, skills, templates and servers ([guide](../build/plugins.md)) |
| **Policy** | a project's rules: budgets, scope, sandbox, network; it can only tighten the organisation's ceiling |
| **Project** | where work happens: its workflows, connectors, members and policy; written `org:slug` |
| **Release train** | scheduled departures that ship merged work to an environment, with a person's approval |
| **Run** | one execution of an agent or a job for one step of a work item, with its journal and its cost |
| **Skill** | a folder an agent carries: a `SKILL.md` saying when to use it, and its files ([guide](../build/skills.md)) |
| **State** | where a work item stands in its workflow |
| **Token** | a secret that acts as a person or an application, with a scope: `*`, `mcp:read`, `mcp:write`, `a2a` |
| **Transition** | a move from one state to another, by an actor, under gates |
| **Trigger** | a URL another system POSTs JSON to, to open a work item ([guide](../integrate/triggers.md)) |
| **Work item** | one piece of work moving through a workflow: a request, a change, a ticket |
| **Workflow** | the state machine a work item follows, written in YAML ([reference](workflow-yaml.md)) |
