"""The core effects: `check` and its alias `verifier` (S39-11a)."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.parametrize("name", ["check", "verifier"])
async def test_check_effect_and_verifier_alias(name: str) -> None:
    from choregos_api.effets import ContexteEffet, EffetRefuse, effet, effets_declares, politique_de_l_effet

    assert name in effets_declares()
    assert politique_de_l_effet(name) == "allowed"
    run = effet(name)
    ctx: Any = ContexteEffet(None, None, None)  # type: ignore[arg-type]
    assert (await run(ctx, {"condition": "True", "reason": "the account"}))["reason"] == "the account"
    # `motif` is the old name of `reason`: it is still read, and still answered.
    old = await run(ctx, {"condition": "true", "motif": "the badge"})
    assert old["reason"] == "the badge" and old["motif"] == "the badge"
    with pytest.raises(EffetRefuse, match="check failed: the laptop"):
        await run(ctx, {"condition": "False", "reason": "the laptop"})
    with pytest.raises(EffetRefuse, match="check failed: the laptop"):
        await run(ctx, {"condition": "False", "motif": "the laptop"})
