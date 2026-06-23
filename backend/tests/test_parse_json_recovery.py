"""Unit tests for the hardened Fence_Stripping_Parse recovery in game._parse_json.

The Game_Service LLM functions parse strict JSON from the model. In production a
node checkpoint for "World War 2" repeatedly failed with
``Expecting value: line 9 column 9`` because the model wrapped the JSON array in
prose / appended a trailing comma, which a bare ``json.loads`` cannot handle —
that surfaced as a 500 on ``POST /api/game/node``.

``game._parse_json`` now falls back to ``game._extract_json_substring``: it
strips code fences, and when a strict parse fails it recovers the first balanced
JSON array/object span (ignoring surrounding prose) and tolerates trailing
commas. These tests pin that recovery while confirming genuinely unparseable
input still raises (so the decide/intuition/clip retry-then-fallback path is
unchanged).
"""
import pytest

from app.services import game


class TestParseJsonHappyPath:
    def test_plain_json_array(self):
        assert game._parse_json('[{"a": 1}, {"b": 2}]') == [{"a": 1}, {"b": 2}]

    def test_fenced_json_array(self):
        raw = '```json\n[{"a": 1}]\n```'
        assert game._parse_json(raw) == [{"a": 1}]

    def test_plain_json_object(self):
        assert game._parse_json('{"hook": "you got this"}') == {"hook": "you got this"}


class TestParseJsonRecovery:
    def test_prose_wrapped_array_is_recovered(self):
        # The model prepends/append prose around the JSON — recover the array.
        raw = 'Sure! Here is the quiz you asked for:\n[{"q": 1}, {"q": 2}]\nHope that helps!'
        assert game._parse_json(raw) == [{"q": 1}, {"q": 2}]

    def test_trailing_comma_before_closing_bracket_is_tolerated(self):
        raw = '[{"q": 1}, {"q": 2},]'
        assert game._parse_json(raw) == [{"q": 1}, {"q": 2}]

    def test_trailing_comma_inside_object_is_tolerated(self):
        raw = '[{"q": 1, "options": ["a", "b",],}]'
        assert game._parse_json(raw) == [{"q": 1, "options": ["a", "b"]}]

    def test_fenced_with_prose_after_fence(self):
        raw = '```json\n[{"q": 1}]\n```\nLet me know if you want changes.'
        assert game._parse_json(raw) == [{"q": 1}]

    def test_brackets_inside_string_values_do_not_confuse_matcher(self):
        # A ']' inside a string must not be treated as the array terminator.
        raw = 'Here: [{"q": "use arr[i] then arr[j]"}] done'
        assert game._parse_json(raw) == [{"q": "use arr[i] then arr[j]"}]

    def test_prose_wrapped_object_is_recovered(self):
        raw = 'The intuition is: {"hook": "think of it as a stack"} ok?'
        assert game._parse_json(raw) == {"hook": "think of it as a stack"}


class TestParseJsonStillRaises:
    def test_unparseable_garbage_raises(self):
        # Mirrors test_llm_function_parsing: fenced non-JSON still raises so the
        # orchestrator's retry-then-fallback path is preserved.
        with pytest.raises(ValueError):
            game._parse_json("```json\nnot valid json at all {{{\n```")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            game._parse_json("")

    def test_prose_with_no_json_raises(self):
        with pytest.raises(ValueError):
            game._parse_json("I could not generate the quiz right now, sorry.")
