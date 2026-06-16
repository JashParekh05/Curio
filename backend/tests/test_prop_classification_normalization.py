"""Property-based test for Concept_Type classification normalization.

# Feature: content-hook-engagement, Property 3: Classification normalizes to a supported type with correct default signalling

classify_concept_type makes an LLM call, so the OpenAI client is mocked
(monkeypatching the module's _client) to return controlled raw strings — both
valid ones ("problem_solving", "conceptual", "default") and invalid ones
(random text, empty, malformed). No real network calls are made.

Validates: Requirements 1.1, 1.7
"""
from hypothesis import HealthCheck, given, settings, strategies as st

from app.agents import section_planner as sp

SUPPORTED = {"problem_solving", "conceptual", "default"}


# ---------------------------------------------------------------------------
# Fake OpenAI client — shapes match resp.choices[0].message.content access
# ---------------------------------------------------------------------------

class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, *a, **k):
        return _FakeResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeClient:
    """A double for openai.OpenAI() returning a fixed completion content."""
    def __init__(self, content):
        self.chat = _FakeChat(content)


class _BoomClient:
    """A client whose create() raises, to exercise the failure path."""
    class _Chat:
        class _Completions:
            def create(self, *a, **k):
                raise RuntimeError("LLM down")
        completions = _Completions()
    chat = _Chat()


def _patch_client(monkeypatch, content):
    monkeypatch.setattr(sp, "_client", lambda: _FakeClient(content))


def _normalize(raw):
    """Oracle mirroring the module's normalization of raw model output."""
    return (raw or "").strip().strip('"').lower()


def _assert_default_arc_integrity(concept_type, default_applied):
    """When the default is applied, the arc built from it must still satisfy
    the Property-2 integrity rules: roles equal the (default) template in order,
    ordinals are consecutive 1..n, no added/omitted/duplicated roles."""
    assert (concept_type, default_applied) == ("default", True)
    template = sp.select_template(concept_type)
    assert template == sp.DEFAULT_TEMPLATE
    arc = sp.build_planned_arc(
        topic_slug="t",
        concept_type=concept_type,
        template=template,
        default_applied=default_applied,
    )
    assert arc.default_applied is True
    assert arc.template_empty is False
    assert [r.role for r in arc.roles] == list(template)
    assert [r.ordinal for r in arc.roles] == list(range(1, len(template) + 1))


# ---------------------------------------------------------------------------
# Property 3
# ---------------------------------------------------------------------------

class TestClassificationNormalization:
    # Variants that still normalize to a supported value (case/quote/whitespace).
    _supported_variants = st.sampled_from([
        "problem_solving", "conceptual", "default",
        "Problem_Solving", "CONCEPTUAL", "Default",
        '"problem_solving"', "  conceptual  ", '"DEFAULT"',
        '\n problem_solving \n', '""conceptual""',
    ])

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(raw=st.one_of(st.none(), st.text(max_size=40)))
    def test_result_is_always_supported_with_correct_default_signal(self, monkeypatch, raw):
        # For ANY raw classifier output (including unparseable/unsupported),
        # the result is a supported ConceptType; unsupported raw -> default+True.
        _patch_client(monkeypatch, raw)
        concept_type, default_applied = sp.classify_concept_type("A Topic", "intermediate")

        assert concept_type in SUPPORTED

        if _normalize(raw) in SUPPORTED:
            assert (concept_type, default_applied) == (_normalize(raw), False)
        else:
            # Unsupported -> default with default_applied True, and the built arc
            # still satisfies the Property-2 integrity rules.
            _assert_default_arc_integrity(concept_type, default_applied)

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(raw=_supported_variants)
    def test_supported_variants_classify_without_default(self, monkeypatch, raw):
        _patch_client(monkeypatch, raw)
        concept_type, default_applied = sp.classify_concept_type("A Topic", "intermediate")
        assert concept_type == _normalize(raw)
        assert concept_type in SUPPORTED
        assert default_applied is False

    # --- fixed edge cases -------------------------------------------------

    def test_empty_string_applies_default(self, monkeypatch):
        _patch_client(monkeypatch, "")
        ct, default_applied = sp.classify_concept_type("A Topic", "intermediate")
        _assert_default_arc_integrity(ct, default_applied)

    def test_none_content_applies_default(self, monkeypatch):
        _patch_client(monkeypatch, None)
        ct, default_applied = sp.classify_concept_type("A Topic", "intermediate")
        _assert_default_arc_integrity(ct, default_applied)

    def test_malformed_output_applies_default(self, monkeypatch):
        _patch_client(monkeypatch, "I think this is a conceptual topic about recursion.")
        ct, default_applied = sp.classify_concept_type("A Topic", "intermediate")
        _assert_default_arc_integrity(ct, default_applied)

    def test_llm_failure_applies_default(self, monkeypatch):
        monkeypatch.setattr(sp, "_client", lambda: _BoomClient())
        ct, default_applied = sp.classify_concept_type("A Topic", "intermediate")
        _assert_default_arc_integrity(ct, default_applied)
