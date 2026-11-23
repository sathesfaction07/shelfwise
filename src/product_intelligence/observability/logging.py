"""
Structured JSON logging (spec section 31). Every major operation logs one
JSON line through this module instead of ad-hoc print()/log calls, so
CloudWatch Logs Insights and metric filters have a consistent shape to
query against.
"""
import json
import logging
import sys

_logger = logging.getLogger("product_intelligence")
if not _logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)


def log_event(event: str, **fields) -> dict:
    """
    Emits one structured JSON log line, e.g.:
      log_event("review_analysis", asin="B001", review_count=842, input_tokens=1150)
    Returns the dict that was logged, so callers can reuse the same data
    for a CloudWatch metric emission if needed.
    """
    record = {"event": event, **fields}
    _logger.info(json.dumps(record))
    return record


def log_error(event: str, error: Exception, **fields) -> dict:
    record = {
        "event": event,
        "error_type": type(error).__name__,
        "error_message": str(error),
        **fields,
    }
    _logger.error(json.dumps(record))
    return record
