"""Example test for arc cache reuse in plan_and_store_arc.

Requirement 8.5: a topic that already has a stored arc skips regeneration —
no LLM classification is performed; the stored arc is returned verbatim.

``plan_and_store_arc`` performs a cache check before any LLM work:

    1. db.table("topic_arcs").select(...).eq(...).limit(1).execute()
    2. if rows exist:
           db.table("topic_arc_roles").select(...).eq(...).order(...).execute()
       and returns a PlannedArc built from those rows WITHOUT calling
       classify_concept_type.

This test drives that path with a minimal fake DB and a classify_concept_type
monkeypatched to blow up, proving the LLM is never invoked on a cache hit.
"""
from app.agents import section_planner
from app.agents.section_planner import plan_and_store_arc
from app.models.schemas import ArcRole, PlannedArc


class _Result:
    """Mimics a supabase execute() result: exposes a .data list."""

    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal query builder: every filter is a no-op that returns self;
    execute() yields the table's preloaded rows. Matches the chained calls
    plan_and_store_arc makes: .select().eq().limit().order().execute()."""

    def __init__(self, data):
        self._data = data

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def execute(self):
        return _Result(self._data)


class _FakeDB:
    """Returns a query builder seeded with each table's rows."""

    def __init__(self, tables):
        self._tables = tables

    def table(self, name):
        return _Query(self._tables.get(name, []))


def test_cache_hit_returns_stored_arc_without_classifying(monkeypatch):
    slug = "recursion"
    name = "Recursion"

    # Stored conceptual arc + its roles already present for the slug.
    arc_row = {
        "concept_type": "conceptual",
        "default_applied": False,
        "template_empty": False,
    }
    role_rows = [
        {"role": "definition", "ordinal": 1},
        {"role": "motivation", "ordinal": 2},
        {"role": "mechanism", "ordinal": 3},
        {"role": "example", "ordinal": 4},
        {"role": "common_misconception", "ordinal": 5},
    ]
    fake_db = _FakeDB(
        {
            "topic_arcs": [arc_row],
            "topic_arc_roles": role_rows,
        }
    )

    # Prove the LLM classifier is never invoked on a cache hit.
    calls = []

    def _boom(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("classify_concept_type must not be called on a cache hit")

    monkeypatch.setattr(section_planner, "classify_concept_type", _boom)

    result = plan_and_store_arc(slug, name, db=fake_db)

    assert calls == [], "classify_concept_type was called on a cache hit"

    expected = PlannedArc(
        topic_slug=slug,
        concept_type="conceptual",
        default_applied=False,
        template_empty=False,
        roles=[ArcRole(role=r["role"], ordinal=r["ordinal"]) for r in role_rows],
    )
    assert result == expected
