"""The skill files the skill library tests and the agent scope tests create skills from."""

from __future__ import annotations

SKILL_MD = """---
name: procedure-onboarding
description: The arrival procedure of a new employee, step by step.
---

# Arrival procedure

1. Create the account in Entra.
"""

#: A valid skill folder: `SKILL.md` and one reference file.
SKILL_FILES = {"SKILL.md": SKILL_MD, "references/groups.md": "- all-staff\n- vpn\n"}
