"""Property-based test for best-round selection.

# Feature: content-hook-engagement, Property 20: Best round is the earliest highest-scoring round

For any non-empty list of round results, ``best_round`` returns a round whose
``coherence_score`` is maximal, and among rounds tied at the maximum it returns
the one with the smallest ``round_index``.

Validates: Requirements 4.7, 4.8
"""
from hypothesis import given, settings, strategies as st

from app.models.schemas import CoherenceResult
from app.services.coherence import best_round


# ---------------------------------------------------------------------------
# Smart generator — build a non-empty list of CoherenceResult with:
#   - random coherence_score in [0, 1] (2 dp, matching the schema contract),
#   - distinct round_index values drawn from a shuffled / non-contiguous pool
#     so ties and arbitrary ordering are both exercised.
# Distinct round_index values keep the tie-break target unambiguous while still
# covering shuffled, non-contiguous indices.
# ---------------------------------------------------------------------------

@st.composite
def _round_lists(draw):
    n = draw(st.integers(min_value=1, max_value=8))

    # Distinct, possibly non-contiguous round_index values, then shuffled.
    index_pool = draw(
        st.lists(
            st.integers(min_value=0, max_value=50),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    draw(st.randoms()).shuffle(index_pool)

    scores = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
            .map(lambda x: round(x, 2)),
            min_size=n,
            max_size=n,
        )
    )

    return [
        CoherenceResult(coherence_score=score, defects=[], round_index=idx)
        for score, idx in zip(scores, index_pool)
    ]


class TestBestRound:
    @settings(max_examples=100)
    @given(rounds=_round_lists())
    def test_best_round_is_earliest_highest(self, rounds):
        result = best_round(rounds)

        max_score = max(r.coherence_score for r in rounds)
        earliest_index_at_max = min(
            r.round_index for r in rounds if r.coherence_score == max_score
        )

        # The selected round has the maximal coherence_score (Req 4.7).
        assert result.coherence_score == max_score

        # Among rounds tied at the max, the smallest round_index wins (Req 4.8).
        assert result.round_index == earliest_index_at_max
