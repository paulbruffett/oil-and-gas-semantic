"""Assessor-panel scaffolding (pairwise, per-judge + spread) and reported effort metering (#9)."""

from __future__ import annotations

import pytest

from oag_harness.effort import (
    NOTIONAL_PRICING,
    EffortRecord,
    ModelPrice,
    TokenUsage,
    notional_cost_usd,
)
from oag_harness.panel import (
    TIE,
    PairwiseVote,
    PanelEntry,
    aggregate_panel,
    run_panel,
)

# --- assessor panel -----------------------------------------------------------------------------


class QualityJudge:
    """Deterministic judge: prefers the higher `artifact` (a stand-in quality score); ties equal."""

    def __init__(self, name: str, bias_toward: str | None = None):
        self.name = name
        self._bias = bias_toward  # if set, always votes for this implementation (models self-pref)

    def compare(self, dimension: str, left: PanelEntry, right: PanelEntry) -> str:
        if self._bias in (left.implementation, right.implementation):
            return self._bias
        if left.artifact == right.artifact:
            return TIE
        return left.implementation if left.artifact > right.artifact else right.implementation


def test_run_panel_covers_every_pair_and_judge():
    subs = [PanelEntry("A", 3), PanelEntry("B", 1), PanelEntry("C", 2)]
    judges = [QualityJudge("j1"), QualityJudge("j2")]
    votes = run_panel(judges, "code-quality", subs)
    # 3 pairs x 2 judges.
    assert len(votes) == 6
    assert {v.judge for v in votes} == {"j1", "j2"}


def test_aggregate_win_rates_rank_by_quality():
    subs = [PanelEntry("A", 3), PanelEntry("B", 1), PanelEntry("C", 2)]
    scores = aggregate_panel(run_panel([QualityJudge("j1")], "code-quality", subs))
    by_impl = {s.implementation: s for s in scores}
    # A beats both -> 1.0; C beats B only -> 0.5; B loses both -> 0.0.
    assert by_impl["A"].per_judge["j1"] == 1.0
    assert by_impl["C"].per_judge["j1"] == 0.5
    assert by_impl["B"].per_judge["j1"] == 0.0


def test_spread_surfaces_a_biased_judge():
    subs = [PanelEntry("A", 1), PanelEntry("B", 3)]  # honestly B > A
    honest = QualityJudge("honest")
    biased = QualityJudge("biased", bias_toward="A")  # a judge always backing A (e.g. its own output)
    scores = aggregate_panel(run_panel([honest, biased], "code-quality", subs))
    a = next(s for s in scores if s.implementation == "A")
    # honest gives A 0.0, biased gives A 1.0 -> published spread is 1.0, disagreement made visible.
    assert a.per_judge == {"honest": 0.0, "biased": 1.0}
    assert a.spread == 1.0
    assert a.mean == 0.5


def test_ties_count_as_half():
    subs = [PanelEntry("A", 2), PanelEntry("B", 2)]
    scores = aggregate_panel(run_panel([QualityJudge("j1")], "docs", subs))
    assert all(s.per_judge["j1"] == 0.5 for s in scores)


def test_invalid_winner_is_a_protocol_error():
    bad = [PairwiseVote(judge="j", dimension="d", left="A", right="B", winner="C")]
    with pytest.raises(ValueError, match="not in"):
        aggregate_panel(bad)


# --- effort metering (reported, not scored) -----------------------------------------------------


def test_notional_cost_breaks_out_token_classes():
    usage = TokenUsage(input=1_000_000, output=1_000_000, cache_read=1_000_000, cache_creation=0)
    price = ModelPrice(input=3.0, output=15.0, cache_read=0.3, cache_creation=3.75)
    # 3 + 15 + 0.3 = 18.3 USD.
    assert notional_cost_usd(usage, price) == pytest.approx(18.3)


def test_effort_record_report_is_reported_not_scored():
    rec = EffortRecord(
        implementation="team-x",
        model="claude-sonnet-4",
        usage=TokenUsage(input=2_000_000, output=500_000, cache_read=10_000_000),
        wall_clock_s=3600.0,
        turns=42,
        human_interventions=3,
    )
    report = rec.report()
    assert report["scored"] is False
    assert report["priced"] is True
    assert report["tokens"]["cache_read"] == 10_000_000
    assert report["total_tokens"] == 12_500_000
    # 2*3 + 0.5*15 + 10*0.3 = 6 + 7.5 + 3 = 16.5 USD.
    assert report["notional_cost_usd"] == pytest.approx(16.5)


def test_unpriced_model_reports_zero_cost_and_flags_it():
    rec = EffortRecord(
        implementation="team-y", model="some-other-llm", usage=TokenUsage(input=1_000_000)
    )
    report = rec.report()
    assert report["priced"] is False
    assert report["notional_cost_usd"] == 0.0


def test_pricing_table_has_the_documented_models():
    assert {"claude-opus-4", "claude-sonnet-4", "claude-haiku-4"} <= set(NOTIONAL_PRICING)
