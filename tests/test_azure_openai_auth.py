from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from autarch.errors import ModelError
from autarch.intelligence.azure_openai import AzureOpenAIProvider


def test_azure_openai_uses_bearer_token_when_api_key_is_absent() -> None:
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1},
        }
    ).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False

    with patch(
        "autarch.intelligence.azure_openai.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        provider = AzureOpenAIProvider(
            deployment="gpt-5.1",
            endpoint="https://example.openai.azure.com",
            token_provider=lambda: "synthetic-access-token",
        )
        assert provider.complete("Return JSON") == "{}"

    request = urlopen.call_args.args[0]
    assert request.get_header("Authorization") == (
        "Bearer synthetic-access-token"
    )
    assert request.get_header("Api-key") is None


def test_azure_openai_supports_foundry_responses_endpoint() -> None:
    response = MagicMock()
    response.read.return_value = json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"lines":[]}',
                        }
                    ],
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 3},
        }
    ).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    endpoint = (
        "https://example.services.ai.azure.com/openai/v1/responses"
    )

    with patch(
        "autarch.intelligence.azure_openai.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        provider = AzureOpenAIProvider(
            deployment="gpt-5.1",
            endpoint=endpoint,
            token_provider=lambda: "synthetic-access-token",
            max_completion_tokens=500,
        )
        assert provider.complete("Return JSON") == '{"lines":[]}'

    request = urlopen.call_args.args[0]
    body = json.loads(request.data.decode("utf-8"))
    assert request.full_url == endpoint
    assert body["model"] == "gpt-5.1"
    assert body["text"]["format"] == {"type": "json_object"}
    assert body["max_output_tokens"] == 500
    assert "response_format" not in body


def test_azure_openai_redacts_principal_from_http_error() -> None:
    error = urllib.error.HTTPError(
        url=(
            "https://example.services.ai.azure.com/openai/v1/responses"
        ),
        code=401,
        msg="PermissionDenied",
        hdrs=None,
        fp=io.BytesIO(
            json.dumps(
                {
                    "error": {
                        "code": "PermissionDenied",
                        "message": (
                            "The principal developer@example.com lacks "
                            "responses/write."
                        ),
                    }
                }
            ).encode("utf-8")
        ),
    )

    with patch(
        "autarch.intelligence.azure_openai.urllib.request.urlopen",
        side_effect=error,
    ):
        provider = AzureOpenAIProvider(
            deployment="gpt-5.1",
            endpoint=(
                "https://example.services.ai.azure.com/"
                "openai/v1/responses"
            ),
            token_provider=lambda: "synthetic-access-token",
        )
        with pytest.raises(ModelError) as captured:
            provider.complete("Return JSON")

    assert "developer@example.com" not in str(captured.value)
    assert "[redacted-principal]" in str(captured.value)
