"""Property-based test for batch atom validation include/exclude semantics.

# Feature: content-hook-engagement, Property 5: Atom batch validation excludes the invalid and preserves the valid

For any batch of raw candidate atom dicts, validating each candidate with
``validate_atom`` partitions the batch cleanly:
  - every candidate that fails validation is absent from the accepted output
    and carries a recorded exclusion reason naming the specific
    missing/invalid label;
  - every candidate that passes validation is present in the accepted output
    with no exclusion reason.

There is no separate batch entry point besides ``segment_into_atoms`` (which
calls the LLM), so the batch semantics are exercised by mapping
``validate_atom`` over a generated list of raw dicts and asserting the
partition invariant holds for the whole list.

Validates: Requirements 2.7, 7.1
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.services.pipeline import validate_atom
from app.models.schemas import PedagogicalRole

_VALID_ROLES = list(get_args(PedagogicalRole))

# ---------------------------------------------------------------------------
# Smart generators — produce a healthy mix of valid and invalid candidates so
# any non-trivial batch contains both accepted and rejected atoms.
# ---------------------------------------------------------------------------

_role = st.one_of(
    st.sampled_from(_VALID_ROLES),
    st.text(max_size=20),
    st.none(),
    st.integers(),
    st.just(""),
)

_concept = st.one_of(
    st.text(max_size=10),
    st.text(min_size=1, max_size=200),
    st.text(min_size=201, max_size=260),  # overlong -> rejected
    st.just(""),
    st.just("   "),                       # blank after strip -> rejected
    st.none(),
    st.integers(),
)

_pk_item = st.one_of(st.text(max_size=12), st.just(""), st.just("dup"))
_prior_knowledge = st.one_of(
    st.lists(_pk_item, max_size=8),
    st.lists(st.text(min_size=1, max_size=8), min_size=51, max_size=60),  # overlong
    st.lists(st.one_of(st.text(max_size=6), st.integers()), max_size=6),  # mixed types
    st.none(),
    st.text(max_size=10),  # wrong type entirely
)

_ts = st.one_of(
    st.floats(min_value=-50, max_value=200, allow_nan=False, allow_infinity=False),
    st.integers(min_value=-50, max_value=200),
    st.none(),
)

_transcript_duration = st.floats(
    min_value=0.0, max_value=300.0, allow_nan=False, allow_infinity=False
)


@st.composite
def _raw_atom(draw):
    raw = {
        "role": draw(_role),
        "concept": draw(_concept),
        "prior_knowledge": draw(_prior_knowledge),
        "start": draw(_ts),
        "end": draw(_ts),
    }
    # Occasionally drop start/end keys entirely to hit the KeyError path.
    if draw(st.booleans()) and draw(st.integers(0, 4)) == 0:
        raw.pop("start", None)
    if draw(st.booleans()) and draw(st.integers(0, 4)) == 0:
        raw.pop("end", None)
    return raw


# A batch is a list of raw candidate dicts (possibly empty).
_batch = st.lists(_raw_atom(), max_size=12)


class TestAtomBatchValidation:
    @settings(max_examples=100)
    @given(batch=_batch, duration=_transcript_duration)
    def test_batch_excludes_invalid_preserves_valid(self, batch, duration):
        results = [validate_atom(raw, duration) for raw in batch]

        accepted = [atom for atom, reason in results if atom is not None]
        rejected = [reason for atom, reason in results if atom is None]

        for atom, reason in results:
            # Each candidate lands in exactly one partition: either it is
            # accepted (atom present, no reason) or rejected (no atom, with a
            # non-empty reason naming the specific missing/invalid label).
            accepted_case = atom is not None and reason is None
            rejected_case = atom is None and isinstance(reason, str) and reason != ""
            assert accepted_case ^ rejected_case, (
                f"candidate violated the partition invariant: "
                f"atom={atom!r}, reason={reason!r}"
            )

        # The two partitions account for every candidate, none double-counted.
        assert len(accepted) + len(rejected) == len(batch)

        # Every rejection reason is a non-empty descriptive string (Req 7.1).
        for reason in rejected:
            assert isinstance(reason, str) and reason.strip() != ""

        # Every accepted atom is a real LearningAtom present in the output.
        for atom in accepted:
            assert atom is not None
            assert atom.role in _VALID_ROLES
