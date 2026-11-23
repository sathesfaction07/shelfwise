#!/usr/bin/env python3
"""
Publishes a metric value intentionally above an alarm's threshold, so the
observability pipeline can be DEMONSTRATED, not just asserted to work
(spec section 34).

Usage:
  python scripts/trigger_test_alarm.py --metric AgentErrors --value 10
  python scripts/trigger_test_alarm.py --metric InputTokens --value 999999
  python scripts/trigger_test_alarm.py --metric EstimatedLLMCost --value 999 --unit None

After running, check:
  CloudWatch -> Alarms -> the relevant alarm should flip OK -> ALARM
  within one evaluation period, and SNS should deliver an email/notification.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from product_intelligence import config
from product_intelligence.aws import client as aws_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--metric", required=True,
                         choices=["AgentErrors", "InputTokens", "EstimatedLLMCost"])
    parser.add_argument("--value", type=float, required=True)
    parser.add_argument("--unit", default="Count")
    parser.add_argument("--dimension-agent-name", default="test-trigger")
    args = parser.parse_args()

    client = aws_client("cloudwatch")

    print(f"Publishing {args.metric}={args.value} to namespace {config.METRICS_NAMESPACE}...")
    client.put_metric_data(
        Namespace=config.METRICS_NAMESPACE,
        MetricData=[{
            "MetricName": args.metric,
            "Value": args.value,
            "Unit": args.unit,
            "Dimensions": [{"Name": "AgentName", "Value": args.dimension_agent_name}],
        }],
    )
    print("Published. Check CloudWatch -> Alarms in a minute or two - the corresponding "
          "alarm should transition to ALARM state and SNS should send a notification.")


if __name__ == "__main__":
    main()
