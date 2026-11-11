---
title: Assistants
description: Find the assistants your organisation gives you, talk to them, check their sources, confirm what they want to write, and keep control of what they remember and do for you.
sidebar_position: 8
---

# Assistants

An **assistant** is an agent you talk to. Your organisation installs it for a group of people, and you
find it in the console. It answers from the sources it was given, and says where each answer comes
from. At `acme`, the billing team gives its people a billing assistant that answers questions about
invoices.

**What you will be able to do**

- Find the assistants you may talk to, and start a conversation.
- Read the citations under an answer, and rate it.
- Confirm or refuse a write the assistant proposes.
- See, correct, add to and export what the assistant remembers about you.
- Connect your own accounts, so the assistant reads your data and nobody else's.
- Subscribe to a task, and decide whether it may read for you while you are away.

## Before you start

- You belong to one of the **audience groups** the assistant was installed for. If you do not, the
  assistant is not in your list. Ask an organisation administrator.
- Nothing else. You do not need a project role.

## Find an assistant and talk to it

1. Open **Assistants** in the sidebar. Each card shows the assistant's name, what it does and a badge:
   `ready`, `incomplete` or `suspended`.
2. Press **Converse** on a `ready` assistant. The conversation opens in your personal space: only you
   read it.
3. Type in **Message** and press **Send**. The answer streams in as it is written. Press **Cancel** to
   stop a turn; what the assistant already did stays in the journal.
4. To come back later, open **My conversations** (the page is titled **Conversations**). Each line shows
   the assistant, the number of turns, the cost and the last activity. Open one to continue it, or press
   **Delete** to remove it.

A conversation left alone for a while goes idle. Your next message reopens it with its history.

**What you should see:** the answer, then under it a badge for each document the assistant used, written
as the document's title and version.

### Citations

The assistant is asked to cite what it uses from its knowledge. The badges under an answer name the
documents. A figure that comes from a source should carry its date. If an answer has neither, ask the
assistant where it comes from, and treat it as unchecked.

### Thumbs and feedback

Under the last answer, press the thumb up (**Good answer**) or the thumb down (**Bad answer**). A text
box, **A comment (optional)**, opens; press **Send feedback**. A thumb down can become a test case for the
people who maintain the assistant, but it only does so after one of them has read it and decided. Your
comment does not enter a test on its own.

## Confirm a write

An assistant never writes to a source by itself. When it wants to, a card **The assistant wants to act**
appears, with the operation, a summary, the arguments, and, when the source can tell, **What would
change**.

1. Read the card.
2. Press **Confirm** to let it go ahead, or **Refuse**.

Only you, the person who owns the conversation, can decide. The write is made with your identity, and
the source decides whether you are allowed to make it. A proposal that nobody decides within 24 hours
expires.

## What the assistant remembers

From **Assistants**, press **Memory, accounts and tasks** on a card. The **Memory** tab shows one card
per memory space the assistant keeps about you.

- A fact the assistant proposes to remember shows a **proposed** badge. Press **Confirm** to keep it or
  **Reject** to drop it. The same card also appears in the conversation: **The assistant proposes to
  remember**.
- **Pin** keeps a fact in front of the assistant. **Unpin** undoes it. **Delete** removes one fact.
- **Add a fact** (or **Add a profile (JSON)** for a profile space) then **Add** writes something yourself.
  It is active at once. For a space kept per subject, name the subject in **Subject key** first.
- **Export** downloads one space as a file. **Export everything** at the top downloads all of them.
- **Forget all** empties one space, after a confirmation.
- **Erase everything about me**, under the cards, removes every conversation and every remembered fact
  this assistant holds about you, after a confirmation. This cannot be undone.

Each card says who may write to the space, under its name (`writes: agent_proposes`, for example, means
the assistant proposes and you decide).
## Connected accounts

Some sources belong to you: your bank, your mailbox, your brokerage. The assistant reads them only with
an account you connected.

1. Open the **Connected accounts** tab, or the answer's warning **An account is not connected** with its
   **Connect your** (source name) **account** button.
2. You are sent to the source's own page to give your consent. You come back to the console.
3. The account then shows **Connected**, the scopes you gave and when it was last used.

**Revoke** removes the connection at once, and Choregos forgets its secret. **Reconnect** appears when a
source stopped accepting the account. Until you connect, the assistant carries on without that source and
says so; it never reads it with someone else's rights. The same list is at `/me/connected-accounts`.

## Tasks, and the standing delegation

Besides conversations, an assistant may offer **tasks**: work it does for you in the background, like a
morning brief. The **Tasks** tab lists them. The **Inbox** tab holds what they delivered, and your
organisation may also send them by email.

1. Choose how it runs: **On demand**, or **On a schedule** with a cron and your **Time zone**.
2. Press **Subscribe**. **Run now** starts it once, and the result lands in your inbox.
3. **Pause**, **Resume** and **Unsubscribe** stop it as you wish.

### When a task reads for you while you are away

A task that reads your accounts when you are not there needs a **standing delegation**: your agreement,
for that task only.

1. Under the task, press **Review and agree**. The console lists the exact sources and operations it
   would read.
2. Press **I agree to these reads**.

A delegation is bounded:

- it reads only, never writes;
- it ends after 90 days unless your installation sets less, and you are reminded before; press **Agree
  again** to renew it;
- **Revoke the delegation** ends it at once;
- it is suspended on its own if you leave the audience or if the plugin changes what the task reads: you
  agree again.

:::caution Your organisation may not offer delegated tasks yet
The standing delegation is delivered, but its decision record
([ADR 0137](../../adr/0137-a-standing-read-only-delegation.md)) is still **proposed**. An organisation
should not offer delegated tasks until its works council has given its opinion and its data protection
impact assessment is done. If you see no **Review and agree** button, or your administrator tells you
the feature is off, that is why. Tasks that read only public sources, and tasks you run yourself, do
not need a delegation.
:::

## What can go wrong

| What you meet | What it means | What to do |
| --- | --- | --- |
| No assistant in the list | you are outside every audience | ask an organisation administrator to add your group |
| The badge says `incomplete` | a source or a setting is missing; **Converse** is disabled | tell the administrators who own the assistant |
| The badge says `suspended` | its plugin is uninstalled | same |
| **An account is not connected** | the assistant needs your account at a source | press the **Connect** button, then ask again |
| A write card is gone | nobody decided within 24 hours | ask again |
| The last run of a task was refused | it needed a delegation you have not given or that is suspended | **Review and agree**, or **Agree again** |
| A turn is refused before it starts | a budget is spent (the conversation's, yours for the month, or the installation's) | wait for the next month, or ask the owners |

## Go further

- Use the same assistant from claude.ai or Claude Code: [Connect an assistant to your own tools](../integrate/assistants.md).
- Documents an assistant may cite are a [knowledge base](knowledge.md) of its organisation.
- Write an assistant: [Ship an assistant in a plugin](../build/assistants.md).
