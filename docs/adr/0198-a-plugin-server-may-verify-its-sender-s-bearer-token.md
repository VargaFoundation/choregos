# 0198 — A plugin server may verify its sender's bearer token

- **Status**: proposed, 2026-11-20 (story S84-03)
- **Concerns**: `packages/contracts/schemas/plugin-server.schema.json` (`webhook.forward_authorization`),
  `apps/api/src/tranibor_api/routers/webhooks.py` (`plugin_webhook`)

## Context

A plugin server that is a tracker receives its system's deliveries through the platform: the webhook
route forwards the headers and the body to the server's `tracker_parse_event`, which says whether the
delivery is its own and which items changed (plan S43, Decision 3). The route drops `Authorization` and
`Cookie`: they are the caller's credentials, and a server has no business with them.

Some senders sign every delivery with a bearer token in `Authorization`: a bot service posts each
activity with a JWT its receiver must verify (issuer, audience, signing keys). Without that header the
server cannot tell a real delivery from a forged one, and the only alternative — a shared secret in
another header — is not what such a sender can send.

## Decision

1. A server declaration's `webhook` gains `forward_authorization` (boolean, `false` by default).
2. When it is `true`, the delivery's `Authorization` header reaches that server's `tracker_parse_event`
   with the other headers; `Cookie` still never does. A server that does not declare it never sees the
   header, even when several servers answer the same URL.
3. The verification is the server's, as for any delivery: the platform trusts nothing of it but the
   keys the server returns, and re-reads each item.
4. The platform neither logs nor stores the header: it is passed in the call and dropped.

## Consequences

- A bot-style sender's deliveries can be verified where its keys are known, by the server.
- The declaration is reviewed with the plugin: an administrator installing it sees that its webhook
  receives its sender's credentials.
- A server that echoes its arguments in an error would put the token in the platform's log of that
  error: a server must not.

## Alternatives discarded

- **Verifying the token in the platform**: the platform would learn every sender's issuer and audience;
  verification belongs where the system's configuration is.
- **Forwarding `Authorization` to every server**: a delivery's credentials would reach a server that
  never asked for them.
