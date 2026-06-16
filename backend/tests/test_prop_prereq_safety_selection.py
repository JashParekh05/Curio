"""Property-based test for prerequisite safety under engagement selection.

# Feature: content-hook-engagement, Property 29: Selection never violates prerequisite ordering for engagement

For any set of candidate orderings, the ordering returned by ``pick_ordering``
always satisfies the prerequisite-precedence invariant: the ``role_ordinal``
sequence of its clips is non-decreasing.  Any candidate that places a
prerequisite-teaching atom after a dependent — i.e. whose ``role_ordinal``
sequence decreases somewhere — is excluded from selection regardless of how
high its engagement is.

To exercise the rule we give the *invalid* candidates a very high first-clip
``engagement_score`` and we keep every candidate's coherence score within
0.001 of the others (so coherence cannot decide and engagement would, if it
were ever allowed to pick an invalid ordering).

Validates: Requirements 6.3, 6.4
"""
import uuid

from hypothesis import given, settings, strategies as st

from app.models.schemas import Clip
from app.services.arc_assembler import pick_ordering


def _clip(role_ordinal: int, engagement: float) -> Clip:
    """Build a minimal valid Clip carrying a role_ordinal and engagement score."""
    return Clip(
        id=f"clip-{uuid.uuid4().hex[:12]}",
        topic_slug="topic-slug",
        title="Title",
        description="Description",
        video_url="https://example.com/video",
        role_ordinal=role_ordinal,
        engagement_score=engagement,
    )


def _non_decreasing(ordinals: list[int]) -> bool:
    return all(ordinals[i] <= ordinals[i + 1] for i in range(len(ordinals) - 1))


@st.composite
def _valid_ordering(draw) -> Clip:
    """A candidate ordering whose role_ordinal sequence is non-decreasing.

    Built by drawing a sorted list of ordinals so the prerequisite-precedence
    invariant holds by construction.  Engagement is kept modest so that an
    invalid high-engagement rival, if it were ever eligible, would out-rank it.
    """
    n = draw(st.integers(min_value=1, max_value=5))
    ordinals = sorted(draw(st.lists(st.integers(min_value=1, max_value=8), min_size=n, max_size=n)))
    return [_clip(o, draw(st.floats(min_value=0.0, max_value=0.3))) for o in ordinals]


@st.composite
def _invalid_ordering(draw) -> Clip:
    """A candidate ordering with a decreasing step (prerequisite violation).

    The first clip is given a high engagement score so that, were the selector
    to ignore the precedence rule, this ordering would win the tiebreaker.
    """
    # Build a base non-decreasing sequence of length >= 2, then force a
    # decreasing step somewhere so the ordinal sequence is guaranteed invalid.
    n = draw(st.integers(min_value=2, max_value=5))
    ordinals = [draw(st.integers(min_value=2, max_value=8)) for _ in range(n)]
    # Pick a position and make it strictly less than its predecessor.
    pos = draw(st.integers(min_value=1, max_value=n - 1))
    ordinals[pos] = draw(st.integers(min_value=0, max_value=ordinals[pos - 1] - 1))

    assert not _non_decreasing(ordinals)  # sanity: this generator is invalid

    # First clip gets a high engagement score (the lure).
    high_engagement = draw(st.floats(min_value=0.9, max_value=1.0))
    clips = [_clip(ordinals[0], high_engagement)]
    clips += [_clip(o, draw(st.floats(min_value=0.0, max_value=1.0))) for o in ordinals[1:]]
    return clips


@st.composite
def _candidate_set(draw):
    """Generate a mix of valid and invalid candidate orderings plus near-equal
    coherence scores (all within 0.001 of each other so engagement decides).

    At least one valid and at least one invalid candidate are guaranteed."""
    n_valid = draw(st.integers(min_value=1, max_value=3))
    n_invalid = draw(st.integers(min_value=1, max_value=3))

    valids = [draw(_valid_ordering()) for _ in range(n_valid)]
    invalids = [draw(_invalid_ordering()) for _ in range(n_invalid)]

    candidates = valids + invalids
    # Shuffle so valid/invalid orderings are interleaved arbitrarily.
    order = draw(st.permutations(list(range(len(candidates)))))
    candidates = [candidates[i] for i in order]

    # Coherence scores all within 0.001 of a base value -> engagement decides.
    base = draw(st.floats(min_value=0.0, max_value=1.0))
    coherence_scores = [
        base + draw(st.floats(min_value=-0.0005, max_value=0.0005))
        for _ in candidates
    ]
    return candidates, coherence_scores


class TestPrereqSafetyUnderSelection:
    @settings(max_examples=100)
    @given(data=_candidate_set())
    def test_selected_ordering_never_violates_prerequisite_precedence(self, data):
        candidates, coherence_scores = data

        result = pick_ordering(candidates, coherence_scores)

        if result is None:
            return  # No selection made — vacuously safe.

        ordinals = [
            clip.role_ordinal if clip.role_ordinal is not None else 0
            for clip in result
        ]
        assert _non_decreasing(ordinals), (
            f"pick_ordering returned an ordering that violates prerequisite "
            f"precedence: role_ordinal sequence {ordinals} is not non-decreasing"
        )
