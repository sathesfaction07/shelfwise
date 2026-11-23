"""
Thin wrapper around Bedrock's invoke_model for the two agents that
actually need an LLM (Review Intelligence, Recommendation). Discovery and
Price & Rating never import this module - they are pure Python/data-provider
calls (see spec section 3/9's LLM-vs-Python split).

boto3 is imported lazily so this module can be imported without AWS
credentials configured (needed for offline/mock code paths and tests).
"""
import json
import time

from product_intelligence import config
from product_intelligence.aws import client as aws_client
from product_intelligence.finops.usage import TokenUsage
from product_intelligence.finops.budget import daily_budget
from product_intelligence.observability.logging import log_event, log_error
from product_intelligence.observability.metrics import MetricsClient


def extract_json(text: str) -> dict:
    """
    Parses JSON from an LLM response, tolerating markdown code fences
    and leading/trailing prose (models often wrap JSON in ```json ... ```).
    Raises ValueError if no JSON object can be found.
    """
    text = text.strip()
    if text.startswith("```"):
        # strip opening fence (``` or ```json) and closing fence
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # fall back: grab the outermost {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise ValueError(f"No JSON object found in LLM response: {text[:120]!r}")



class LLMError(Exception):
    """Raised when a Bedrock invocation fails after retries."""


class LLMInvocationResult:
    def __init__(self, text: str, usage: TokenUsage, latency_ms: int):
        self.text = text
        self.usage = usage
        self.latency_ms = latency_ms


def invoke(
    prompt: str,
    system_prompt: str = "",
    model_id: str | None = None,
    max_tokens: int = 500,
    agent_name: str = "unknown",
    metrics_client: MetricsClient | None = None,
    max_retries: int = 2,
) -> LLMInvocationResult:
    """
    Calls Bedrock's invoke_model with the Anthropic Messages format, logs a
    structured event, and publishes CloudWatch metrics for tokens/cost.
    Raises LLMError if every retry attempt fails (spec section 61: agent
    timeout / LLM error must be handled gracefully by the caller).
    """
    # FinOps guard: refuse to spend if today's ceiling is already reached.
    # BudgetExceededError propagates to the caller (agents fall back to mock).
    daily_budget.check()

    from botocore.exceptions import ClientError

    model_id = model_id or config.MODEL_ID
    metrics_client = metrics_client or MetricsClient()
    client = aws_client("bedrock-runtime")

    # Nova models use a different request format than Anthropic models.
    is_nova = "nova" in model_id.lower() or "amazon" in model_id.lower()

    if is_nova:
        body = {
            "messages": [{"role": "user", "content": [{"text": (system_prompt + "\n\n" + prompt) if system_prompt else prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.1},
        }
    else:
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }

    last_error = None
    # Retrying a slow local generation queues more work behind the same model.
    if config.AWS_MODE == "floci":
        max_retries = 0
    for attempt in range(max_retries + 1):
        start = time.perf_counter()
        try:
            if config.AWS_MODE == "floci":
                request = {
                    "modelId": model_id,
                    "messages": [{"role": "user", "content": [{"text": prompt}]}],
                    "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.1},
                }
                if system_prompt:
                    request["system"] = [{"text": system_prompt}]
                payload = client.converse(**request)
            else:
                response = client.invoke_model(modelId=model_id, body=json.dumps(body))
                payload = json.loads(response["body"].read())
            latency_ms = int((time.perf_counter() - start) * 1000)

            converse_shape = config.AWS_MODE == "floci" or is_nova
            content = (payload.get("output", {}).get("message", {}).get("content", [])
                       if converse_shape else payload.get("content", []))
            text = "".join(block.get("text", "") for block in content)
            if not text.strip():
                raise ValueError("Bedrock returned no model text")
            usage_data = payload.get("usage", {})
            usage = TokenUsage(
                model_id=model_id,
                input_tokens=usage_data.get("inputTokens", 0) if converse_shape else usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("outputTokens", 0) if converse_shape else usage_data.get("output_tokens", 0),
            )

            cost = usage.cost_breakdown()
            daily_budget.record(cost["estimated_total_cost"])
            log_event(
                "llm_invocation",
                agent_name=agent_name,
                model_id=model_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                estimated_cost_usd=cost["estimated_total_cost"],
                latency_ms=latency_ms,
            )
            metrics_client.publish("AgentInvocations", 1, dimensions={"AgentName": agent_name})
            metrics_client.publish("InputTokens", usage.input_tokens, dimensions={"AgentName": agent_name})
            metrics_client.publish("OutputTokens", usage.output_tokens, dimensions={"AgentName": agent_name})
            metrics_client.publish("EstimatedLLMCost", cost["estimated_total_cost"], unit="None",
                                    dimensions={"AgentName": agent_name})

            return LLMInvocationResult(text=text, usage=usage, latency_ms=latency_ms)

        except (ClientError, Exception) as exc:  # noqa: BLE001 - deliberately broad, retried then surfaced
            last_error = exc
            log_error("llm_invocation_error", exc, agent_name=agent_name, attempt=attempt)
            metrics_client.publish("AgentErrors", 1, dimensions={"AgentName": agent_name})

    raise LLMError(
        f"Bedrock invocation failed for agent={agent_name} after {max_retries + 1} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    ) from last_error
