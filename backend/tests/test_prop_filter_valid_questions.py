"""Property-based test for the checkpoint validation filter.

# Feature: adaptive-learning-game, Property 8: Only valid MCQs survive checkpoint generation

For any collection of candidate question dicts (valid and malformed mixed
arbitrarily), the kept set contains only questions that pass
``quiz._validate_question`` and excludes every candidate that fails it.

``quiz._validate_question`` is the oracle here: the filter's kept set must agree
with it exactly. We generate a mix of guaranteed-valid candidates and arbitrary
(largely malformed) candidates, then assert the filter's output corresponds
one-to-one and in order with the candidates the oracle accepts.

Validates: Requirements 11.4
"""
from hypothesis import given, settings, strategies as st

from app.services import quiz
from app.services.game import _filter_valid_questions

# Option/prompt text whose stripped form is always non-empty, so a generated
# "valid" candidate genuinely passes _validate_question (which trims and drops
# blank options). Avoids whitespace-only strings collapsing the option count.
_nonblank_text = st.text(min_size=1, max_size=40).filter(lambda s: s.strip() != "")

# A Concept_Tag / level the filter must preserve onto the validated question
# (Req 11.3) — validation itself drops these fields.
_concept_tags = st.text(min_size=1, max_size=24).filter(lambda s: s.strip() != "")
_levels = st.sampled_from(["prerequisite", "core", "stretch"])


@st.composite
def _valid_candidate(draw):
    """A candidate dict guaranteed to pass ``quiz._validate_question``.

    2-4 non-blank options, an in-range correct_index, and non-empty prompt +
    explanation. Optionally carries a concept_tag / level so preservation is
    exercised.
    """
    n_options = draw(st.integers(min_value=2, max_value=4))
    options = draw(
        st.lists(_nonblank_text, min_size=n_options, max_size=n_options)
    )
    candidate = {
        "question": draw(_nonblank_text),
        "options": options,
        "correct_index": draw(st.integers(min_value=0, max_value=n_options - 1)),
        "explanation": draw(_nonblank_text),
    }
    if draw(st.booleans()):
        candidate["concept_tag"] = draw(_concept_tags)
    if draw(st.booleans()):
        candidate["level"] = draw(_levels)
    return candidate


# Arbitrary / malformed candidates: non-dicts and dicts broken in the ways
# _validate_question rejects (missing/blank prompt, bad options, out-of-range
# correct_index). The oracle still drives the assertion, so any of these that
# happen to be valid are handled correctly too.
_malformed_candidate = st.one_of(
    st.none(),
    st.integers(),
    st.text(),
    st.lists(st.integers()),
    st.fixed_dictionaries({}),
    # Blank prompt / explanation.
    st.fixed_dictionaries(
        {
            "question": st.just("   "),
            "options": st.lists(_nonblank_text, min_size=2, max_size=4),
            "correct_index": st.just(0),
            "explanation": st.just(""),
        }
    ),
    # Options not a list, or wrong count (too few / too many).
    st.fixed_dictionaries(
        {
            "question": _nonblank_text,
            "options": st.one_of(
                st.text(),
                st.lists(_nonblank_text, min_size=0, max_size=1),
                st.lists(_nonblank_text, min_size=5, max_size=8),
            ),
            "correct_index": st.integers(min_value=0, max_value=3),
            "explanation": _nonblank_text,
        }
    ),
    # correct_index out of range or non-int.
    st.fixed_dictionaries(
        {
            "question": _nonblank_text,
            "options": st.lists(_nonblank_text, min_size=2, max_size=4),
            "correct_index": st.one_of(
                st.integers(min_value=5, max_value=50),
                st.integers(max_value=-1),
                st.text(),
                st.none(),
            ),
            "explanation": _nonblank_text,
        }
    ),
)

_candidate = st.one_of(_valid_candidate(), _malformed_candidate)
_candidates = st.lists(_candidate, min_size=0, max_size=12)


def _passes(candidate) -> bool:
    """Oracle: does the candidate pass the reused MCQ validation?"""
    return quiz._validate_question(candidate) is not None


class TestOnlyValidMcqsSurviveCheckpointGeneration:
    @settings(max_examples=200)
    @given(candidates=_candidates)
    def test_kept_set_is_exactly_those_passing_validation(self, candidates):
        kept = _filter_valid_questions(candidates)

        # The candidates the oracle accepts, in original order.
        passing = [c for c in candidates if _passes(c)]

        # Membership: the filter keeps exactly as many as pass validation —
        # nothing extra survives and nothing valid is dropped (Req 11.4).
        assert len(kept) == len(passing)

        # Correspondence: kept[i] is the validated form of the i-th passing
        # candidate, so the kept set is *exactly* those passing, in order.
        for kept_q, source in zip(kept, passing):
            expected = quiz._validate_question(source)
            assert kept_q["question"] == expected["question"]
            assert kept_q["options"] == expected["options"]
            assert kept_q["correct_index"] == expected["correct_index"]
            assert kept_q["explanation"] == expected["explanation"]

    @settings(max_examples=200)
    @given(candidates=_candidates)
    def test_every_kept_question_revalidates(self, candidates):
        # Every survivor itself passes validation: no malformed candidate
        # leaks through the filter (excludes every failing candidate, Req 11.4).
        kept = _filter_valid_questions(candidates)
        for kept_q in kept:
            assert quiz._validate_question(kept_q) is not None

    @settings(max_examples=200)
    @given(candidates=_candidates)
    def test_concept_tag_and_level_preserved_when_present(self, candidates):
        # Validation drops concept_tag / level; the filter must re-attach them
        # since downstream node quizzes tag each question by concept (Req 11.3).
        kept = _filter_valid_questions(candidates)
        passing = [c for c in candidates if _passes(c)]
        for kept_q, source in zip(kept, passing):
            if isinstance(source, dict) and source.get("concept_tag") is not None:
                assert kept_q["concept_tag"] == source["concept_tag"]
            if isinstance(source, dict) and source.get("level") is not None:
                assert kept_q["level"] == source["level"]

    def test_empty_input_returns_empty_list(self):
        assert _filter_valid_questions([]) == []

    def test_mixed_example_keeps_only_valid(self):
        # Explicit example: one valid MCQ between two malformed candidates.
        valid = {
            "question": "What does backtracking do on a dead end?",
            "options": ["Undo the last choice", "Restart from scratch"],
            "correct_index": 0,
            "explanation": "It reverts the last decision and tries another.",
            "concept_tag": "backtracking",
            "level": "core",
        }
        candidates = [
            {"question": "", "options": [], "correct_index": 0, "explanation": ""},
            valid,
            "not-a-dict",
        ]
        kept = _filter_valid_questions(candidates)
        assert len(kept) == 1
        assert kept[0]["question"] == valid["question"]
        assert kept[0]["concept_tag"] == "backtracking"
