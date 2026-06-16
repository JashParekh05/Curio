"""Property-based test for accepted-atom invariants.

# Feature: content-hook-engagement, Property 4: Accepted Learning_Atoms satisfy all label and timestamp bounds

For any raw candidate atom dict and transcript duration, if ``validate_atom``
accepts it (returns ``(atom, None)``) then ALL of the following hold on the
returned atom:
  - ``role`` is one defined ``PedagogicalRole`` value;
  - ``concept`` is non-empty with length 1-200;
  - ``prior_knowledge`` is a set of 0-50 distinct concepts, none equal to the
    covered concept;
  - ``0 <= start < end <= transcript_duration``;
  - ``3 <= end - start <= 90``.

Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5
"""
from typing import get_args

from hypothesis import given, settings, strategies as st

from app.services.pipeline import validate_atom
from app.models.schemas import PedagogicalRole

_VALID_ROLES = list(get_args(PedagogicalRole))

# ---------------------------------------------------------------------------
# Smart generators — span the input space with a healthy mix of valid and
# invalid roles, concept lengths, prior_knowledge shapes, and timestamps.
# ---------------------------------------------------------------------------

# role: valid literals plus a variety of invalid values.
_role = st.one_of(
    st.sampled_from(_VALID_ROLES),
    st.text(max_size=20),            # arbitrary (mostly invalid) strings
    st.none(),
    st.integers(),
    st.just(""),
)

# concept: empty, blank, normal, and boundary/overlong lengths.
_concept = st.one_of(
    st.text(max_size=10),
    st.text(min_size=1, max_size=200),
    st.text(min_size=201, max_size=260),   # overlong -> rejected
    st.just(""),
    st.just("   "),                         # blank after strip -> rejected
    st.none(),
    st.integers(),
)

# prior_knowledge: lists with duplicates, blanks, overlong, non-strings, plus
# valid small lists. Also non-list values to exercise the type guard.
_pk_item = st.one_of(st.text(max_size=12), st.just(""), st.just("dup"))
_prior_knowledge = st.one_of(
    st.lists(_pk_item, max_size=8),
    st.lists(st.text(min_size=1, max_size=8), min_size=51, max_size=60),  # overlong
    st.lists(st.one_of(st.text(max_size=6), st.integers()), max_size=6),  # mixed types
    st.none(),
    st.text(max_size=10),  # wrong type entirely
)

# timestamps: include negatives, zero, equal, reversed, and large values so we
# exercise start<0, end<=start, end>duration, and duration bound violations.
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


class TestAcceptedAtomInvariants:
    @settings(max_examples=100)
    @given(raw=_raw_atom(), duration=_transcript_duration)
    def test_accepted_atoms_satisfy_all_bounds(self, raw, duration):
        atom, reason = validate_atom(raw, duration)

        if atom is None:
            # Rejected candidates must carry a reason and produce no atom.
            assert reason is not None
            return

        # Accepted -> reason is None and every invariant holds.
        assert reason is None

        # Req 2.2: role is exactly one defined PedagogicalRole value.
        assert atom.role in _VALID_ROLES

        # Req 2.3: concept non-empty, length 1-200.
        assert isinstance(atom.concept, str)
        assert 1 <= len(atom.concept) <= 200
        assert atom.concept.strip() == atom.concept  # normalized (no surrounding ws)
        assert atom.concept != ""

        # Req 2.4: prior_knowledge is a set of 0-50 distinct concepts, none
        # equal to the covered concept.
        assert isinstance(atom.prior_knowledge, list)
        assert len(atom.prior_knowledge) <= 50
        assert len(set(atom.prior_knowledge)) == len(atom.prior_knowledge)  # distinct
        assert atom.concept not in atom.prior_knowledge

        # Req 2.5: 0 <= start < end <= duration.
        assert atom.start >= 0
        assert atom.start < atom.end
        assert atom.end <= duration

        # Req 2.1: 3 <= end - start <= 90.
        span = atom.end - atom.start
        assert 3.0 <= span <= 90.0
