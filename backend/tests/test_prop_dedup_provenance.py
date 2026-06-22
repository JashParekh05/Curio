"""Property-based test for complete provenance on admitted candidates.

# Feature: alternative-content-streams, Property 16: Admitted clips carry complete provenance

When the Curio_System admits a Clip it must persist a Provider_Provenance record
containing a non-empty Provider_Id AND a non-empty external item identifier, so
that the originating Content_Provider is retrievable for every admitted Clip
(Req 8.1). ``dedup`` is the pure core that decides which candidate Source_Items
are admitted for a Topic; every candidate it places in ``DedupOutcome.admit``
must therefore carry complete provenance.

This property generates candidate lists deliberately mixing complete and
incomplete provenance (empty provider ids and empty external ids), plus an
already-admitted list spanning the same identity/content space, and asserts that
EVERY candidate in ``outcome.admit`` has a non-empty ``provider_id`` and a
non-empty ``external_id``. Candidates lacking either identifier must instead be
rejected, never admitted.

Imports only the pure module under test so it runs offline with no external
service (evidence for Req 9.5).

Validates: Requirements 8.1
"""
from hypothesis import given, settings, strategies as st

from app.services.provider_dedup import DedupCandidate, DedupOutcome, Provenance, dedup

# Small pools so generated candidates mix complete and incomplete provenance:
# the empty string for either id models the missing-identifier case that must be
# rejected (Req 8.6), while the non-empty values model admissible candidates.
# Overlapping identities and content ids exercise the same-identity collapse and
# cross-provider suppression paths so admits are produced under realistic input.
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
_admitted_lists = st.lists(_candidates, max_size=8)


class TestAdmittedProvenanceComplete:
    @settings(max_examples=100)
    @given(candidates=_candidate_lists, already_admitted=_admitted_lists)
    def test_every_admitted_candidate_has_complete_provenance(
        self, candidates, already_admitted
    ):
        outcome = dedup(candidates, already_admitted)
        assert isinstance(outcome, DedupOutcome)

        # Every admitted candidate must carry complete provenance so the
        # originating provider is retrievable for the resulting Clip (Req 8.1):
        # a non-empty provider_id AND a non-empty external_id.
        for admitted in outcome.admit:
            assert admitted.provenance.provider_id, (
                "admitted candidate has empty provider_id: " f"{admitted!r}"
            )
            assert admitted.provenance.external_id, (
                "admitted candidate has empty external_id: " f"{admitted!r}"
            )
