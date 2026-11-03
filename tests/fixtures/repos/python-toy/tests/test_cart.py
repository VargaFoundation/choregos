"""Tests of the toy cart."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cart import Cart, Line, total_with_tax  # noqa: E402


def sample_cart() -> Cart:
    cart = Cart()
    cart.add(Line("coffee", 349, 2))
    cart.add(Line("filter", 199))
    return cart


def test_subtotal() -> None:
    assert sample_cart().subtotal_cents() == 897


def test_total_with_tax_rounded_to_the_cent() -> None:
    # 897 × 1.20 = 1076.4 → 1076
    assert total_with_tax(sample_cart()) == 1076


def test_empty_cart() -> None:
    assert total_with_tax(Cart()) == 0
