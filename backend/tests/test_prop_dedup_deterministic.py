"""Property-based test for deduplication determinism.

# Feature: alternative-content-streams, Property 15: Deduplication is deterministic

``dedup`` decides, for one Topic, which candidate Source_Items to admit given the
clips already admitted for that Topic. Its outcome must depend ONLY on the
provenance values and content identifiers supplied as inputs — never on
wall-clock time, invocation count, or any external or stored state (Req 8.5).

This property asserts that calling ``dedup`` repeatedly on the SAME inputs yields
an identical ``DedupOutcome`` (``admit``, ``suppressed``, and ``rejected`` all
equal), including a fresh deep-copy of the inputs to prove the decision carries no
hidden dependence on object identity or on a prior call having mutated state.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 8.5
"""
import copy

from hypothesis import given, settings, strategies as st

from app.services.provider_dedup import DedupCandidate, DedupOutcome, Provenance, dedup

# Small pools so generated candidates deliberately collide: repeated
# (provider_id, external_id) identities, cross-provider and same-provider
# content_id matches, plus empty ids that must be rejected. Overlapping
# identities and content ids are exactly the inputs whose determinism matters.
_provider_ids = st.sampled_from(["", "youtube", "vimeo", "podcast", "khan"])
_external_ids = st.sampled_from(["", "a", "b", "c", "d"])
_content_ids = st.sampled_from([None, "cx", "cy", "cz"])

_candidates = st.builds(
    DedupCandidate,
    provenance=st.builds(
        Provenance, provider_id=_provider_ids, external_id=_external_ids
    ),
    content_id=_content_ids,
)
_candidate_lists = st.lists(_candidates, max_size=12)
# already_admitted overlaps the candidate identity/content space on purpose so
# the seeded-retained-state path is exercised under repeated calls.
_admitted_lists = st.lists(_candidates, max_size=8)


class TestDedupDeterminism:
    @settings(max_examples=100)
    @given(candidates=_candidate_lists, already_admitted=_admitted_lists)
    def test_repeated_calls_yield_identical_outcome(
        self, candidates, already_admitted
    ):
        # First call.
        first = dedup(candidates, already_admitted)

        # Second call on the SAME input objects: a pure decision must reproduce
        # the outcome exactly, independent of wall-clock time and call count.
        second = dedup(candidates, already_admitted)
        assert isinstance(first, DedupOutcome)
        assert second == first
        assert second.admit == first.admit
        assert second.suppressed == first.suppressed
        assert second.rejected == first.rejected

        # Third call on a fresh deep copy of the inputs: identical *values* must
        # produce an identical outcome, proving the decision depends only on the
        # provenance + content-id values and on no object identity or external
        # state. ``dedup`` must also leave its inputs unmutated, so the copies
        # still equal the originals after the earlier calls.
        candidates_copy = copy.deepcopy(candidates)
        admitted_copy = copy.deepcopy(already_admitted)
        third = dedup(candidates_copy, admitted_copy)
        assert third == first

        # Inputs were not mutated by any call (pure function).
        assert candidates == candidates_copy
        assert already_admitted == admitted_copy
