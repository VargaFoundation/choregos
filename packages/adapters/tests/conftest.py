# SPDX-License-Identifier: Apache-2.0
import os

# The egress allowlist (S39-22) only warns in the adapters' suites: the tests that prove the guard
# ask for `enforce` themselves.
os.environ.setdefault("CHOREGOS_EGRESS_MODE", "warn")
