#!/usr/bin/env python3
"""
OPTIONAL script (spec section 40) - retrieves actual Bedrock spend from AWS
Cost Explorer, if the account has the required permissions
(ce:GetCostAndUsage). This is not required for the basic demo.

IMPORTANT: the application's own FinOps estimate (finops/usage.py) will
never exactly match your AWS invoice - Cost Explorer data typically lags
by up to 24 hours and reflects negotiated/actual billing, while the
application estimate is computed client-side from token counts and the
prices in finops/model_pricing.json. Use this script to compare the two,
not to treat either one as ground truth on its own.

Usage:
  python scripts/billing_report.py --days 7
"""
import argparse
import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from product_intelligence import config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    if config.AWS_MODE == "floci":
        parser.error("Cost Explorer billing is not meaningful in Floci; use /budget for estimates")

    import boto3

    ce = boto3.client("ce", region_name="us-east-1")  # Cost Explorer is a global/us-east-1 API
    end = date.today()
    start = end - timedelta(days=args.days)

    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Bedrock"]}},
    )

    print(f"AWS Cost Explorer - Amazon Bedrock spend, {start} to {end}")
    print("-" * 50)
    total = 0.0
    for day in response["ResultsByTime"]:
        amount = float(day["Total"]["UnblendedCost"]["Amount"])
        total += amount
        print(f"{day['TimePeriod']['Start']}: ${amount:.4f}")
    print("-" * 50)
    print(f"Total: ${total:.4f}")
    print("\nCompare this against the application's own estimate from "
          "finops/report.py or last_run_report.json - they will not match exactly.")


if __name__ == "__main__":
    main()
