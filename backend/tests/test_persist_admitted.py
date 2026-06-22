"""Integration test for Admission_Gate persistence field-set parity.

Exercises ``admission_gate.persist_admitted`` against a mocked Supabase client
(the shared ``FakeDB`` double from ``conftest``), verifying that each
Admitted_Clip is written with:

  - the IDENTICAL clip field set the on-demand pipeline writes
    (``topic_slug``, ``title``, ``description``, ``video_url``,
    ``thumbnail_url``, ``duration_seconds``, ``transcript``, ``source_url``,
    ``source_platform``, ``hook_score`` — see ``pipeline_agent._node_segment``);
  - PLUS the required NON-NULL coherence/plan metadata
    (``pedagogical_role``, ``role_ordinal``, ``concept_label``,
    ``coherence_score``).

``persist_admitted`` imports ``get_client`` lazily from ``app.db.supabase``
inside the function body, so the bound name to patch lives in the
``app.db.supabase`` module namespace (same fail-closed pattern as
``test_quota_store``, where the import site determines what gets patched).

Validates: Requirements 4.4, 7.2
"""
from dataclasses import dataclass

import app.db.supabase as supabase_mod
from app.services import admission_gate
from tests.conftest import FakeDB

# The exact clip column set the on-demand pipeline (pipeline_agent base clip)
# persists, which the deep-ingestion admission path must match (Req 7.2).
_ONDEMAND_FIELDS = {
    "topic_slug",
    "title",
    "description",
    "video_url",
    "thumbnail_url",
    "duration_seconds",
    "transcript",
    "source_url",
    "source_platform",
    "hook_score",
}

# Required NON-NULL coherence / plan metadata for an Admitted_Clip (Req 4.4).
_METADATA_FIELDS = {
    "pedagogical_role",
    "role_ordinal",
    "concept_label",
    "coherence_score",
}


# --- Lightweight stubs standing in for MappedSegment / LearningAtom ---------

@dataclass
class _StubAtom:
    video_id: str = "vid123"
    start: float = 10.0
    end: float = 40.0
    concept: str = "Binary search invariant"
    transcript: str | None = "Some clear explanatory transcript text."
    source_url: str | None = "https://www.youtube.com/watch?v=vid123"


@dataclass
class _StubSegment:
    atom: _StubAtom
    pedagogical_role: str = "definition"  # a defined PedagogicalRole value
    role_ordinal: int = 2


def _use_db(monkeypatch, db):
    """Point persist_admitted's lazy get_client import at the fake client."""
    monkeypatch.setattr(supabase_mod, "get_client", lambda: db)


class TestPersistAdmittedFieldParity:
    def test_writes_ondemand_field_set_plus_nonnull_metadata(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        segment = _StubSegment(atom=_StubAtom())
        stored = admission_gate.persist_admitted([segment], "binary-search", 0.87)

        assert stored == 1
        inserts = [payload for (table, payload) in db.rec["inserts"] if table == "clips"]
        assert len(inserts) == 1
        row = inserts[0]

        # Exact field-set parity: the on-demand clip columns PLUS the metadata,
        # and nothing else (Req 7.2).
        assert set(row.keys()) == _ONDEMAND_FIELDS | _METADATA_FIELDS

        # Every required metadata field is present and NON-NULL (Req 4.4).
        for field in _METADATA_FIELDS:
            assert field in row
            assert row[field] is not None

        # The metadata reflects the admitted segment / topic coherence.
        assert row["pedagogical_role"] == "definition"
        assert row["role_ordinal"] == 2
        assert row["concept_label"] == "Binary search invariant"
        assert row["coherence_score"] == 0.87
        assert row["topic_slug"] == "binary-search"
        assert row["source_platform"] == "youtube"

    def test_stores_each_admitted_segment(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        segments = [
            _StubSegment(atom=_StubAtom(video_id=f"vid{i}", start=float(i * 10),
                                        end=float(i * 10 + 30),
                                        concept=f"Concept {i}"))
            for i in range(3)
        ]
        stored = admission_gate.persist_admitted(segments, "binary-search", 0.5)

        assert stored == 3
        inserts = [p for (t, p) in db.rec["inserts"] if t == "clips"]
        assert len(inserts) == 3
        # Field-set parity holds for every persisted row.
        for row in inserts:
            assert set(row.keys()) == _ONDEMAND_FIELDS | _METADATA_FIELDS
            assert row["coherence_score"] == 0.5

    def test_empty_admitted_writes_nothing(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        stored = admission_gate.persist_admitted([], "binary-search", 0.5)

        assert stored == 0
        assert db.rec["inserts"] == []

    def test_segment_without_atom_is_skipped(self, monkeypatch):
        db = FakeDB(store={})
        _use_db(monkeypatch, db)

        @dataclass
        class _NoAtom:
            atom: object = None
            pedagogical_role: str = "definition"
            role_ordinal: int = 1

        stored = admission_gate.persist_admitted([_NoAtom()], "binary-search", 0.5)

        # A malformed segment is logged and skipped, never stored.
        assert stored == 0
        assert db.rec["inserts"] == []
