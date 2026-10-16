# SPDX-License-Identifier: Apache-2.0
"""`choregos-egress`: the per-run egress proxy (ADR 0070).

It runs from the API image (`python -m choregos_api.egress`), holds no secret and no database
access: a runner presents its run id and run token as proxy credentials, and the proxy asks the API
(`GET /internal/runs/{id}/network`) what THAT run may reach, then lets a tunnel through or refuses.
"""
