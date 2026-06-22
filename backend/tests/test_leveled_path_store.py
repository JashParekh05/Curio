"""Unit tests for the Level_Grouping I/O shell (leveled_path_store).

Covers the seam wiring only -- the pure partition logic is property-tested in
``test_prop_level_grouping.py``. Verifies that an ordered Spine_Path is mapped to
``PathTopic`` and grouped, that the serialized projection is jsonb-ready
(ordinal -> name -> topic_slugs), that the projection is persisted into
``learning_paths.levels`` on the matching row, and that a failure (e.g. the
column not yet existing) degrades gracefully without raising.

Feature: structured-learn-curriculum, Task 7.5 (Level_Grouping shell)
Validates: Requirements 1.1, 1.2, 5.3
"""
from tests.conftest import FakeDB

from app.services.curriculum_spine import SpineNode
from app.services.leveled_path_store import (
    build_leveled_path,
    persist_leveled_path,
    serialize_leveled_path,
)


def _nodes() -> list[SpineNode]:
    # A non-decreasing prerequisite order spanning two level bands.
    return [
        SpineNode(topic_slug="a", content_level="beginner", est_minutes=5),
        SpineNode(topic_slug="b", content_level="beginner", est_minutes=5),
        SpineNode(topic_slug="c", content_level="intermediate", est_minutes=7),
        SpineNode(topic_slug="d", content_level="advanced", est_minutes=9),
    ]


def test_build_preserves_order_and_partitions():
    leveled = build_leveled_path(_nodes())
    # 2-4 levels for a multi-topic path, and flattening reproduces input order.
    assert 2 <= len(leveled.levels) <= 4
    flat = [slug for level in leveled.levels for slug in level.topic_slugs]
    assert flat == ["a", "b", "c", "d"]
    # Ordinals are consecutive from 1.
    assert [lvl.ordinal for lvl in leveled.levels] == list(
        range(1, len(leveled.levels) + 1)
    )


def test_build_empty_path_yields_empty_leveled_path():
    leveled = build_leveled_path([])
    assert leveled.levels == ()
    assert serialize_leveled_path(leveled) == []


def test_serialize_is_jsonb_ready():
    leveled = build_leveled_path(_nodes())
    payload = serialize_leveled_path(leveled)
    assert isinstance(payload, list)
    for obj in payload:
        assert set(obj.keys()) == {"ordinal", "name", "topic_slugs"}
        assert isinstance(obj["ordinal"], int)
        assert isinstance(obj["name"], str)
        assert isinstance(obj["topic_slugs"], list)
        assert all(isinstance(s, str) for s in obj["topic_slugs"])


def test_build_accepts_equivalent_objects_without_spine_node():
    class _Equivalent:
        def __init__(self, topic_slug, content_level):
            self.topic_slug = topic_slug
            self.content_level = content_level

    leveled = build_leveled_path(
        [_Equivalent("x", "beginner"), _Equivalent("y", "advanced")]
    )
    flat = [slug for level in leveled.levels for slug in level.topic_slugs]
    assert flat == ["x", "y"]


def test_persist_writes_levels_onto_matching_row():
    db = FakeDB(store={"learning_paths": [{"session_id": "s1", "levels": None}]})
    leveled = build_leveled_path(_nodes())

    assert persist_leveled_path("s1", leveled, db) is True

    row = db.store["learning_paths"][0]
    assert row["levels"] == serialize_leveled_path(leveled)
    # The update was recorded against the correct row key.
    assert db.rec["updates"][0][0] == "learning_paths"
    assert db.rec["updates"][0][2] == {"session_id": "s1"}


def test_persist_degrades_gracefully_when_column_absent():
    # A failing table (e.g. the `levels` column not yet migrated) must not raise.
    db = FakeDB(
        store={"learning_paths": [{"session_id": "s1"}]},
        fail={"learning_paths"},
    )
    leveled = build_leveled_path(_nodes())
    assert persist_leveled_path("s1", leveled, db) is False


def test_persist_no_session_id_is_noop():
    db = FakeDB(store={"learning_paths": []})
    assert persist_leveled_path("", build_leveled_path(_nodes()), db) is False
    assert db.rec.get("updates", []) == []
