# 0102 — A tracker comment carries no decision

- **Status**: accepted, 2026-11-03
- **Concerns**: `routers/webhooks.py` (`_dispatch`, `_answer_tracker_command`), `temporal.py`
  (`deliver_inbound`), `Settings.deployed`; builds on [ADR 0100](0100-console-sign-in-binds-to-the-provider-subject.md)

## Context

A `/choregos approve` comment on a GitHub, Jira or GitLab ticket became a `human.decision` inbound event
and was signalled to the work item's interpreter, which dropped it. Nothing was posted back, so the
author believed it had been taken or ignored. Two other failures sat on the same path: a gone interpreter
made the delivery fail and roll back its dedupe row (the next retry processed the same delivery again),
and a project whose tracker connector could not be built failed the delivery with a 502. Unsigned GitHub
and Tekton webhooks were accepted unless `env` was staging or prod, although the default `env` is dev even
behind a TLS ingress.

## Decision

1. **A tracker login is not a Choregos principal.** The login on a comment is a name the tracker gives,
   not an identity Choregos authenticated.
2. **Decisions never come from comments.** Step-up, group verdicts and gesture controls live in the
   console, after a fresh sign-in.
3. `pause`, `resume` and `stop` are honoured (implemented in S47-14) only for a GitHub account that an
   organisation administrator linked to a member by its stable numeric user id, in a delivery verified
   by its HMAC. They run with that member's own rights (ITEM_CONTROL), are audited on the channel
   `tracker` and confirmed on the ticket. Jira and GitLab are not honoured: a display name or a username
   is not a stable id, and their deliveries carry only the shared secret (also accepted in the Tekton
   sink URL). The link is asserted by the administrator, not proven by the member.
4. Every other command is answered with a console link (the inbox for `approve`, `reject`, `answer`,
   `scope`; the work item page for `pause`, `resume`, `stop`), audited as `tracker.command.redirected`,
   and no decision reaches the interpreter. The reply never starts with the command, so it is not read as
   one.
5. A reply that cannot be posted (a connector that cannot be built, a tracker that refuses) never fails
   the delivery. A webhook event for a work item whose interpreter has ended is logged and kept: its
   dedupe row stands and a merge still boards the train.
6. Unsigned GitHub and Tekton webhooks are accepted only where `Settings.deployed` is false.

## Card moves

A status change on a tracker board (GitHub Projects v2, Jira) is not a decision either. It is logged and
never delivered to the workflow: the webhook skips every `tracker.item.moved` event, before any audit row
or comment. Choregos's own moves (the mirror of a work item's state onto its card) come back as the same
event and are ignored the same way. A person moves a work item from the console. The interpreter branch
that matches `tracker.status` stays, only for histories recorded before this change.

One move still counts, and not as a decision: a move into a project start rule's state (S56-03) that
births an item the project did not hold. The item is created and its workflow started, the event is kept
as `created_work_item`, and the move itself is still delivered to no workflow.

## Consequences

- No workflow signal carries a tracker command, so a forged comment cannot approve, reject or stop.
- A person who types a command gets an answer that tells them where to act.
- Honouring `pause`, `resume` and `stop` needs the identity link and an HMAC-verified GitHub delivery.
