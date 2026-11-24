"""
Aggregates TokenUsage records into a cost report, and writes it to disk
(local demo) or S3 (daily_cost_report Lambda). Kept storage-agnostic -
callers decide where the JSON goes.
"""
import json
from datetime import date

from product_intelligence.finops.usage import TokenUsage


class CostReportBuilder:
    def __init__(self):
        self._records: list[dict] = []

    def add(self, usage: TokenUsage, agent_name: str):
        breakdown = usage.cost_breakdown()
        breakdown["agent_name"] = agent_name
        self._records.append(breakdown)

    def build(self, report_date: str | None = None) -> dict:
        report_date = report_date or date.today().isoformat()
        total_input_tokens = sum(r["input_tokens"] for r in self._records)
        total_output_tokens = sum(r["output_tokens"] for r in self._records)
        total_cost = sum(r["estimated_total_cost"] for r in self._records)

        by_agent: dict[str, float] = {}
        for r in self._records:
            by_agent[r["agent_name"]] = round(
                by_agent.get(r["agent_name"], 0) + r["estimated_total_cost"], 6
            )

        return {
            "date": report_date,
            "invocation_count": len(self._records),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_estimated_cost_usd": round(total_cost, 6),
            "cost_by_agent": by_agent,
            "records": self._records,
        }

    def write(self, path: str, report_date: str | None = None) -> dict:
        report = self.build(report_date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        return report
