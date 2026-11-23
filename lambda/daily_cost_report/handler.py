"""
Lambda: daily_cost_report
Triggered on a schedule by EventBridge (spec section 36). Reads the day's
structured CloudWatch Logs for llm_invocation events, aggregates them into
a FinOps cost report, and writes it to S3.

This handler expects CloudWatch Logs Insights query permissions and an
S3 bucket name via the REPORT_BUCKET environment variable.
"""
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from product_intelligence.finops.usage import TokenUsage
from product_intelligence.finops.report import CostReportBuilder
from product_intelligence import config


def handler(event, context):
    import boto3

    logs_client = boto3.client("logs", region_name=config.AWS_REGION)
    s3_client = boto3.client("s3", region_name=config.AWS_REGION)

    report_bucket = os.environ["REPORT_BUCKET"]
    log_group = os.environ.get("LOG_GROUP_NAME", config.LOG_GROUP_NAME)

    yesterday = date.today() - timedelta(days=1)
    start_ts = int(time.mktime(yesterday.timetuple()) * 1000)
    end_ts = int(time.mktime(date.today().timetuple()) * 1000)

    query = (
        "fields @message "
        "| filter event = 'llm_invocation' "
        "| sort @timestamp asc"
    )

    query_id = logs_client.start_query(
        logGroupName=log_group,
        startTime=start_ts,
        endTime=end_ts,
        queryString=query,
    )["queryId"]

    # Poll for query completion (CloudWatch Logs Insights queries are async)
    for _ in range(30):
        result = logs_client.get_query_results(queryId=query_id)
        if result["status"] in ("Complete", "Failed", "Cancelled"):
            break
        time.sleep(1)

    builder = CostReportBuilder()
    for row in result.get("results", []):
        message_field = next((f["value"] for f in row if f["field"] == "@message"), None)
        if not message_field:
            continue
        try:
            record = json.loads(message_field)
            usage = TokenUsage(
                model_id=record["model_id"],
                input_tokens=record["input_tokens"],
                output_tokens=record["output_tokens"],
            )
            builder.add(usage, agent_name=record.get("agent_name", "unknown"))
        except (json.JSONDecodeError, KeyError):
            continue

    report = builder.build(report_date=yesterday.isoformat())
    report_key = f"daily-cost-report-{yesterday.isoformat()}.json"

    s3_client.put_object(
        Bucket=report_bucket,
        Key=report_key,
        Body=json.dumps(report, indent=2),
        ContentType="application/json",
    )

    return {"report_key": report_key, "total_estimated_cost_usd": report["total_estimated_cost_usd"]}
