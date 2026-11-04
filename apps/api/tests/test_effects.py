"""The core effects: `check` and its alias `verifier` (S39-11a)."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.parametrize("name", ["check", "verifier"])
async def test_check_effect_and_verifier_alias(name: str) -> None:
    from choregos_api.effects import EffectContext, EffectRefused, declared_effects, effect, effect_policy

    assert name in declared_effects()
    assert effect_policy(name) == "allowed"
    run = effect(name)
    ctx: Any = EffectContext(None, None, None)  # type: ignore[arg-type]
    assert (await run(ctx, {"condition": "True", "reason": "the account"}))["reason"] == "the account"
    # `motif` is the old name of `reason`: it is still read, and still answered.
    old = await run(ctx, {"condition": "true", "motif": "the badge"})
    assert old["reason"] == "the badge" and old["motif"] == "the badge"
    with pytest.raises(EffectRefused, match="check failed: the laptop"):
        await run(ctx, {"condition": "False", "reason": "the laptop"})
    with pytest.raises(EffectRefused, match="check failed: the laptop"):
        await run(ctx, {"condition": "False", "motif": "the laptop"})
