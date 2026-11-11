# 0145 — The egress proxy reports with a key the agent does not hold

- **Status**: accepted, 2026-11-11 — contract S44-06a, implementation S44-06. Amends
  [ADR 0115](0115-the-agent-never-holds-the-run-token.md) (its first consequence)
- **Concerns**: `POST /internal/runs/{id}/network/decisions` (`postRunNetworkDecisions`, now `runToken` or
  `egressToken` + `egressReportKey`), `choregos_api.deps`, `choregos_api.egress.report.HttpDecisionSink`,
  the `egress-report-key` key of `global.apiSecretRef`

## Context

ADR 0115 took the run token out of the agent's environment: the proxy variables carry an egress token,
which opens the run's network routes only. One of those routes is the proxy's report,
`postRunNetworkDecisions`, whose counters are cumulative totals that each post replaces. The agent
inherits the egress token, so it can post its own run's totals: erase the hosts it reached, or add
some it did not. The audit of what an agent reached was as trustworthy as the proxy's next flush.

Production has carried the egress token since 0.21.2; on 2026-11-10 its API logs show the proxy
reading networks and posting decisions with it, and nothing else.

## Decision

1. **The proxy has a key of its own.** A random string, the `egress-report-key` key of the API's
   secret (`global.apiSecretRef`), given to the API and to the egress proxy as
   `CHOREGOS_EGRESS_REPORT_KEY`, and to nothing else: a run's pod never receives it.
2. **A report made with the egress token carries the key** in `X-Choregos-Egress-Key`. The API compares
   it in constant time and answers 403 when it is missing or wrong. The egress token still says which
   run the report is for, and is still checked: the key alone opens nothing.
3. **Reading the run's network does not need the key.** What an agent may reach is no secret from that
   agent, and the proxy needs only the egress token to learn it.
4. **A deployment without a key** accepts the egress token alone, as before, and the API says so once
   at start-up (`egress.report_key_missing`). The chart's development secrets draw one; an operator
   adds the key to the API's secret.

## Consequences

- An agent can no longer rewrite its run's network counters. What it can still do is read its run's
  network policy, as before.
- One more secret for an operator to hold, rotated by replacing it in the API's secret and restarting
  the API and the proxy together; a report in between is refused, logged, and posted again at the next
  flush (the counters are totals, nothing is lost).
- The runner's own run token still opens the route; the runner holds it, the agent does not.

## Alternatives rejected

- **A Kubernetes service-account token for the proxy, checked by the API** (TokenReview, or the
  cluster's JWKS): no secret to provision, but cluster-scoped RBAC for the API, and nothing for the
  compose or ACA deployments, which would need a shared key anyway.
- **The API pulls the counters from the proxy**: the API would have to find and call every proxy
  replica, and a replica that restarts loses what was not yet pulled.
- **Derive the key from the run-token signing key**: the proxy would still need it delivered, and
  giving the proxy the signing key would let it mint run tokens.
