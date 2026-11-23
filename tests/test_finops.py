import pytest

from product_intelligence.finops.usage import TokenUsage
from product_intelligence.finops.report import CostReportBuilder


def test_known_tokens_and_price_produce_expected_cost():
    usage = TokenUsage(
        model_id="anthropic.claude-haiku-4-5-20251001-v1:0",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
    )
    breakdown = usage.cost_breakdown()
    assert breakdown["estimated_input_cost"] == 1.00
    assert breakdown["estimated_output_cost"] == 5.00
    assert breakdown["estimated_total_cost"] == 6.00


def test_unknown_model_raises_clear_error():
    usage = TokenUsage(model_id="not-a-real-model", input_tokens=100, output_tokens=100)
    with pytest.raises(KeyError):
        usage.cost_breakdown()


def test_cost_report_aggregates_across_agents():
    builder = CostReportBuilder()
    builder.add(TokenUsage("anthropic.claude-haiku-4-5-20251001-v1:0", 500_000, 100_000), agent_name="review_intelligence")
    builder.add(TokenUsage("anthropic.claude-haiku-4-5-20251001-v1:0", 200_000, 50_000), agent_name="recommendation")
    report = builder.build(report_date="2026-08-04")

    assert report["invocation_count"] == 2
    assert report["total_input_tokens"] == 700_000
    assert "review_intelligence" in report["cost_by_agent"]
    assert "recommendation" in report["cost_by_agent"]
    assert report["total_estimated_cost_usd"] == round(
        report["cost_by_agent"]["review_intelligence"] + report["cost_by_agent"]["recommendation"], 6
    )
