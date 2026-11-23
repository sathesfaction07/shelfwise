"""AWS clients with explicit support for the existing Floci local stack."""
import os

from product_intelligence import config


def client(service: str):
    import boto3
    from botocore.config import Config

    if config.AWS_MODE not in {"aws", "floci"}:
        raise ValueError("AWS_MODE must be 'aws' or 'floci'")
    endpoint = os.environ.get(
        "AWS_ENDPOINT_URL_" + service.upper().replace("-", "_"),
        config.AWS_ENDPOINT_URL,
    )
    kwargs = {"region_name": config.AWS_REGION}
    if config.AWS_MODE == "floci":
        endpoint = endpoint or "http://localhost:4566"
        # Never resolve the user's AWS profile or instance credentials locally.
        kwargs.update(
            aws_access_key_id="test",
            aws_secret_access_key="test",
            aws_session_token="test",
            config=Config(connect_timeout=3, read_timeout=config.LOCAL_LLM_TIMEOUT_SECONDS,
                          retries={"max_attempts": 0}),
        )
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client(service, **kwargs)
