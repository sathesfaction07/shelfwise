#!/usr/bin/env python3
"""
Invokes the DEPLOYED AgentCore Runtime (not the local pipeline - that's
run_local_demo.py). Requires the stack to be deployed (see infrastructure/)
and AGENT_RUNTIME_ARN to be set (printed as a CloudFormation output).

Usage:
  python scripts/invoke_agent.py --product "wireless headphones" --country US
  python scripts/invoke_agent.py --product "wireless headphones" --country US --min-rating 4.0 --max-price 100
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from product_intelligence import config
from product_intelligence.aws import client as aws_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--min-rating", type=float, default=None)
    parser.add_argument("--max-price", type=float, default=None)
    parser.add_argument("--agent-runtime-arn", default=os.environ.get("AGENT_RUNTIME_ARN"))
    args = parser.parse_args()

    if not args.agent_runtime_arn:
        print("ERROR: --agent-runtime-arn not provided and AGENT_RUNTIME_ARN is not set.")
        print("Deploy the CloudFormation stack first and copy its AgentRuntimeArn output.")
        sys.exit(1)

    client = aws_client("bedrock-agentcore")

    payload = {
        "product": args.product,
        "country": args.country,
        "min_rating": args.min_rating,
        "max_price": args.max_price,
    }

    print("Invoking deployed AgentCore Runtime...")
    response = client.invoke_agent_runtime(
        agentRuntimeArn=args.agent_runtime_arn,
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps(payload).encode(),
    )

    content_type = response.get("contentType", "")
    if "text/event-stream" in content_type:
        chunks = []
        for event in response.get("response", []):
            chunks.append(event)
        result_text = b"".join(chunks).decode("utf-8", errors="replace")
    else:
        result_text = response["response"].read().decode("utf-8")

    try:
        result = json.loads(result_text)
        print(json.dumps(result, indent=2))
    except json.JSONDecodeError:
        print(result_text)


if __name__ == "__main__":
    main()
