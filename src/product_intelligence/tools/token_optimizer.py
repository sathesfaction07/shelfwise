"""
Token estimation and reduction tracking (spec sections 18-19).

Important distinction the spec insists on: anything computed here is an
ESTIMATE, never an "exact token count", unless it comes from the real
model invocation response or CloudWatch. We use a simple heuristic
(chars / 4) as a stand-in for a real tokenizer, which is accurate enough
to reason about relative reduction (raw vs optimized) even if the
absolute number differs slightly from the model's real tokenizer.
"""
from dataclasses import asdict, dataclass


def estimate_token_count(text: str) -> int:
    """Returns an ESTIMATED token count (chars / 4 heuristic), not an exact count."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass
class TokenReduction:
    raw_estimate: int
    optimized_estimate: int

    @property
    def reduction_percent(self) -> float:
        if self.raw_estimate == 0:
            return 0.0
        return round((self.raw_estimate - self.optimized_estimate) / self.raw_estimate * 100, 1)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["reduction_percent"] = self.reduction_percent
        return d


def measure_reduction(raw_reviews: list[dict], optimized_sample: dict) -> TokenReduction:
    """
    raw_reviews: the full, unfiltered review set that WOULD have been sent
                 to the LLM without any optimization.
    optimized_sample: the {"positive": [...], "negative": [...]} sample
                 actually used to build the LLM prompt (see
                 analysis.reviews.sample_representative).

    Both sides are reduced to the SAME representation (title + text only)
    so the comparison is apples-to-apples - it measures the effect of
    filtering/dedup/sampling, not the overhead of Python dict formatting.
    """
    raw_text = " ".join(f"{r.get('title', '')} {r.get('text', '')}" for r in raw_reviews)

    sampled_reviews = optimized_sample.get("positive", []) + optimized_sample.get("negative", [])
    optimized_text = " ".join(f"{r.get('title', '')} {r.get('text', '')}" for r in sampled_reviews)

    return TokenReduction(
        raw_estimate=estimate_token_count(raw_text),
        optimized_estimate=estimate_token_count(optimized_text),
    )
