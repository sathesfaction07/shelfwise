"""
Daily LLM budget guard (ported from bedrock-review-intel).

A process-wide accumulator that tracks estimated LLM spend for the current
calendar day and blocks new Bedrock calls once the configured ceiling
(config.DAILY_COST_BUDGET_USD) is reached. Resets automatically at
day rollover.

This is an application-side estimate, not an AWS invoice value - the
CloudWatch EstimatedLLMCost alarm (infrastructure/template.yaml) is the
authoritative cloud-side guard.
"""
from datetime import date

from product_intelligence import config
from product_intelligence.observability.logging import log_event


class BudgetExceededError(Exception):
    """Raised before a Bedrock call when the daily cost ceiling is reached."""


class _DailyBudget:
    def __init__(self):
        self._day = date.today()
        self._total_cost_usd = 0.0
        self._calls = 0

    def _roll_if_new_day(self):
        today = date.today()
        if today != self._day:
            log_event("budget_day_rollover", previous_day=str(self._day),
                      previous_total_usd=round(self._total_cost_usd, 6))
            self._day = today
            self._total_cost_usd = 0.0
            self._calls = 0

    def check(self):
        """Raises BudgetExceededError if today's spend has hit the ceiling."""
        self._roll_if_new_day()
        limit = config.DAILY_COST_BUDGET_USD
        if self._total_cost_usd >= limit:
            raise BudgetExceededError(
                f"Daily LLM budget of ${limit:.2f} reached "
                f"(spent ~${self._total_cost_usd:.4f} across {self._calls} calls today). "
                f"Raise DAILY_COST_BUDGET_USD in .env to continue."
            )

    def record(self, cost_usd: float):
        self._roll_if_new_day()
        self._total_cost_usd += cost_usd
        self._calls += 1
        limit = config.DAILY_COST_BUDGET_USD
        utilization = (self._total_cost_usd / limit * 100) if limit > 0 else 0.0
        log_event(
            "budget_update",
            spent_today_usd=round(self._total_cost_usd, 6),
            daily_limit_usd=limit,
            utilization_percent=round(utilization, 2),
            calls_today=self._calls,
        )

    @property
    def spent_today_usd(self) -> float:
        self._roll_if_new_day()
        return self._total_cost_usd

    @property
    def calls_today(self) -> int:
        self._roll_if_new_day()
        return self._calls


# Process-wide singleton
daily_budget = _DailyBudget()
