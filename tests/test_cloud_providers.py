"""Offline tests for the cloud providers and pricing (no network, no keys)."""
import json

import pytest

from autarch.errors import ModelError, ModelUnavailable, RateLimited
from autarch.intelligence.anthropic import AnthropicProvider
from autarch.intelligence.factory import build_provider
from autarch.intelligence.openai import OpenAIProvider
from autarch.intelligence.pricing import DEFAULT_PRICE_BOOK, PriceBook, estimate_tokens


def test_openai_request_and_response_shape():
    seen = {}

    def transport(body, headers, timeout):
        seen["body"] = json.loads(body)
        seen["auth"] = headers.get("Authorization")
        return json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()

    p = OpenAIProvider(api_key="sk-test", _transport=transport)
    assert p.complete("hi", system="sys") == "hello"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"][0]["role"] == "system"
    assert seen["body"]["messages"][1]["content"] == "hi"


def test_openai_missing_key_raises():
    with pytest.raises(ModelError):
        OpenAIProvider(api_key="").complete("hi")


def test_openai_bad_response_raises():
    p = OpenAIProvider(api_key="k", _transport=lambda b, h, t: b"{}")
    with pytest.raises(ModelError):
        p.complete("hi")


def test_anthropic_request_and_response_shape():
    seen = {}

    def transport(body, headers, timeout):
        seen["body"] = json.loads(body)
        seen["key"] = headers.get("x-api-key")
        seen["ver"] = headers.get("anthropic-version")
        return json.dumps({"content": [{"type": "text", "text": "claude says hi"}]}).encode()

    p = AnthropicProvider(api_key="sk-ant", _transport=transport)
    assert p.complete("hi", system="be brief") == "claude says hi"
    assert seen["key"] == "sk-ant"
    assert seen["ver"]
    assert seen["body"]["system"] == "be brief"


def test_factory_builds_cloud_providers_without_keys():
    # Construction must not require a key (only calling complete() does).
    assert build_provider("openai:gpt-4o").name.endswith("gpt-4o")
    assert build_provider("anthropic:claude-3-5-haiku-latest").name.endswith("haiku-latest")
    assert build_provider("gpt").name.startswith("openai")
    assert build_provider("claude").name.startswith("anthropic")


def test_pricing_known_and_family_match():
    pb = DEFAULT_PRICE_BOOK
    assert pb.price("gpt-4o") == (2.50, 10.00)
    # family/prefix match for a dated model id
    assert pb.price("claude-3-5-haiku-20241022") == pb.price("claude-3-5-haiku-latest")
    # local models are free
    assert pb.price("ollama:llama3") == (0.0, 0.0)


def test_token_cost_and_overrides():
    pb = PriceBook(overrides={"custom-model": (1.0, 2.0)})
    # 1M input tokens at $1/M + 0 output = $1.00
    assert abs(pb.token_cost("custom-model", 1_000_000, 0) - 1.0) < 1e-9
    assert estimate_tokens("abcd") == 1


def test_bare_model_names_route_to_right_vendor():
    from autarch.intelligence.factory import build_provider
    assert build_provider("gpt-4o").name == "openai:gpt-4o"
    assert build_provider("gpt-4o-mini").name == "openai:gpt-4o-mini"
    assert build_provider("o3-mini").name == "openai:o3-mini"
    assert build_provider("claude-3-5-sonnet-latest").name == "anthropic:claude-3-5-sonnet-latest"


def test_mixed_vendor_council_builds_without_keys():
    from autarch.intelligence.factory import build_provider
    council = [build_provider(s) for s in
               ["gpt-4o", "claude-3-5-haiku-latest", "ollama:llama3", "mock"]]
    names = [p.name for p in council]
    assert any("openai" in n for n in names)
    assert any("anthropic" in n for n in names)
    assert any("ollama" in n for n in names)
    assert any("mock" in n for n in names)


def test_unknown_spec_still_errors_clearly():
    import pytest
    from autarch.intelligence.factory import build_provider
    with pytest.raises(ValueError):
        build_provider("not-a-real-model-xyz")
