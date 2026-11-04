"""Cart model: lines, a total in cents."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

TAX_RATE = Decimal("0.20")


@dataclass(frozen=True)
class Line:
    """A cart line. Prices are in cents: no floats."""

    label: str
    unit_price_cents: int
    quantity: int = 1

    def total_cents(self) -> int:
        return self.unit_price_cents * self.quantity


@dataclass
class Cart:
    lines: list[Line] = field(default_factory=list)

    def add(self, line: Line) -> None:
        self.lines.append(line)

    def subtotal_cents(self) -> int:
        return sum(line.total_cents() for line in self.lines)


def total_with_tax(cart: Cart) -> int:
    """Total with tax, in cents."""
    net = Decimal(cart.subtotal_cents())
    gross = net * (Decimal(1) + TAX_RATE)
    return int(gross)
