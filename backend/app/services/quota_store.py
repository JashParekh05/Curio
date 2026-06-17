"""Fail-closed I/O shell over the pure ``quota_pool`` core.

The Seeding_Worker and the ``youtube.py`` charge site need durable, per-project
daily YouTube quota usage so spend survives restarts and never exceeds the
10,000 units/day budget per Google Cloud project. The accounting itself is pure
(``quota_pool``); this module is the thin best-effort wrapper that reads and
writes the ``project_quota_usage`` table, mirroring the convention of
``coherence_budget.py`` and ``self_heal_state.py`` (pure decision core, tiny I/O
entrypoints).

It does three things:

  - ``configured_projects``: parse the operator-provisioned ``YT_PROJECTS`` env
    into ``(project_id, api_key)`` pairs (Req 6.7).
  - ``load_today``: read each configured project's ``used_units`` for the current
    Pacific-time rollover window; a missing row means a fresh window so
    ``used = 0`` (Req 1.13, 6.5, 6.7).
  - ``charge_and_persist``: atomically increment ``used_units`` via the
    ``increment_quota_usage`` RPC, persisting the charge BEFORE the caller issues
    the real API call so a recorded increment always precedes spend (Req 6.3, 6.4).

**Fail closed.** Quota I/O is best-effort, but unlike most best-effort shells a
failure here must never silently permit spend: an unreadable project is reported
as fully used (``used = DAILY_QUOTA`` => ``remaining == 0`` => unaffordable), and
an unwritable charge returns ``False`` so the caller skips the API call. A
persistence outage therefore halts spending instead of risking an overspend.
ASCII only.

Validates: Requirements 1.13, 6.3, 6.4, 6.5, 6.7
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from app.db.supabase import get_client
from app.services.quota_pool import (
    DAILY_QUOTA,
    ProjectQuota,
    quota_window_date,
)

logger = logging.getLogger(__name__)

#: Name of the durable per-project daily usage table (see migration_cold_start.sql).
_TABLE = "project_quota_usage"

#: Name of the atomic, overspend-safe upsert RPC (see migration_cold_start.sql).
_INCREMENT_RPC = "increment_quota_usage"

#: Env var holding the operator-provisioned projects as a comma-separated list of
#: ``project_id:api_key`` pairs, e.g. ``YT_PROJECTS="projA:AIza...,projB:AIza..."``.
_PROJECTS_ENV = "YT_PROJECTS"

#: Legacy single-key env var; used to synthesize one ``default`` project when
#: ``YT_PROJECTS`` is unset so existing single-key deployments keep working.
_LEGACY_KEY_ENV = "YOUTUBE_API_KEY"
_LEGACY_PROJECT_ID = "default"


def configured_projects() -> list[tuple[str, str]]:
    """Return the operator-provisioned ``(project_id, api_key)`` pairs.

    Parses ``YT_PROJECTS`` (``"projA:keyA,projB:keyB"``). Blank entries, entries
    without a ``:`` separator, entries missing a project id or key, and duplicate
    project ids (first wins) are skipped so a malformed env never yields a bad
    project. When ``YT_PROJECTS`` is unset/empty but the legacy
    ``YOUTUBE_API_KEY`` is present, a single ``default`` project is synthesized so
    existing single-key deployments keep working. Returns ``[]`` when nothing is
    configured (Req 6.7).

    Validates: Requirements 6.7
    """
    raw = os.environ.get(_PROJECTS_ENV, "").strip()
    projects: list[tuple[str, str]] = []
    seen: set[str] = set()
    if raw:
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            project_id, _, api_key = entry.partition(":")
            project_id = project_id.strip()
            api_key = api_key.strip()
            if not project_id or not api_key or project_id in seen:
                continue
            seen.add(project_id)
            projects.append((project_id, api_key))
    if projects:
        return projects

    # Backwards-compatible fallback: one project from the legacy single key.
    legacy_key = os.environ.get(_LEGACY_KEY_ENV, "").strip()
    if legacy_key:
        return [(_LEGACY_PROJECT_ID, legacy_key)]
    return []


def load_today(now_utc: datetime | None = None) -> list[ProjectQuota]:
    """Return each configured project's ``ProjectQuota`` for today's window.

    Reads ``used_units`` from ``project_quota_usage`` for
    ``quota_window_date(now)``; a project with no row for the window is a fresh
    rollover window and reports ``used = 0`` (Req 1.13, 6.5, 6.7). Values are
    clamped defensively to ``[0, DAILY_QUOTA]``.

    **Fail closed.** If the read raises, every configured project is returned with
    ``used = DAILY_QUOTA`` so ``remaining == 0`` and it is treated as unaffordable
    by the pure core; a persistence outage thus halts spending rather than risking
    an overspend (Req 6.3, 6.4, 6.7).

    Returns ``[]`` when no projects are configured.

    Validates: Requirements 1.13, 6.5, 6.7
    """
    projects = configured_projects()
    if not projects:
        return []

    when = now_utc if now_utc is not None else datetime.now(timezone.utc)
    window = quota_window_date(when)

    try:
        db = get_client()
        resp = (
            db.table(_TABLE)
            .select("project_id, used_units")
            .eq("quota_date", window.isoformat())
            .execute()
        )
        rows = resp.data or []
        used_by_project = {
            row["project_id"]: int(row.get("used_units") or 0)
            for row in rows
            if row.get("project_id") is not None
        }
    except Exception as exc:
        # Fail closed: an unreadable store makes every project unaffordable.
        logger.warning(
            "[quota_store] load_today read failed for window %s; failing closed "
            "(all projects unaffordable): %s",
            window.isoformat(),
            exc,
        )
        return [ProjectQuota(project_id=pid, used=DAILY_QUOTA) for pid, _ in projects]

    result: list[ProjectQuota] = []
    for project_id, _ in projects:
        used = used_by_project.get(project_id, 0)
        used = max(0, min(DAILY_QUOTA, used))
        result.append(ProjectQuota(project_id=project_id, used=used))
    return result


def charge_and_persist(
    project_id: str,
    cost: int,
    now_utc: datetime | None = None,
) -> bool:
    """Atomically persist a ``cost``-unit charge for ``project_id`` today.

    Calls the ``increment_quota_usage(p_project, p_date, p_cost)`` RPC, which
    upserts and caps ``used_units`` at ``DAILY_QUOTA`` in a single statement
    (overspend-safe). Returns ``True`` only when the increment is durably
    recorded; the caller must charge (issue the real API call) ONLY after a
    ``True`` return, so a persisted increment always precedes spend (Req 6.3, 6.4).

    **Fail closed.** A non-positive cost or any write failure returns ``False`` so
    the caller does not spend; an unwritable project is effectively unaffordable
    (Req 6.3, 6.4).

    Validates: Requirements 6.3, 6.4, 6.5
    """
    if not project_id or cost <= 0:
        return False

    when = now_utc if now_utc is not None else datetime.now(timezone.utc)
    window = quota_window_date(when)

    try:
        db = get_client()
        db.rpc(
            _INCREMENT_RPC,
            {
                "p_project": project_id,
                "p_date": window.isoformat(),
                "p_cost": int(cost),
            },
        ).execute()
        return True
    except Exception as exc:
        # Fail closed: an unwritable charge must not be followed by spend.
        logger.warning(
            "[quota_store] charge_and_persist failed for project=%s cost=%s "
            "window=%s; failing closed (no spend): %s",
            project_id,
            cost,
            window.isoformat(),
            exc,
        )
        return False
