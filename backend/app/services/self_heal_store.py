"""Durable persistence shell for the self-heal retry budget.

The pure retry policy + decision functions live in ``self_heal_state`` (no DB,
no clock, no globals read). That module's attempt tracker, however, is
in-process only: it resets on every restart/deploy and is not shared across
workers, so a topic that can never be filled re-burns paid TranscriptAPI
credits + YouTube quota a fresh ``MAX_SELF_HEAL_ATTEMPTS`` times after every
deploy, forever.

This thin shell persists the per-topic attempt count + last-attempt time to the
``self_heal_attempts`` table (migration_self_heal_durable.sql) so the cap
survives restarts and is shared across workers. It exposes the SAME
``read`` / ``record_attempt`` / ``clear`` surface the in-process tracker does,
so call sites swap module without changing the pure decision calls.

Best-effort throughout: every DB access is guarded and, on any failure, degrades
to the in-process ``self_heal_state`` tracker so self-heal still works (just
non-durably) when the table is absent or the DB is unreachable. Never raises.
ASCII only.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.db.supabase import get_client
from app.services import self_heal_state

logger = logging.getLogger(__name__)

_TABLE = "self_heal_attempts"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(last_attempt_at: str | None) -> float | None:
    """Wall-clock seconds since ``last_attempt_at`` (ISO-8601), or None.

    Total and best-effort: an unparseable / missing timestamp yields None so the
    cooldown branch in ``should_self_heal`` treats it as "no prior attempt".
    """
    if not last_attempt_at:
        return None
    try:
        ts = datetime.fromisoformat(last_attempt_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
        return max(0.0, delta)
    except Exception:
        return None


def read(slug: str) -> tuple[int, float | None]:
    """Return (attempts, age_seconds_since_last_attempt | None) for a slug.

    Durable read from ``self_heal_attempts``. On any DB failure (table missing,
    connection error) degrades to the in-process tracker so the decision still
    has a bound.
    """
    try:
        res = (
            get_client()
            .table(_TABLE)
            .select("attempts,last_attempt_at")
            .eq("topic_slug", slug)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning("[self_heal] durable read failed for '%s'; using in-process: %s", slug, exc)
        return self_heal_state.read(slug)

    rows = res.data or []
    if not rows:
        return 0, None
    row = rows[0]
    attempts = int(row.get("attempts") or 0)
    return attempts, _age_seconds(row.get("last_attempt_at"))


def record_attempt(slug: str) -> None:
    """Record an empty/failed generation attempt for a slug, durably.

    Increments the persisted attempt count and stamps ``last_attempt_at``. Also
    mirrors to the in-process tracker so a DB-down window still bounds spend
    within the process. Best-effort: failures are logged and swallowed.

    Note: the increment is read-modify-write, so two workers racing may under-
    count by one. That is acceptable for a best-effort spend cap -- worst case a
    topic gets one extra attempt, vastly better than a full reset every deploy.
    """
    # Always keep the in-process tracker current as a fallback signal.
    self_heal_state.record_attempt(slug)

    try:
        db = get_client()
        current, _ = 0, None
        res = (
            db.table(_TABLE)
            .select("attempts")
            .eq("topic_slug", slug)
            .limit(1)
            .execute()
        )
        if res.data:
            current = int(res.data[0].get("attempts") or 0)
        db.table(_TABLE).upsert(
            {
                "topic_slug": slug,
                "attempts": current + 1,
                "last_attempt_at": _now_iso(),
                "updated_at": _now_iso(),
            },
            on_conflict="topic_slug",
        ).execute()
    except Exception as exc:
        logger.warning("[self_heal] durable record_attempt failed for '%s': %s", slug, exc)


def clear(slug: str) -> None:
    """Clear tracking after a successful generation (>= 1 clip), durably.

    Deletes the persisted row and clears the in-process tracker. Best-effort.
    """
    self_heal_state.clear(slug)
    try:
        get_client().table(_TABLE).delete().eq("topic_slug", slug).execute()
    except Exception as exc:
        logger.warning("[self_heal] durable clear failed for '%s': %s", slug, exc)
