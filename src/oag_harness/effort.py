"""Effort metering: a **reported, not scored** signal (DESIGN.md §7, ADR 0015).

Effort-to-build is captured, never graded -- it contextualises the rubric scores without letting
"cheaper" masquerade as "better". The recipe (§7): report tokens **broken out**
(input / output / cacheRead / cacheCreation) and a **notional cost = tokens x public list price**
against **one pricing table**, plus wall-clock and turns/human-interventions. Thinking tokens are
billed inside ``output`` and aren't separately isolable in Claude Code; cache tokens are reported
separately because cache-hit rates differ across harnesses and skew cost.

The pricing table below is *notional* and **operator-maintained**: list prices move, so the operator
refreshes it at grading time and the published cost cites the table version used. Cost is a modelled
ROM (Max-plan runs are flat-rate), which is exactly why it is reported and not scored.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    """Broken-out token counts for one build (§7)."""

    input: int = 0
    output: int = 0  # includes thinking tokens (not separately isolable in Claude Code)
    cache_read: int = 0
    cache_creation: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_read + self.cache_creation


@dataclass(frozen=True)
class ModelPrice:
    """Public list price in USD per 1,000,000 tokens, by token class."""

    input: float
    output: float
    cache_read: float
    cache_creation: float


# One pricing table (the §7 requirement). NOTIONAL, operator-maintained list prices (USD / 1M tokens)
# -- refresh at grading time and cite `PRICING_TABLE_VERSION` in the published scorecard. Values are
# rough public-list ROMs, not a live quote; cost derived from them is reported, never scored.
PRICING_TABLE_VERSION = "2026-07-notional"
NOTIONAL_PRICING: dict[str, ModelPrice] = {
    "claude-opus-4": ModelPrice(input=15.0, output=75.0, cache_read=1.5, cache_creation=18.75),
    "claude-sonnet-4": ModelPrice(input=3.0, output=15.0, cache_read=0.3, cache_creation=3.75),
    "claude-haiku-4": ModelPrice(input=0.8, output=4.0, cache_read=0.08, cache_creation=1.0),
}

_PER_MILLION = 1_000_000.0


def notional_cost_usd(usage: TokenUsage, price: ModelPrice) -> float:
    """Modelled cost = Σ (tokens_class x price_class) / 1e6. A ROM, per the recipe -- reported only."""
    return (
        usage.input * price.input
        + usage.output * price.output
        + usage.cache_read * price.cache_read
        + usage.cache_creation * price.cache_creation
    ) / _PER_MILLION


@dataclass(frozen=True)
class EffortRecord:
    """The full reported effort signal for one implementation's build."""

    implementation: str
    model: str
    usage: TokenUsage
    wall_clock_s: float = 0.0
    turns: int = 0
    human_interventions: int = 0
    pricing: dict[str, ModelPrice] = field(default_factory=lambda: NOTIONAL_PRICING)
    pricing_table_version: str = PRICING_TABLE_VERSION

    @property
    def notional_cost_usd(self) -> float:
        """Cost against the record's pricing table; 0.0 if the model isn't priced (reported as such)."""
        price = self.pricing.get(self.model)
        return notional_cost_usd(self.usage, price) if price else 0.0

    def report(self) -> dict[str, Any]:
        """The published effort block: broken-out tokens + notional cost + wall-clock/turns."""
        return {
            "implementation": self.implementation,
            "model": self.model,
            "tokens": asdict(self.usage),
            "total_tokens": self.usage.total,
            "notional_cost_usd": round(self.notional_cost_usd, 4),
            "pricing_table_version": self.pricing_table_version,
            "priced": self.model in self.pricing,
            "wall_clock_s": self.wall_clock_s,
            "turns": self.turns,
            "human_interventions": self.human_interventions,
            "scored": False,  # effort is reported, never scored (ADR 0015)
        }
