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
3. `pause`, `resume` and `stop` may be honoured only for tracker identities an organisation administrator
   linked by their stable provider id, on authenticated deliveries (S47-14).
4. Until then every command is answered with a console link (the inbox for `approve`, `reject`, `answer`,
   `scope`; the work item page for `pause`, `resume`, `stop`), audited as `tracker.command.redirected`,
   and no command reaches the interpreter. The reply never starts with the command, so it is not read as
   one.
5. A reply that cannot be posted (a connector that cannot be built, a tracker that refuses) never fails
   the delivery. A webhook event for a work item whose interpreter has ended is logged and kept: its
   dedupe row stands and a merge still boards the train.
6. Unsigned GitHub and Tekton webhooks are accepted only where `Settings.deployed` is false.

## Consequences

- No workflow signal carries a tracker command, so a forged comment cannot approve, reject or stop.
- A person who types a command gets an answer that tells them where to act.
- Honouring `pause`, `resume` and `stop` needs the identity link and authenticated deliveries first.
