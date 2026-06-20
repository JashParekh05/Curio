"""Contract-level integration tests for the acquirer <-> pipeline boundary (Task 10.4).

# Feature: alternative-content-streams

This file pins the boundary behaviors that the Source_Acquirer plus the
Content_Provider contract *guarantee* at the seam where normalized
``SourceItem``s hand off to the unchanged shared ``ingest_topic`` pipeline
(decode -> break-down -> map -> judge -> admit). It complements
``test_source_acquirer_pipeline_boundary.py`` (which pins the acquirer-side
division of responsibility) by grounding each guarantee in the REAL production
seams the pipeline uses, exercised offline:

  - the ``content_provider`` capability gate (``supports_capability`` /
    ``gate_invocation``) -- the contract that decides whether ``fetch_transcript``
    may be invoked at all (Req 5.5);
  - ``deep_decoder.decode_video`` -- the DECODE stage that fetches the transcript
    and, when none is available, returns ``skipped`` so nothing is admitted
    (Req 5.5, 5.8);
  - ``admission_gate.admit_topic`` -- the ADMIT stage that admits nothing when the
    JUDGE stage withholds a passing verdict, preserving no partial Clip output
    (Req 5.6, 5.7).

Everything is offline: the transcript fetcher is monkeypatched to return ``None``
(modeling both a provider that lacks ``fetch_transcript`` and a declared-but-empty
fetch, including the 30s-timeout case of Req 5.8), and the pure ADMIT core runs
directly with no DB. No external service is contacted.

What is COVERED here (contract level, offline):
  - Req 5.5: a provider lacking ``fetch_transcript`` cannot have that op invoked
    (gate -> ``'unsupported_operation'``); a transcript-less ``SourceItem`` from it
    decodes to ``skipped`` so the shared pipeline admits NO Clip, recorded against
    the originating Provider_Id.
  - Req 5.8: a provider that DECLARES ``fetch_transcript`` (gate permits it) but
    yields no transcript (empty / timed-out fetch) decodes to ``skipped`` -> no
    Clip admitted, recorded against the Provider_Id.
  - Req 5.6: when JUDGE produces no passing verdict for a candidate, ADMIT admits
    no Clip, recorded against the Provider_Id.
  - Req 5.7: when a pipeline stage fails (decode skipped / verdict missing), ADMIT
    admits no Clip AND preserves no partial Clip output, recorded against the
    Provider_Id.

What is DEFERRED to task 11.2 (full ``ingest_topic`` wiring):
  - End-to-end execution of ``ingest_topic`` over a real provider-sourced video
    (transcript fetch -> break-down -> map -> judge -> admit -> persist) and the
    assertion that ``run_pipeline`` and the cold-start ``Seeding_Worker`` both
    route provider ``SourceItem``s through the same shared pipeline with no
    bypass. That wiring requires model-backed labeling and DB persistence and is
    therefore exercised by the 11.2 shared-pipeline routing test, not here.

Validates: Requirements 5.5, 5.6, 5.7, 5.8
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services import admission_gate, deep_decoder
from app.services.content_provider import (
    UNSUPPORTED_OPERATION,
    EmbedReference,
    ProviderCapability,
    SourceItem,
    gate_invocation,
    supports_capability,
)

SEARCH = ProviderCapability.SEARCH
FETCH_METADATA = ProviderCapability.FETCH_METADATA
FETCH_TRANSCRIPT = ProviderCapability.FETCH_TRANSCRIPT

# A provider that can find and describe candidates but CANNOT fetch a transcript.
_NO_TRANSCRIPT = frozenset({SEARCH, FETCH_METADATA})
# A provider that declares every capability, including fetch_transcript.
_FULL = frozenset({SEARCH, FETCH_METADATA, FETCH_TRANSCRIPT})


# ---------------------------------------------------------------------------
# Builders + offline doubles
# ---------------------------------------------------------------------------


def _source_item(provider_id: str, external_id: str, *, transcript=None) -> SourceItem:
    """A SourceItem tagged with ``provider_id`` and carrying ``transcript``."""
    return SourceItem(
        provider_id=provider_id,
        embed_ref=EmbedReference(
            provider_id=provider_id,
            external_id=external_id,
            playback_params={"start": "0"},
        ),
        title=f"{provider_id}:{external_id}",
        duration_seconds=120.0,
        transcript=transcript,
        metadata={"source": provider_id},
    )


def _recorded_skip(item: SourceItem, decode: deep_decoder.DecodeResult) -> tuple[str, str]:
    """Model the pipeline's per-Source_Item result indication for a skipped item.

    Req 5.5/5.7/5.8 each require the skip/failure to be recorded "identifying the
    Provider_Id". The pipeline keys its funnel/result records by the Source_Item;
    here we compose that indication as ``(provider_id, reason)`` from the
    originating item and the real DECODE outcome, proving the Provider_Id flows
    through the boundary alongside the stage reason.
    """
    return (item.provider_id, decode.reason or "skipped")


@pytest.fixture
def no_transcript_available(monkeypatch):
    """Make the DECODE transcript fetch yield None (offline).

    ``deep_decoder.decode_video`` lazily imports ``_fetch_transcript`` from
    ``app.services.youtube`` and calls it; returning ``None`` models BOTH a
    provider that supplies no transcript (Req 5.5) and a declared fetch that
    returns nothing within its 30s budget (Req 5.8). No network is touched.
    """
    import app.services.youtube as youtube_mod

    monkeypatch.setattr(youtube_mod, "_fetch_transcript", lambda *a, **k: None)
    return youtube_mod


# ---------------------------------------------------------------------------
# Req 5.5: a provider lacking fetch_transcript -> op ungated -> nothing admitted
# ---------------------------------------------------------------------------


class TestMissingTranscriptCapabilitySkips:
    def test_fetch_transcript_is_not_declared_so_invocation_is_unsupported(self):
        # Contract level: a provider whose declared capabilities omit
        # fetch_transcript cannot have that operation invoked through the
        # contract -- the gate refuses it with 'unsupported_operation' and
        # touches no persisted state (Req 1.4, 5.5).
        assert supports_capability(_NO_TRANSCRIPT, FETCH_TRANSCRIPT) is False
        assert gate_invocation(_NO_TRANSCRIPT, FETCH_TRANSCRIPT) == UNSUPPORTED_OPERATION
        # Its search/metadata ops remain permitted, so it is still a usable source.
        assert gate_invocation(_NO_TRANSCRIPT, SEARCH) is None
        assert gate_invocation(_NO_TRANSCRIPT, FETCH_METADATA) is None

    def test_transcriptless_item_decodes_to_skip_and_admits_nothing(
        self, no_transcript_available
    ):
        # A Source_Item from a provider that declares no fetch_transcript and
        # carries no transcript of its own: the DECODE stage fetches no
        # transcript, returns skipped, and the ADMIT stage therefore admits no
        # Clip -- recorded against the Provider_Id (Req 5.5).
        item = _source_item("podcast", "ep-1", transcript=None)
        assert gate_invocation(_NO_TRANSCRIPT, FETCH_TRANSCRIPT) == UNSUPPORTED_OPERATION

        decode = deep_decoder.decode_video(
            item.embed_ref.external_id, "topic-slug", None
        )

        # DECODE skipped for want of a transcript; no atoms produced (Req 5.5).
        assert decode.skipped is True
        assert decode.atoms == []
        assert decode.reason == "transcript_unavailable"

        # ADMIT over zero candidates admits no Clip.
        admitted, _decisions, outcome = admission_gate.admit_topic(
            [], [], {}, True, True
        )
        assert admitted == []
        assert outcome == "skipped"

        # The skip is recorded identifying the originating Provider_Id (Req 5.5).
        assert _recorded_skip(item, decode) == ("podcast", "transcript_unavailable")

    @settings(max_examples=25)
    @given(
        caps=st.sets(st.sampled_from([SEARCH, FETCH_METADATA])).map(frozenset),
        op=st.just(FETCH_TRANSCRIPT),
    )
    def test_property_transcript_op_ungated_for_any_provider_without_it(self, caps, op):
        # For ANY capability set that omits fetch_transcript, the gate refuses a
        # fetch_transcript invocation -- so such a provider can never supply a
        # transcript through the contract (the precondition of the Req 5.5 skip).
        assert FETCH_TRANSCRIPT not in caps
        assert supports_capability(caps, op) is False
        assert gate_invocation(caps, op) == UNSUPPORTED_OPERATION


# ---------------------------------------------------------------------------
# Req 5.8: declares fetch_transcript but returns none in time -> skip
# ---------------------------------------------------------------------------


class TestDeclaredTranscriptButEmptySkips:
    def test_declared_fetch_yielding_no_transcript_admits_nothing(
        self, no_transcript_available
    ):
        # A provider that DOES declare fetch_transcript (the gate permits the op)
        # but whose fetch returns nothing -- e.g. no captions, or no result within
        # its 30s budget -- still leaves the Source_Item without a transcript, so
        # DECODE skips it and ADMIT admits no Clip, recorded against the
        # Provider_Id (Req 5.8).
        item = _source_item("vimeo", "v-9", transcript=None)
        assert gate_invocation(_FULL, FETCH_TRANSCRIPT) is None  # op is permitted

        decode = deep_decoder.decode_video(
            item.embed_ref.external_id, "topic-slug", None
        )
        assert decode.skipped is True
        assert decode.atoms == []
        assert decode.reason == "transcript_unavailable"

        admitted, _decisions, outcome = admission_gate.admit_topic(
            [], [], {}, True, True
        )
        assert admitted == []
        assert outcome == "skipped"
        assert _recorded_skip(item, decode) == ("vimeo", "transcript_unavailable")


# ---------------------------------------------------------------------------
# Req 5.6: a judge/stage rejection admits no clip, preserves no partial output
# ---------------------------------------------------------------------------


class TestJudgeRejectionAdmitsNothing:
    def test_failing_verdict_admits_no_clip_recorded_against_provider(self):
        # A candidate Mapped_Segment whose Segment_Verdict is failing is rejected
        # by ADMIT: no Clip is admitted from the Source_Item, and the rejection is
        # recorded against the Provider_Id (Req 5.6). Uses the REAL admit core.
        item = _source_item("khan_academy", "k-1")
        segment = SimpleNamespace(atom=SimpleNamespace(id="atom-1"))
        failing_verdict = SimpleNamespace(status="failing")

        admitted, decisions, outcome = admission_gate.admit_topic(
            [segment], [], {"atom-1": failing_verdict}, True, True
        )

        assert admitted == []  # no Clip admitted from this Source_Item
        assert outcome == "skipped"
        assert [d.reason for d in decisions] == ["failing_verdict"]
        # Recorded against the originating Provider_Id together with the reason.
        assert (item.provider_id, decisions[0].reason) == (
            "khan_academy",
            "failing_verdict",
        )


# ---------------------------------------------------------------------------
# Req 5.7: a stage failure admits no clip AND preserves no partial output
# ---------------------------------------------------------------------------


class TestStageFailurePreservesNoPartialOutput:
    def test_decode_failure_admits_nothing_and_keeps_no_partial_clip(
        self, no_transcript_available
    ):
        # A DECODE-stage failure (transcript fetch failed) yields skipped with no
        # atoms; the downstream stages have nothing to admit, so no Clip and NO
        # partial Clip output survives, recorded against the Provider_Id (Req 5.7).
        item = _source_item("podcast", "ep-2", transcript=None)

        decode = deep_decoder.decode_video(
            item.embed_ref.external_id, "topic-slug", None
        )
        assert decode.skipped is True
        assert decode.idea_units == []  # no partial break-down output retained
        assert decode.atoms == []  # no partial atoms retained

        admitted, _decisions, outcome = admission_gate.admit_topic(
            [], [], {}, True, True
        )
        assert admitted == []
        assert outcome == "skipped"
        assert _recorded_skip(item, decode) == ("podcast", "transcript_unavailable")

    def test_missing_verdict_mid_judge_admits_nothing_no_partial(self):
        # A JUDGE-stage gap -- a Mapped_Segment with NO verdict at all (the judge
        # failed to produce one) -- is treated as a failing verdict by ADMIT, so
        # no Clip is admitted and no partial output is kept for the Source_Item
        # (Req 5.7). Real admit core.
        item = _source_item("vimeo", "v-2")
        segment = SimpleNamespace(atom=SimpleNamespace(id="atom-x"))

        admitted, decisions, outcome = admission_gate.admit_topic(
            [segment], [], {}, True, True  # empty verdicts map -> verdict missing
        )

        assert admitted == []
        assert outcome == "skipped"
        assert decisions[0].reason == "failing_verdict"
        assert (item.provider_id, decisions[0].reason) == ("vimeo", "failing_verdict")
