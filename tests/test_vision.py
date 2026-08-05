"""Vision (multimodal) support: ImageRef, the provider seam, Azure vision, and resilient pass-through."""
import base64

from autarch import ImageRef
from autarch.intelligence.base import ModelProvider
from autarch.intelligence.mock import MockProvider
from autarch.intelligence.azure_openai import AzureOpenAIProvider
from autarch.intelligence.vision import openai_vision_content
from autarch.resilience import make_resilient


def test_imageref_from_bytes_data_uri():
    ref = ImageRef.from_bytes(b"\x89PNG\r\n", mime="image/png")
    uri = ref.to_data_uri()
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"\x89PNG\r\n"


def test_imageref_url_passthrough():
    assert ImageRef.from_url("https://x/y.png").to_data_uri() == "https://x/y.png"


def test_imageref_from_path(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"hello-bytes")
    uri = ImageRef.from_path(str(p)).to_data_uri()
    assert uri.startswith("data:image/png;base64,")
    assert base64.b64decode(uri.split(",", 1)[1]) == b"hello-bytes"


def test_openai_vision_content_shape():
    parts = openai_vision_content("hi", [ImageRef.from_url("https://x/y.png", detail="high")])
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"] == "https://x/y.png"
    assert parts[1]["image_url"]["detail"] == "high"


def test_text_provider_defaults_and_fallback():
    m = MockProvider()
    assert m.supports_vision() is False
    # a text-only provider ignores images and falls back to complete()
    out = m.complete_vision("describe", [ImageRef.from_url("https://x/y.png")], system="s")
    assert isinstance(out, str)


def test_azure_supports_vision_and_builds_multimodal(monkeypatch):
    p = AzureOpenAIProvider(deployment="gpt-5.4", endpoint="https://e", api_key="k")
    assert p.supports_vision() is True
    captured = {}

    def fake_chat(messages, *, json_mode, est_prompt, est_system):
        captured["messages"] = messages
        captured["json_mode"] = json_mode
        return "described"

    monkeypatch.setattr(p, "_chat", fake_chat)
    out = p.complete_vision("what is this", [ImageRef.from_url("https://x/y.png")], system="sys")
    assert out == "described"
    user_msg = captured["messages"][-1]
    assert user_msg["role"] == "user"
    parts = user_msg["content"]
    assert parts[0]["type"] == "text" and parts[0]["text"] == "what is this"
    assert parts[1]["type"] == "image_url" and parts[1]["image_url"]["url"] == "https://x/y.png"
    assert captured["json_mode"] is False  # prose prompt (no "json") -> JSON mode not forced


def test_resilient_passes_vision_through():
    class Fake(ModelProvider):
        name = "fake"

        def complete(self, prompt, system=None):
            return "text"

        def supports_vision(self):
            return True

        def complete_vision(self, prompt, images, system=None):
            return f"vision:{len(list(images))}"

    r = make_resilient(Fake())
    assert r.supports_vision() is True
    assert r.complete_vision("p", [ImageRef.from_url("https://x")], system="s") == "vision:1"
