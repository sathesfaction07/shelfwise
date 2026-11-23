"""
Custom CloudWatch metrics (spec section 32). A thin wrapper around
cloudwatch.put_metric_data with a fixed, deliberately small set of metric
names and dimensions - "do not create hundreds of dimensions".

boto3 is imported lazily so that unit tests and the local demo (DemoProvider
path) never require AWS credentials just to import this module.
"""
from product_intelligence import config
from product_intelligence.aws import client as aws_client

_METRIC_NAMES = {
    "AgentInvocations",
    "AgentErrors",
    "ToolInvocations",
    "ToolErrors",
    "ReviewsFetched",
    "ReviewsAnalyzed",
    "RawEstimatedTokens",
    "OptimizedEstimatedTokens",
    "TokenReductionPercent",
    "InputTokens",
    "OutputTokens",
    "EstimatedLLMCost",
}


class MetricsClient:
    """
    Set enabled=False (or leave AWS credentials unconfigured and just don't
    call publish()) to make this a no-op for local/offline runs.
    """

    def __init__(self, namespace: str | None = None, enabled: bool = True):
        self.namespace = namespace or config.METRICS_NAMESPACE
        self.enabled = enabled
        self._client = None

    def _get_client(self):
        if self._client is None:
            self._client = aws_client("cloudwatch")
        return self._client

    def publish(self, metric_name: str, value: float, unit: str = "Count", dimensions: dict | None = None):
        if metric_name not in _METRIC_NAMES:
            raise ValueError(f"Unknown metric name {metric_name!r}. Add it to _METRIC_NAMES if intentional.")
        if not self.enabled:
            return None

        dimensions = dimensions or {}
        try:
            client = self._get_client()
            return client.put_metric_data(
                Namespace=self.namespace,
                MetricData=[{
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": [{"Name": k, "Value": str(v)} for k, v in dimensions.items()],
                }],
            )
        except Exception:
            # Metrics publishing should never break the application (spec
            # section 61: "CloudWatch metric failure" is a handled error).
            return None
