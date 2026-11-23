"""Exercise Floci's model and metrics APIs without Selenium or mock fallbacks."""
import json
import os
import sys
from urllib.request import urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from product_intelligence import config, llm
from product_intelligence.aws import client
from product_intelligence.observability.metrics import MetricsClient


def main():
    if config.AWS_MODE != "floci":
        print("Set AWS_MODE=floci before running this local-only check.")
        return 1
    endpoint = config.AWS_ENDPOINT_URL or "http://localhost:4566"
    results = []
    try:
        with urlopen(endpoint.rstrip("/") + "/_localstack/health", timeout=5) as response:
            results.append({"check": "floci_health", "status": response.status})
        # Call directly: MetricsClient intentionally suppresses publication errors.
        client("cloudwatch").put_metric_data(
            Namespace="ProductIntelligence/FlociCheck",
            MetricData=[{"MetricName": "Connectivity", "Value": 1, "Unit": "Count"}],
        )
        results.append({"check": "cloudwatch_put_metric_data", "status": "passed"})
        for model_id in dict.fromkeys([config.MODEL_ID_TOOLS, config.MODEL_ID]):
            result = llm.invoke(
                'Return exactly this JSON object: {"integration": "ok"}',
                system_prompt="Return only JSON.", model_id=model_id,
                max_tokens=60, max_retries=0,
                metrics_client=MetricsClient(enabled=False),
            )
            if llm.extract_json(result.text) != {"integration": "ok"}:
                raise ValueError("Model did not return the expected integration response")
            results.append({"check": "bedrock_converse", "model": model_id,
                            "status": "passed", "input_tokens": result.usage.input_tokens,
                            "output_tokens": result.usage.output_tokens})
    except Exception as exc:
        results.append({"check": "integration", "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}"})
        print(json.dumps(results, indent=2))
        return 1
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
