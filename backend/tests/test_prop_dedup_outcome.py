"""Property-based test for the cross-provider deduplication outcome.

# Feature: alternative-content-streams, Property 14: Cross-provider deduplication outcome

``dedup`` decides, for one Topic, which candidate Source_Items to admit given the
clips already admitted for that Topic. This property exercises the three outcome
rules the requirements pin down:

  - At most one Clip is admitted per ``(provider_id, external_id)`` identity per
    Topic (Req 8.2).
  - A candidate whose ``content_id`` matches an already-admitted clip from a
    DIFFERENT provider is suppressed, the suppression records BOTH provenances,
    and the previously admitted clip is retained (Req 8.3, 8.4).
  - A candidate lacking a non-empty ``provider_id`` or ``external_id`` is rejected
    and admits no Clip (Req 8.6).

It also asserts that ``admit``, ``suppressed``, and ``rejected`` partition the
input candidates (every candidate lands in exactly one bucket).

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 8.2, 8.3, 8.4, 8.6
"""
from collections import Counter

from hypothesis import assume, given, settings, strategies as st

from app.services.provider_dedup import DedupCandidate, Provenance, dedup

# Small pools so generated candidates deliberately collide: repeated
# (provider_id, external_id) identities, cross-provider and same-provider
# content_id matches, plus empty ids that must be rejected.
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
# already_admitted may itself carry incomplete provenance; the core ignores those
# entries for seeding, so feeding them in exercises that path too.
_admitted_lists = st.lists(_candidates, max_size=8)

# Non-empty provider/external ids for the targeted cross-provider scenario.
_nonempty_provider = st.sampled_from(["youtube", "vimeo", "podcast", "khan"])
_nonempty_external = st.sampled_from(["a", "b", "c", "d"])


def _complete(candidate: DedupCandidate) -> bool:
    """A candidate is admissible only with a non-empty provider AND external id."""
    prov = candidate.provenance
    return bool(prov.provider_id) and bool(prov.external_id)


class TestDedupOutcome:
    @settings(max_examples=100)
    @given(candidates=_candidate_lists, already_admitted=_admitted_lists)
    def test_partition_identity_uniqueness_and_rejection(
        self, candidates, already_admitted
    ):
        outcome = dedup(candidates, already_admitted)

        suppressed_cands = [cand for cand, _retained in outcome.suppressed]

        # --- Partition: counts add up and every input lands in exactly one bucket.
        assert (
            len(outcome.admit)
            + len(outcome.suppressed)
            + len(outcome.rejected)
            == len(candidates)
        )
        combined = (
            Counter(outcome.admit)
            + Counter(suppressed_cands)
            + Counter(outcome.rejected)
        )
        assert combined == Counter(candidates)

        # --- Rejection: incomplete candidates are rejected (admit none), and the
        # rejected bucket holds only incomplete candidates (Req 8.6).
        for cand in candidates:
            if not _complete(cand):
                assert cand in outcome.rejected
        for cand in outcome.rejected:
            assert not _complete(cand)

        # --- At most one admit per (provider_id, external_id) identity (Req 8.2),
        # and an admitted identity never collides with an already-admitted one.
        admitted_identities = [
            (c.provenance.provider_id, c.provenance.external_id)
            for c in outcome.admit
        ]
        assert len(admitted_identities) == len(set(admitted_identities))

        prior_identities = {
            (c.provenance.provider_id, c.provenance.external_id)
            for c in already_admitted
            if _complete(c)
        }
        for identity in admitted_identities:
            assert identity not in prior_identities

        # --- Every suppression records BOTH provenances (Req 8.4): the suppressed
        # candidate keeps its own complete provenance, and the retained provenance
        # belongs to a clip that is actually retained (already admitted or admitted
        # in this call) — i.e. the prior clip is retained, never dropped.
        retained_pool = {c.provenance for c in already_admitted if _complete(c)}
        retained_pool |= {c.provenance for c in outcome.admit}
        for cand, retained in outcome.suppressed:
            assert _complete(cand)
            assert cand.provenance.provider_id and cand.provenance.external_id
            assert retained.provider_id and retained.external_id
            assert retained in retained_pool

    @settings(max_examples=100)
    @given(
        provider_a=_nonempty_provider,
        provider_b=_nonempty_provider,
        external_a=_nonempty_external,
        external_b=_nonempty_external,
        content_id=st.sampled_from(["cx", "cy", "cz"]),
    )
    def test_cross_provider_content_match_against_admitted_is_suppressed(
        self, provider_a, provider_b, external_a, external_b, content_id
    ):
        # A candidate sharing a content_id with an already-admitted clip from a
        # DIFFERENT provider must be suppressed, recording both provenances, and
        # the prior clip retained (Req 8.3, 8.4).
        assume(provider_a != provider_b)

        admitted = DedupCandidate(Provenance(provider_a, external_a), content_id)
        candidate = DedupCandidate(Provenance(provider_b, external_b), content_id)

        outcome = dedup([candidate], [admitted])

        # Candidate is suppressed, admits no Clip, and the suppression names BOTH
        # the candidate's provenance and the retained (prior) clip's provenance.
        assert outcome.admit == []
        assert outcome.rejected == []
        assert (candidate, admitted.provenance) in outcome.suppressed
