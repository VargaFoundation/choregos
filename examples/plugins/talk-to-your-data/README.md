# Talk to your data

Reference journey for the MCP door between products: a person asks a question in natural language,
a Choregos agent queries [graal](https://github.com/DiametralGroup/graal) (catalogue, then read-only SQL)
under the person's own identity, and the answer cites the tables it read.

```
person --(console/MCP)--> Choregos assistant --(MCP, RFC 8693 token exchange, ADR 0135)--> graal MCP
                                                                     |
                                           graal-api checks the person's rights on every call
```

## Identity

The `graal` need is `identity: user_exchange` and `access: read`. Per call, Choregos exchanges the
person's token for a graal token (no fallback to a service identity: without an exchange the call is
refused with `identity_unavailable`). The connector's own OAuth client credentials (S70-01) are the
tenant identity and serve only operation discovery.

## Install

1. Register a `graal` MCP connector whose URL is `https://<graal>/t/<tenant>/mcp?profile=read`
   (the `profile=read` door exposes only read-only, non-destructive tools), with
   `identity_modes: [service, user_exchange]` and `token_exchange.audience` set to graal's client.
2. In graal's Keycloak, allow token exchange from Choregos's client to graal's client (an
   administrator gesture, not in this repository).
3. Install the plugin and bind the `graal` need to that connector.

Run the offline checks: `uv run pytest packages/core/tests/test_talk_to_your_data_plugin.py`.

Not proven here: a live exchange against a real Keycloak and a real graal.
