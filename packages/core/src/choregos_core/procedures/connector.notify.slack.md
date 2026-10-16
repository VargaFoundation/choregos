---
id: connector.notify.slack
title: Connect Slack for notifications and decisions
applies_to: {connector: notify/slack}
audience: project_owner
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Your Slack apps", url: "https://api.slack.com/apps"}
  - {title: "Sending messages using incoming webhooks", url: "https://api.slack.com/messaging/webhooks"}
  - {title: "chat:write scope", url: "https://api.slack.com/scopes/chat:write"}
egress: [slack.com, hooks.slack.com]
values: [public_url]
fields:
  channel: "the channel notifications go to, for example #choregos"
  public_url: "this platform's console address, used for the buttons' links"
  webhook_url: "an incoming webhook URL (simple mode)"
  bot_token: "a bot token (the mode that posts to any channel it is in)"
---

## Before you start

- A Slack workspace where you may create or install an app (otherwise an administrator does).
- The channel notifications go to.
- Choose a mode. **Incoming webhook**: simplest, fixed to one channel. **Bot token**: posts to any
  channel the bot is in, and can also be tested with `auth.test`.

## At Slack

1. `api.slack.com/apps`, **Create New App**, **From scratch**, pick the workspace.
2. Either:
   - **Incoming Webhooks**: turn it on, **Add New Webhook to Workspace**, pick the channel, copy the
     webhook URL; or
   - **OAuth & Permissions**: add the bot scope `chat:write` (and `chat:write.public` to post in
     channels the bot has not joined), **Install to Workspace**, copy the **Bot User OAuth Token**.
3. For a bot token, invite the bot to the channel: `/invite @<app name>`.

## Permissions

- Webhook mode needs nothing else: the webhook can post to the one channel you picked.
- Bot mode needs `chat:write`. Nothing else is read from the workspace.
- Do not enable Interactivity: Choregos exposes no Slack interactivity endpoint today, so a message's
  decision buttons do not post back. The button that opens the item in the console is a plain link and
  works; decisions are taken in the console.

## In Choregos

1. Project settings, Connectors, **Notify**, type **Slack** (or an organisation connector).
2. `channel`: for example `#choregos`.
3. `public_url`: this platform's console address, `{{public_url}}`. Messages carry a button that opens
   the item here.
4. Paste **one** of `webhook_url` or `bot_token` in its password field (sealed, shown once).

## Test it

Press **Test** on the connector. In bot mode it calls `auth.test` and shows the workspace; in webhook
mode it checks that a webhook URL is set. Then trigger a notification and look at the channel.

## When it fails

- `channel_not_found`: the channel name is wrong, or the bot cannot see a private channel.
- `not_in_channel`: invite the bot (`/invite @app`), or add `chat:write.public`.
- `invalid_auth` or `token_revoked`: the bot token is wrong or the app was reinstalled; paste the new one.
- A `404` or `no_service` from a webhook: the webhook was revoked; create a new one.
- A timeout: the egress allowlist must open `slack.com` (bot) or `hooks.slack.com` (webhook).
