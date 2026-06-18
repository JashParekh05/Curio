"""Integration tests for the best-effort Impression_Store writer (Task 7.3).

Exercises ``app.services.impression_store.record_impressions`` against a mocked
Supabase client, verifying the fail-open posture the writer must keep because it
runs inside a FastAPI ``BackgroundTask`` after the feed response is already sent:

  - per-row failure: when one row's ``insert().execute()`` raises a generic
    error, the remaining rows are still inserted, a single WARNING naming the
    failing clip is logged, and ``record_impressions`` does not raise (Req 1.10);
  - table absent: when ``insert().execute()`` raises a PostgREST
    "relation does not exist" / PGRST205 style error, ``record_impressions``
    logs exactly one WARNING, returns without raising, and does NOT attempt an
    insert for every remaining row (Req 6.3, 6.4).

``impression_store`` does ``from app.db.supabase import get_client``, so the bound
name to patch lives in the impression_store module namespace.

Validates: Requirements 1.10, 6.3, 6.4
"""
import logging

import pytest

from app.models.schemas import Impression
from app.services import impression_store


# --------------------------------------------------------------------------
# Test doubles: a fake Supabase client whose per-row insert can be made to fail
# --------------------------------------------------------------------------

class _FakeInsert:
    """One ``insert(row)`` handle; ``execute()`` records or raises per the client."""

    def __init__(self, client, row):
        self._client = client
        self._row = row

    def execute(self):
        self._client.attempts.append(self._row)
        err = self._client.error_for(self._row)
        if err is not None:
            raise err
        self._client.inserted.append(self._row)
        return None


class _FakeTable:
    def __init__(self, client):
        self._client = client

    def insert(self, row):
        return _FakeInsert(self._client, row)


class _FakeClient:
    """Minimal Supabase double for the insert path.

    ``error_factory(row) -> Exception | None`` decides, per row, whether that
    row's ``execute()`` raises (and with what error) or succeeds. Records every
    attempted row and every successfully inserted row.
    """

    def __init__(self, error_factory):
        self._error_factory = error_factory
        self.attempts = []   # every row whose execute() was called
        self.inserted = []   # rows that landed without error

    def error_for(self, row):
        return self._error_factory(row)

    def table(self, name):
        assert name == "impressions"
        return _FakeTable(self)


def _impression(clip_id, *, position):
    """Build a minimally-valid Impression for the discover surface."""
    return Impression(
        clip_id=clip_id,
        session_id="sess-1",
        user_id="user-1",
        feed_surface="discover",
        feed_position=position,
        pedagogical_role=None,
        content_level=None,
        source_platform=None,
        topic_slug="binary-search",
        served_at="2024-01-15T12:00:00+00:00",
    )


def _use_client(monkeypatch, client):
    monkeypatch.setattr(impression_store, "get_client", lambda: client)


# --------------------------------------------------------------------------
# Req 1.10 — a single failing row must not lose the rest of the batch
# --------------------------------------------------------------------------

class TestPerRowFailureDegradation:
    def test_one_failing_row_still_inserts_the_rest(self, monkeypatch, caplog):
        impressions = [
            _impression("clip-a", position=0),
            _impression("clip-bad", position=1),
            _impression("clip-c", position=2),
        ]

        def error_factory(row):
            # Only the middle row's insert fails, with a generic error.
            if row.get("clip_id") == "clip-bad":
                return RuntimeError("constraint violation")
            return None

        client = _FakeClient(error_factory)
        _use_client(monkeypatch, client)

        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            # Best-effort: must not raise even though one row fails.
            impression_store.record_impressions(impressions)

        # The two healthy rows still landed; the bad row did not.
        inserted_ids = [r["clip_id"] for r in client.inserted]
        assert inserted_ids == ["clip-a", "clip-c"]

        # Every row was attempted (the failure did not short-circuit the batch).
        attempted_ids = [r["clip_id"] for r in client.attempts]
        assert attempted_ids == ["clip-a", "clip-bad", "clip-c"]

        # Exactly one WARNING, and it names the failing clip.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "clip-bad" in warnings[0].getMessage()

    def test_per_row_failure_does_not_raise(self, monkeypatch, caplog):
        impressions = [_impression("clip-x", position=0)]
        client = _FakeClient(lambda row: RuntimeError("boom"))
        _use_client(monkeypatch, client)

        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            # No exception should escape.
            impression_store.record_impressions(impressions)

        # Nothing landed, but the call returned normally and warned once.
        assert client.inserted == []
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "clip-x" in warnings[0].getMessage()


# --------------------------------------------------------------------------
# Req 6.3 / 6.4 — a missing table degrades to a single WARNING, no per-row spam
# --------------------------------------------------------------------------

class TestTableAbsentDegradation:
    @pytest.mark.parametrize(
        "message",
        [
            'relation "impressions" does not exist',
            "Could not find the table 'public.impressions' in the schema cache",
            "PGRST205: table not found",
            "ERROR: 42P01: undefined_table",
        ],
    )
    def test_missing_table_logs_one_warning_and_stops(
        self, monkeypatch, caplog, message
    ):
        impressions = [
            _impression("clip-a", position=0),
            _impression("clip-b", position=1),
            _impression("clip-c", position=2),
        ]
        # Every insert would raise the missing-table error.
        client = _FakeClient(lambda row: RuntimeError(message))
        _use_client(monkeypatch, client)

        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            impression_store.record_impressions(impressions)

        # Exactly one WARNING for the whole batch (not one per row).
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

        # It stopped after the first failing attempt rather than trying every row.
        assert len(client.attempts) == 1
        assert client.inserted == []

    def test_missing_table_does_not_raise(self, monkeypatch, caplog):
        impressions = [_impression("clip-a", position=0)]
        client = _FakeClient(lambda row: RuntimeError('relation "impressions" does not exist'))
        _use_client(monkeypatch, client)

        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            # Must return without propagating the error.
            impression_store.record_impressions(impressions)

        assert client.inserted == []


# --------------------------------------------------------------------------
# get_client failure also degrades to a single WARNING (Req 6.3, 6.4)
# --------------------------------------------------------------------------

class TestClientUnavailable:
    def test_client_unavailable_logs_one_warning_and_returns(self, monkeypatch, caplog):
        def boom():
            raise RuntimeError("no supabase client configured")

        monkeypatch.setattr(impression_store, "get_client", boom)

        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            impression_store.record_impressions([_impression("clip-a", position=0)])

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1

    def test_empty_batch_is_a_noop(self, monkeypatch, caplog):
        # No client lookup, no warning for an empty served set.
        def boom():
            raise AssertionError("get_client must not be called for an empty batch")

        monkeypatch.setattr(impression_store, "get_client", boom)
        with caplog.at_level(logging.WARNING, logger=impression_store.logger.name):
            impression_store.record_impressions([])
        assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
