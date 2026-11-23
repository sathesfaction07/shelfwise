"""Local routing and real SDK contract tests; no services or credentials needed."""
import io
import json

import pytest
from botocore.stub import Stubber

from product_intelligence import aws, config, llm
from product_intelligence.agents.recommendation import RecommendationAgent
from product_intelligence.agents.review import ReviewIntelligenceAgent
from product_intelligence.observability.metrics import MetricsClient


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(config, "AWS_MODE", "floci")
    monkeypatch.setattr(config, "AWS_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setattr(config, "REQUIRE_LLM_SUCCESS", True)
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.setattr(llm.daily_budget, "check", lambda: None)
    monkeypatch.setattr(llm.daily_budget, "record", lambda cost: None)


def test_floci_routing_and_credentials_ignore_real_profile(local):
    client = aws.client("bedrock-runtime")
    assert client.meta.endpoint_url == "http://localhost:4566"
    credentials = client._request_signer._credentials.get_frozen_credentials()
    assert credentials.access_key == "test"
    assert credentials.secret_key == "test"


def test_service_endpoint_override(local, monkeypatch):
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://localhost:4567")
    assert aws.client("bedrock-runtime").meta.endpoint_url == "http://localhost:4567"
    assert aws.client("cloudwatch").meta.endpoint_url == "http://localhost:4566"


def test_invalid_mode_does_not_create_client(monkeypatch):
    monkeypatch.setattr(config, "AWS_MODE", "flcoi")
    with pytest.raises(ValueError, match="AWS_MODE"):
        aws.client("bedrock-runtime")


@pytest.mark.parametrize("model", [config.MODEL_ID, config.MODEL_ID_TOOLS])
def test_floci_converse_contract_and_usage(local, monkeypatch, model):
    client = aws.client("bedrock-runtime")
    monkeypatch.setattr(llm, "aws_client", lambda service: client)
    with Stubber(client) as stub:
        stub.add_response("converse", {
            "output": {"message": {"role": "assistant", "content": [{"text": '{"ok":true}'}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 12, "outputTokens": 5, "totalTokens": 17},
            "metrics": {"latencyMs": 20},
        }, {
            "modelId": model,
            "messages": [{"role": "user", "content": [{"text": "hello"}]}],
            "system": [{"text": "JSON only"}],
            "inferenceConfig": {"maxTokens": 50, "temperature": 0.1},
        })
        result = llm.invoke("hello", system_prompt="JSON only", model_id=model,
                            max_tokens=50, max_retries=0,
                            metrics_client=MetricsClient(enabled=False))
        assert llm.extract_json(result.text) == {"ok": True}
        assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 5)
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("nova", [False, True])
def test_aws_invoke_model_response_contract(local, monkeypatch, nova):
    client = aws.client("bedrock-runtime")
    monkeypatch.setattr(config, "AWS_MODE", "aws")
    monkeypatch.setattr(llm, "aws_client", lambda service: client)
    model = config.MODEL_ID_TOOLS if nova else config.MODEL_ID
    payload = ({"output": {"message": {"content": [{"text": "answer"}]}},
                "usage": {"inputTokens": 21, "outputTokens": 3}}
               if nova else {"content": [{"text": "answer"}],
                             "usage": {"input_tokens": 21, "output_tokens": 3}})
    with Stubber(client) as stub:
        stub.add_response("invoke_model", {
            "body": io.BytesIO(json.dumps(payload).encode()),
            "contentType": "application/json",
        })
        result = llm.invoke("hello", model_id=model, max_retries=0,
                            metrics_client=MetricsClient(enabled=False))
        assert result.text == "answer"
        assert (result.usage.input_tokens, result.usage.output_tokens) == (21, 3)


def test_local_model_failure_cannot_be_reported_as_mock_success(local, monkeypatch):
    def fail(**kwargs):
        raise llm.LLMError("Floci unavailable")

    monkeypatch.setattr(llm, "invoke", fail)
    metrics = MetricsClient(enabled=False)
    review = ReviewIntelligenceAgent(None, metrics)
    recommendation = RecommendationAgent(metrics)
    monkeypatch.setattr(review, "_build_prompt", lambda *args: "prompt")
    with pytest.raises(llm.LLMError, match="Floci unavailable"):
        review._summarize("product", {}, {})
    with pytest.raises(llm.LLMError, match="Floci unavailable"):
        recommendation._explain([])


def test_default_gateway_providers_exist():
    from product_intelligence.providers.amazon import AmazonProvider
    from product_intelligence.tools import price_fetcher, product_search, review_fetcher

    for tool in (price_fetcher, product_search, review_fetcher):
        assert isinstance(tool._default_provider(), AmazonProvider)


def test_slow_local_model_is_not_retried(local, monkeypatch):
    from unittest.mock import Mock

    client = Mock()
    client.converse.side_effect = RuntimeError("model timed out")
    monkeypatch.setattr(llm, "aws_client", lambda service: client)
    with pytest.raises(llm.LLMError, match="model timed out"):
        llm.invoke("prompt", max_retries=4, metrics_client=MetricsClient(enabled=False))
    assert client.converse.call_count == 1


def test_local_prompt_is_bounded_but_statistics_cover_all_reviews(local, monkeypatch):
    from datetime import date
    from unittest.mock import Mock

    provider = Mock()
    provider.get_reviews.return_value = [
        {"review_id": str(i), "rating": 5, "text": str(i) + "x" * 2000,
         "date": date.today().isoformat(), "title": "review", "helpful_votes": i}
        for i in range(10)
    ]
    agent = ReviewIntelligenceAgent(provider, MetricsClient(enabled=False))
    seen = {}

    def summarize(title, stats, sample):
        seen.update(sample)
        return {"positive_themes": [], "negative_themes": [], "summary": "summary",
                "overall_sentiment": "positive"}

    monkeypatch.setattr(agent, "_summarize", summarize)
    result = agent.analyze("TEST", "US")
    assert result["review_count"] == 10
    assert len(seen["positive"]) <= config.LOCAL_REVIEW_SAMPLE_PER_SENTIMENT
    assert all(len(r["text"]) <= config.LOCAL_REVIEW_EXCERPT_CHARS for r in seen["positive"])
    assert result["tokens"]["optimized_estimate"] < result["tokens"]["raw_estimate"]


def test_interactive_review_retains_evidence_on_model_failure(local, monkeypatch):
    from datetime import date
    from unittest.mock import Mock

    provider = Mock()
    provider.get_reviews.return_value = [{"review_id": "r1", "rating": 5,
                                         "text": "good sound", "date": date.today().isoformat()}]
    monkeypatch.setattr(llm, "invoke", Mock(side_effect=llm.LLMError("unavailable")))
    agent = ReviewIntelligenceAgent(provider, MetricsClient(enabled=False))
    agent.allow_partial = True
    result = agent.analyze("TEST", "US")
    assert result["review_count"] == 1
    assert result["positive_percentage"] == 100
    assert result["analysis_source"] == "unavailable"
    assert result["analysis_error"]
    assert result["positive_themes"] == []


def test_interactive_ranking_is_explicitly_deterministic_on_model_failure(local, monkeypatch):
    from unittest.mock import Mock

    monkeypatch.setattr(llm, "invoke", Mock(side_effect=llm.LLMError("unavailable")))
    agent = RecommendationAgent(MetricsClient(enabled=False))
    agent.allow_partial = True
    candidates = [{"product": {"asin": asin}, "reviews": {}, "pricing": {},
                   "scores": {"overall_score": score}}
                  for asin, score in [("LOW", 0.2), ("HIGH", 0.8)]]
    result = agent.recommend(candidates)
    assert result["asin"] == "HIGH"
    assert result["source"] == "deterministic"
    assert result["recommendation"] == "score_based"
    assert result["confidence"] is None
