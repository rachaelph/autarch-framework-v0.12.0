"""Parsing robustness — real models return messy text, not clean JSON."""
from autarch.util import extract_json


def test_clean_json():
    assert extract_json('{"capability": "file.read"}') == {"capability": "file.read"}


def test_json_in_markdown_fence():
    text = '```json\n{"verdict": "approve", "reasons": "ok"}\n```'
    assert extract_json(text) == {"verdict": "approve", "reasons": "ok"}


def test_plain_fence_without_lang():
    text = '```\n{"verdict": "veto"}\n```'
    assert extract_json(text) == {"verdict": "veto"}


def test_prose_before_json():
    text = 'Sure! Here is my decision:\n{"verdict": "revise", "reasons": "risky"}'
    assert extract_json(text) == {"verdict": "revise", "reasons": "risky"}


def test_prose_after_json():
    text = '{"capability": "file.write", "params": {}} -- hope that helps!'
    assert extract_json(text)["capability"] == "file.write"


def test_first_of_multiple_objects_wins():
    text = '{"verdict": "approve"} and also {"verdict": "veto"}'
    assert extract_json(text) == {"verdict": "approve"}


def test_nested_object_is_balanced():
    text = 'noise {"capability": "file.write", "params": {"path": "a.txt", "meta": {"x": 1}}} tail'
    obj = extract_json(text)
    assert obj["params"]["meta"]["x"] == 1


def test_braces_inside_string_do_not_confuse_scanner():
    text = '{"reasons": "use {curly} braces carefully", "verdict": "approve"}'
    obj = extract_json(text)
    assert obj["verdict"] == "approve"
    assert "{curly}" in obj["reasons"]


def test_garbage_returns_none():
    assert extract_json("I cannot help with that.") is None


def test_empty_returns_none():
    assert extract_json("") is None
    assert extract_json(None) is None


def test_array_is_not_an_object():
    # We only accept JSON objects, not arrays or scalars.
    assert extract_json('[1, 2, 3]') is None


def test_unterminated_object_returns_none():
    assert extract_json('{"verdict": "approve"') is None
