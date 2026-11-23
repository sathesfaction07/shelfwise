#!/usr/bin/env python3
"""
Convenience wrapper around the CloudFormation stack deletion + a reminder
of anything that might not be captured by the stack (spec section 69).

Usage:
  python scripts/cleanup.py --stack-name julie
"""
import argparse
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="julie")
    args = parser.parse_args()

    print(f"Deleting CloudFormation stack: {args.stack_name}")
    result = subprocess.run(
        ["aws", "cloudformation", "delete-stack", "--stack-name", args.stack_name],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print("Failed to initiate stack deletion:")
        print(result.stderr)
        sys.exit(1)

    print("Deletion initiated. Waiting for stack to be fully removed "
          "(this can take a few minutes)...")
    subprocess.run(
        ["aws", "cloudformation", "wait", "stack-delete-complete", "--stack-name", args.stack_name],
    )
    print("Stack deleted.")
    print("\nAlso check manually for anything NOT managed by this stack:")
    print("  - Any AgentCore Gateway targets created outside CloudFormation")
    print("  - Any manually-created SNS email subscriptions")
    print("  - The S3 bucket's contents, if versioning/retention left objects behind")


if __name__ == "__main__":
    main()
