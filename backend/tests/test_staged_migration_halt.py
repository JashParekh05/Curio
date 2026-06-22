"""Reversal-incomplete halt test for the Staged_Migration runner (Task 14.7).

Wires the operator-run ``scripts.staged_migration`` runner against an in-memory
Supabase double, with no external service touched, to assert the Req 8.13
guarantee the pure gate cannot express on its own: when a step's recorded reverse
SQL fails to restore the exact pre-step schema, the runner HALTS, leaves the
recorded migration state unchanged, and returns a reversal-incomplete indication.

It also asserts the clean-reverse path for contrast: when the reverse DOES restore
the pre-step schema, the runner records the reverse (clearing the applied marker)
and returns a ``reversed`` result.

Run from the backend/ dir:
``.venv/bin/python -m pytest tests/test_staged_migration_halt.py``.

Validates: Requirements 8.13
"""
from scripts import staged_migration as sm


# ---------------------------------------------------------------------------
# Minimal stateful in-memory Supabase double for the migration_state table
# (supports upsert + update with eq filters, which the runner uses).
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table = table
        self.store = store
        self.op = "select"
        self.payload = None
        self.eqs = {}

    def select(self, *a, **k):
        self.op = "select"; return self

    def upsert(self, payload, **k):
        self.op = "upsert"; self.payload = payload; return self

    def update(self, payload, **k):
        self.op = "update"; self.payload = payload; return self

    def eq(self, col, val):
        self.eqs[col] = val; return self

    def _matches(self, row):
        return all(row.get(c) == v for c, v in self.eqs.items())

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self.op == "upsert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            for item in payload:
                key = (item.get("migration_id"), item.get("step"))
                existing = next(
                    (r for r in rows
                     if (r.get("migration_id"), r.get("step")) == key),
                    None,
                )
                if existing is not None:
                    existing.update(item)
                else:
                    rows.append(dict(item))
            return _Result(list(payload))
        if self.op == "update":
            matched = [r for r in rows if self._matches(r)]
            for r in matched:
                r.update(self.payload)
            return _Result(matched)
        return _Result([r for r in rows if self._matches(r)])


class StatefulDB:
    def __init__(self, store=None):
        self.store = store or {}

    def table(self, name):
        return _Query(name, self.store)


# Schema markers: a tiny stand-in for "what the schema looks like". The pre-step
# schema is the recorded baseline the reverse must restore.
_PRE_STEP_SCHEMA = {"canonical_arc": False, "topics.archived": False}
_POST_ADDITIVE_SCHEMA = {"canonical_arc": True, "topics.archived": True}


def _applied_row(db, migration_id, step):
    rows = db.store.get("migration_state", [])
    return next(
        (r for r in rows
         if r.get("migration_id") == migration_id and r.get("step") == step),
        None,
    )


class TestReverseFailsToRestore:
    def test_halts_and_leaves_state_unchanged_on_incomplete_reversal(self):
        db = StatefulDB()
        # The additive step is already applied and recorded.
        sm._record_applied(db, "content_revamp", "additive", "snap-001",
                           sm.MIGRATIONS["content_revamp"]["additive"].reverse_sql)
        before = dict(_applied_row(db, "content_revamp", "additive"))
        assert before["applied_at"] is not None

        executed = []

        def _executor(sql):
            executed.append(sql)

        # The reverse SQL runs, but the probe shows the schema did NOT return to
        # the pre-step baseline (e.g. a drop silently failed) -> incomplete.
        def _probe_not_restored():
            return _POST_ADDITIVE_SCHEMA

        result = sm.reverse_step(
            "content_revamp", "additive", _PRE_STEP_SCHEMA,
            db=db, execute_sql=_executor, schema_probe=_probe_not_restored,
        )

        # Reversal-incomplete indication returned (Req 8.13).
        assert result.status == sm.STATUS_REVERSAL_INCOMPLETE
        assert result.reason == "schema_not_restored"
        # The reverse SQL was attempted...
        assert executed == [sm.MIGRATIONS["content_revamp"]["additive"].reverse_sql]
        # ...but the recorded migration state is LEFT UNCHANGED: the applied marker
        # is still set, the runner halted rather than clearing it (Req 8.13).
        after = _applied_row(db, "content_revamp", "additive")
        assert after == before
        assert after["applied_at"] is not None

    def test_reverse_sql_raising_also_halts_without_touching_state(self):
        db = StatefulDB()
        sm._record_applied(db, "content_revamp", "additive", "snap-001",
                           sm.MIGRATIONS["content_revamp"]["additive"].reverse_sql)
        before = dict(_applied_row(db, "content_revamp", "additive"))

        def _boom(sql):
            raise RuntimeError("connection lost mid-reverse")

        result = sm.reverse_step(
            "content_revamp", "additive", _PRE_STEP_SCHEMA,
            db=db, execute_sql=_boom,
            schema_probe=lambda: (_ for _ in ()).throw(AssertionError("probe must not run")),
        )

        assert result.status == sm.STATUS_REVERSAL_INCOMPLETE
        assert "connection lost" in result.reason
        # State untouched after the halt.
        assert _applied_row(db, "content_revamp", "additive") == before


class TestReverseRestoresCleanly:
    def test_clean_reverse_clears_applied_marker(self):
        db = StatefulDB()
        sm._record_applied(db, "content_revamp", "additive", "snap-001",
                           sm.MIGRATIONS["content_revamp"]["additive"].reverse_sql)
        assert _applied_row(db, "content_revamp", "additive")["applied_at"] is not None

        # The probe confirms the schema returned to the pre-step baseline.
        result = sm.reverse_step(
            "content_revamp", "additive", _PRE_STEP_SCHEMA,
            db=db, execute_sql=lambda sql: None,
            schema_probe=lambda: dict(_PRE_STEP_SCHEMA),
        )

        assert result.status == sm.STATUS_REVERSED
        assert result.reason is None
        # On a clean reverse the applied marker is cleared.
        assert _applied_row(db, "content_revamp", "additive")["applied_at"] is None
