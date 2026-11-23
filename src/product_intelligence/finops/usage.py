"""
Per-invocation token usage and cost calculation (spec section 38).
"""
from dataclasses import dataclass

from product_intelligence.finops.pricing import get_model_pricing


@dataclass
class TokenUsage:
    model_id: str
    input_tokens: int
    output_tokens: int

    def cost_breakdown(self) -> dict:
        pricing = get_model_pricing(self.model_id)
        input_cost = self.input_tokens / 1_000_000 * pricing["input_price_per_million"]
        output_cost = self.output_tokens / 1_000_000 * pricing["output_price_per_million"]
        total_cost = input_cost + output_cost
        return {
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_input_cost": round(input_cost, 6),
            "estimated_output_cost": round(output_cost, 6),
            "estimated_total_cost": round(total_cost, 6),
        }
