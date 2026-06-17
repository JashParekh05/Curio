"""Integration tests for ``ingest_topic`` Fast_Preview reconciliation (Task 9.3).

After Full_Processing the orchestrator reconciles feed-eligibility so that ONLY
Admitted_Clips remain feed-eligible and un-admitted Fast_Preview candidates are
dropped (Req 6.4, 6.5). When NOTHING is admitted, reconciliation is skipped
entirely so existing feed-eligible previews are retained for a later retry
(Req 6.7).

These tests mock the component calls in the ``ingestion_pipeline`` namespace
(``from app.services import admission_gate, deep_decoder, ingestion_store,
segment_judge, segment_mapper, youtube``) plus the locally-imported
``plan_and_store_arc`` and the clip-id read-back helper, so the run is fully
deterministic and offline.

Validates: Requirements 6.4, 6.5, 6.7
"""
from types import SimpleNamespace

from app.services import ingestion_pipeline
import app.agents.section_planner as section_planner


def _atom(atom_id):
    return SimpleNamespace(id=atom_id)


def _mapped_segment(atom_id):
    return SimpleNamespace(atom=_atom(atom_id))


def _assembly(mapped, unmapped=None, coherence_score=0.9):
    """A stub AssemblyOutcome: coherent + aligned, with the given mapped/unmapped."""
    return SimpleNamespace(
        mapping=SimpleNamespace(mapped=mapped, unmapped=unmapped or []),
        clips=[],
        coherence=SimpleNamespace(defects=[], coherence_score=coherence_score),
        alignment=SimpleNamespace(aligned=True, unresolved=[]),
        arc_missing=False,
    )


def _passing_verdict(*a, **k):
    return SimpleNamespace(
        quality_score=0.9, fit="belongs", status="passing", reason="ok"
    )


def _install_pipeline(
    monkeypatch,
    *,
    mapped,
    admitted,
    stored,
    clip_ids,
    feed_calls,
):
    """Wire all component calls ingest_topic makes for a single Source_Video.

    ``feed_calls`` is the list every set_feed_eligibility call appends to, so a
    test can assert exactly which ids the orchestrator reconciled to.
    """
    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store,
        "topic_already_ingested",
        lambda topic_slug: False,
    )
    monkeypatch.setattr(
        section_planner, "plan_and_store_arc", lambda *a, **k: object()
    )
    monkeypatch.setattr(
        ingestion_pipeline.youtube,
        "youtube_search",
        lambda *a, **k: [{"video_id": "vid1"}],
    )
    monkeypatch.setattr(
        ingestion_pipeline.deep_decoder,
        "decode_video",
        lambda *a, **k: SimpleNamespace(
            video_id="vid1",
            idea_units=[object()],
            atoms=[object()],
            skipped=False,
            reason=None,
        ),
    )
    monkeypatch.setattr(
        ingestion_pipeline.segment_mapper,
        "map_and_assemble",
        lambda *a, **k: _assembly(mapped),
    )
    monkeypatch.setattr(
        ingestion_pipeline.segment_judge, "judge_segment", _passing_verdict
    )
    monkeypatch.setattr(
        ingestion_pipeline.admission_gate,
        "admit_topic",
        lambda *a, **k: (admitted, [], "fully_admitted" if admitted else "skipped"),
    )
    monkeypatch.setattr(
        ingestion_pipeline.admission_gate,
        "persist_admitted",
        lambda *a, **k: stored,
    )
    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store, "record_funnel", lambda *a, **k: None
    )
    # The Topic's persisted clip ids (this path persists only Admitted_Clips).
    monkeypatch.setattr(
        ingestion_pipeline,
        "_collect_topic_clip_ids",
        lambda topic_slug, limit=None: set(clip_ids),
    )

    def _record_feed(topic_slug, ids):
        feed_calls.append((topic_slug, set(ids)))

    monkeypatch.setattr(
        ingestion_pipeline.ingestion_store, "set_feed_eligibility", _record_feed
    )


def test_reconciliation_keeps_only_admitted_clips_feed_eligible(monkeypatch):
    # One mapped + passing + admitted segment. The path persists only the
    # admitted clip, so the read-back clip-id set IS the admitted set, and the
    # final reconciliation must mark exactly that set feed-eligible (Req 6.4, 6.5).
    seg = _mapped_segment("atom-1")
    feed_calls: list[tuple[str, set]] = []
    _install_pipeline(
        monkeypatch,
        mapped=[seg],
        admitted=[seg],
        stored=1,
        clip_ids={"clip-admitted-1"},
        feed_calls=feed_calls,
    )

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    assert summary.stored == 1
    assert summary.outcome in ("fully_admitted", "partially_admitted")
    # set_feed_eligibility was driven; the reconciliation (last call) keeps only
    # the admitted clip ids feed-eligible.
    assert feed_calls, "expected set_feed_eligibility to be invoked"
    final_topic, final_ids = feed_calls[-1]
    assert final_topic == "binary-search"
    assert final_ids == {"clip-admitted-1"}


def test_nothing_admitted_skips_reconciliation_and_retains_previews(monkeypatch):
    # Mapped segment exists but nothing is admitted (admit_topic -> [], stored 0).
    # total_stored stays 0 so reconciliation is skipped entirely, leaving any
    # existing feed-eligible previews untouched for a later retry (Req 6.7).
    seg = _mapped_segment("atom-1")
    feed_calls: list[tuple[str, set]] = []
    _install_pipeline(
        monkeypatch,
        mapped=[seg],
        admitted=[],
        stored=0,
        clip_ids={"clip-preview-1"},
        feed_calls=feed_calls,
    )

    summary = ingestion_pipeline.ingest_topic("binary-search", "Binary Search")

    assert summary.stored == 0
    assert summary.outcome == "skipped"
    # Reconciliation never ran -> previews are retained, not dropped.
    assert feed_calls == []
