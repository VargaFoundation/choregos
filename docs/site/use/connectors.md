---
title: Connectors
description: Connect a project to its tracker, repository and deployment, and the organisation to its directory, documents, MCP servers and notifications.
sidebar_position: 6
---

# Connectors

A connector is how Choregos talks to a system outside it: the tracker where requests live, the
repository agents change, the tool that deploys, the directory where accounts are created. This page
shows where each kind is set, how to tell that it works, and what to do when it does not.

**What you will be able to do**

- See which connectors a project needs, and why.
- Connect the tracker, the repository and the deployment of a project.
- Declare the organisation's own connectors: a directory, a document store, an MCP server.
- Read a connector's state and test it.

## Before you start

- A project's connectors: **Project owner** on the project.
- The organisation's connectors: **Organisation admin**.
- The address of the system, and a credential for it: a token, an App, a service account. Choregos
  never needs a broad personal token.

## Two levels, three pages

| Page | What is there | Who |
| :-- | :-- | :-- |
| **Settings › Connections**, in a project | The project's delivery tools: the work tracker, the source code, CI pipelines, the deployment (CD), the agent runtime, the model gateway, memory; and **Notifications** | Project owner |
| **Admin › Installed connectors** | The organisation's business systems and MCP servers: a directory, a device fleet, a document store, a web search, any MCP server, and which projects may use each | Organisation admin |
| **Connector types**, in the library | The catalogue: every kind of system Choregos can connect to, and what each one can do | Everyone |

A project needs only what its workflows use. A workflow that opens pull requests needs a source code
connector; a release train needs a deployment connector; an HR workflow with no repository needs
neither. The **Connections** page puts first what is required and missing, and each card says **Why it
is needed**.

## Connect a project's tools

1. Open the project's **Settings › Connections**.
2. On a card, press **Configure** (or **Edit** once it is set). A drawer opens with the form of that
   connector type.
3. Fill in the settings. Type a secret once: it is sealed, never shown again, and can only be replaced.
   Or give a reference to a secret your platform team keeps in its vault.
4. Save. The connector is **tested when it is saved**, and again when you press **Test**.

### What each card says

| State | Meaning |
| :-- | :-- |
| **Connected** | The last test passed; the card says when |
| **Failing** | The last test failed; the card shows the error |
| **Degraded** | It answers, but not fully |
| **Not tested yet** | Saved before tests ran on save; press **Test** |
| **Demo stand-in** | A simulation: nothing real happens. Good for a trial, not for real work |
| **Provided by the platform** | Your instance supplies it (the agent runtime, the model gateway, memory); nothing to configure |
| **Not connected** | Required by a workflow, and nothing provides it |

A failing test does not refuse the save: the settings are kept, and the card says why it fails.

### GitHub

Repositories on GitHub are reached through the Choregos GitHub App, never a personal token. When the
App is not installed on the repository's owner, the **Source code** card says so: press **Install on
GitHub**, choose the repository on GitHub, and you come back to Choregos. The installation is found on
its own; there is no number to copy.

For the platform to merge, protect the default branch with required status checks but no required
review, and set the connector's merge queue option to match the repository.

### Notifications

**Settings › Connections › Notifications** holds the Slack channel and the e-mail addresses the project
notifies: a question waiting, a release departing, a finding of high severity.

## Declare an organisation connector

1. Open **Admin › Installed connectors**.
2. Pick a type in **Add from the library**, or fill in **Declare a connector**: its **Name**, its
   **Type**, its **Secrets and settings**.
3. Under **Available to**, choose **Every project** or **Selected projects**.
4. For each write operation, you may name an **Approver group**: the people who must approve, as a
   governed action, every write an agent proposes through it. A read needs no approval.

MCP servers are declared here too, from **MCP servers** in the library: an official server, one of
your organisation's, or one served by your MCP gateway. Agents then reach them through the platform,
which holds the credential. See [Tools](../build/tools.md) and [MCP](../integrate/mcp.md).

## The network

Choregos calls an outside system from its own servers, and only the hosts your organisation has opened.
When you add a connector, an MCP server or a knowledge source whose host is not open, a **network
access** window lists each host and whether it is allowed. An organisation admin presses **Allow and
continue**; anyone else presses **Ask an administrator**, and the request waits in the admins'
**Inbox**. **Admin › Network** shows the organisation's network in one page.

## How to connect a given system

Each connector type has a step-by-step page: what to create on the other side, the permissions to give,
and the errors you may meet. For example: [GitHub](../../procedures/connector.scm.github.md),
[Jira](../../procedures/connector.tracker.jira.md), [Argo CD](../../procedures/connector.cd.argocd.md),
[Slack](../../procedures/connector.notify.slack.md),
[Confluence](../../procedures/connector.documents.confluence.md),
[Microsoft Entra ID](../../procedures/connector.identity.entra.md). The [full list](../../procedures/README.md)
is generated from the platform's own procedures.

## What can go wrong

| Symptom | What to do |
| :-- | :-- |
| A card reads **Failing** | Read the error on the card. A `401` or `403` is the credential or its permissions; a timeout is often the network |
| A card reads **Not connected** | A workflow needs it: press **Configure**, or ask an organisation admin when the readiness list says so |
| "The Choregos App is not installed" on the source code card | Press **Install on GitHub**, then come back |
| A readiness line still says a connector is stale | Its last passing test is older than a week; press **Test** |
| Nothing starts when an issue is labelled | Check the App's recent webhook deliveries on GitHub: the address must be your instance's, and reachable |

## Go further

- [Projects and members](projects-and-members.md): the readiness list.
- [Knowledge](knowledge.md): document sources that sync.
- [Connectors](../../usage.md#connectors), in depth.
