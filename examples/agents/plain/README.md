# Plain-Python example: a ticket triager

One async function on the `choregos-agent-sdk`: it reads the ticket, asks the knowledge search (one
governed tool call, when the run offers it), asks the model for a two-sentence reading (one counted
turn; a rule takes over when the run has no gateway) and delivers `triage.md`.

```python
@agent(name="ticket-triager")
async def run(ctx: Context) -> Result: ...
```

It passes the seven conformance checks in CI (`tests/conformance/backends/test_code_agents.py`).

## Run it in a platform

1. Build the image FROM the runner image (see the `Dockerfile` header), push it to your registry,
   and name it in the operator's configuration with its digest:
   `global.images.agents.ticket-triager: registry.example/agents/ticket-triager@sha256:...`
2. Register the agent (`POST /orgs/<org>/agents`):

```json
{
  "slug": "ticket-triager",
  "display_name": "Ticket triager",
  "spec": {
    "model": "profile:standard",
    "runtime": {
      "kind": "code",
      "image": "ticket-triager",
      "command": ["/opt/agents/plain/bin/python", "-m", "choregos_agent_examples.plain"]
    }
  }
}
```

3. Name it in a workflow (`k8s_job` executor, not `agent_sandbox`: the pod runs the agent's image):

```yaml
actors:
  triager: { type: agent, role: triage, agent: ticket-triager }
```

Check an image before using it: `make conformance-agent IMAGE=<ref> COMMAND='["/opt/agents/plain/bin/python","-m","choregos_agent_examples.plain"]'`.
