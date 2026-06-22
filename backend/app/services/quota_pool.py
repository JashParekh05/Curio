"""Pure quota-accounting core for the multi-project YouTube Key_Pool.

YouTube Data API v3 quota is 10,000 units/day **per Google Cloud project**
(charged to the project, not to an individual key): a search costs 100 units, a
metadata (``videos.list``) call costs 1 unit, and the daily window resets at
midnight Pacific Time. This module models that accounting as a set of *pure*
functions over an immutable ``ProjectQuota`` value — no DB reads, no clock
reads, no global mutation — mirroring ``self_heal_state.py`` and
``coherence_budget.py``. The thin I/O shell that persists usage lives in
``quota_store.py``; this module never touches it.

The functions answer the questions the Seeding_Worker and the ``youtube.py``
charge site need: how many units remain, whether a cost is affordable, what the
new state is after a charge, which project should serve a cost (with
deterministic ascending-id tie-break and failover), how to reset at rollover,
the total daily budget, and which Pacific-time calendar date a timestamp falls
in (the rollover key).

Invalid costs (``cost <= 0``) are rejected everywhere and leave all state
unchanged. ASCII only.

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10,
1.11, 1.12, 1.13, 1.14, 6.5, 6.9
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Policy constants
# ---------------------------------------------------------------------------

#: Units of Quota_Budget per Project per Quota_Rollover window (Req 1.1, 1.14).
DAILY_QUOTA: int = 10_000

#: Quota_Cost of a single ``youtube/v3/search`` call (Req 1.6).
SEARCH_COST: int = 100

#: Quota_Cost of a single ``videos.list`` (metadata) call (Req 1.7).
METADATA_COST: int = 1

#: IANA zone whose calendar date defines the daily Quota_Rollover (midnight
#: Pacific Time, when YouTube quota resets) (Req 6.5).
_PACIFIC = ZoneInfo("America/Los_Angeles")


# ---------------------------------------------------------------------------
# Immutable per-project quota state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProjectQuota:
    """Immutable snapshot of one Project's daily quota usage.

    Attributes:
        project_id: Stable Project identifier; also the deterministic ascending
            tie-break key for selection and failover (Req 1.9, 1.10).
        used: Units spent by this Project in the current rollover window.
            Invariant: ``0 <= used <= DAILY_QUOTA`` (Req 1.1, 1.2).
    """

    project_id: str
    used: int


# ---------------------------------------------------------------------------
# Pure accounting functions
# ---------------------------------------------------------------------------

def remaining(p: ProjectQuota) -> int:
    """Return this Project's remaining Quota_Budget, never negative.

    ``DAILY_QUOTA - used``, clamped at 0 so a (defensively) over-charged row can
    never report a negative budget.

    Validates: Requirements 1.1, 1.2
    """
    return max(0, DAILY_QUOTA - p.used)


def can_afford(p: ProjectQuota, cost: int) -> bool:
    """Return True iff ``cost`` is valid and this single Project can serve it.

    True if and only if ``cost > 0`` and ``remaining(p) >= cost``. Affordability
    is evaluated per single Project with no splitting of cost across projects, and
    the call never mutates ``p`` (Req 1.3, 1.4, 1.5).

    Validates: Requirements 1.3, 1.4, 1.5
    """
    if cost <= 0:
        return False
    return remaining(p) >= cost


def charge(p: ProjectQuota, cost: int) -> ProjectQuota:
    """Return a NEW ``ProjectQuota`` with ``used`` increased by ``cost``.

    When ``cost > 0`` and affordable, the only effect is producing the updated
    state for this Project (``used += cost``); the input ``p`` is left unchanged.
    When ``cost <= 0`` or unaffordable, ``p`` is returned unchanged. No other
    side effect occurs (Req 1.5, 1.6, 1.7, 1.8).

    Validates: Requirements 1.5, 1.6, 1.7, 1.8
    """
    if not can_afford(p, cost):
        return p
    return replace(p, used=p.used + cost)


def select_project(projects: list[ProjectQuota], cost: int) -> str | None:
    """Return the ``project_id`` that should serve ``cost``, or None.

    Among all Projects whose remaining Quota_Budget is ``>= cost``, return the
    one with the lowest ``project_id`` in ascending order (deterministic
    tie-break). Return None when ``cost <= 0`` or no Project is affordable. Pure;
    no Project state is modified (Req 1.5, 1.9, 1.11, 1.12).

    Validates: Requirements 1.5, 1.9, 1.11, 1.12
    """
    if cost <= 0:
        return None
    affordable = [p.project_id for p in projects if can_afford(p, cost)]
    if not affordable:
        return None
    return min(affordable)


def failover_select(projects: list[ProjectQuota], cost: int,
                    tried: frozenset[str]) -> str | None:
    """Return the next affordable ``project_id`` not already tried, or None.

    Like ``select_project`` but skips any Project whose id is in ``tried``,
    yielding the lowest-id affordable Project that has not yet been attempted.
    Used to fail over when a selected Project turns out to be exhausted. Return
    None when ``cost <= 0`` or no untried Project is affordable. Pure; no Project
    state is modified (Req 1.10, 1.12).

    Validates: Requirements 1.10, 1.12
    """
    if cost <= 0:
        return None
    affordable = [
        p.project_id
        for p in projects
        if p.project_id not in tried and can_afford(p, cost)
    ]
    if not affordable:
        return None
    return min(affordable)


def rollover(projects: list[ProjectQuota]) -> list[ProjectQuota]:
    """Return a new list with every Project's ``used`` reset to 0.

    Models the daily Quota_Rollover: each Project's used units return to 0 and
    its remaining Quota_Budget to ``DAILY_QUOTA``. Pure; the input projects are
    not modified (Req 1.13).

    Validates: Requirements 1.13
    """
    return [replace(p, used=0) for p in projects]


def total_daily_budget(projects: list[ProjectQuota]) -> int:
    """Return the pool's total daily Quota_Budget: ``DAILY_QUOTA * len(projects)``.

    Adding one Project increases the total by exactly ``DAILY_QUOTA`` (Req 1.14).

    Validates: Requirements 1.14
    """
    return DAILY_QUOTA * len(projects)


def quota_window_date(now_utc: datetime) -> date:
    """Return the Pacific-time calendar date ``now_utc`` falls in.

    This is the daily Quota_Rollover key: it is constant for all timestamps
    within a single 24-hour Pacific window and increments at midnight Pacific.
    A naive datetime is assumed to be UTC; an aware datetime is converted to
    Pacific before its date is taken (Req 6.5).

    Validates: Requirements 6.5
    """
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_PACIFIC).date()


# ---------------------------------------------------------------------------
# Operator-provisioned key registration (pure decision core)
# ---------------------------------------------------------------------------
#
# An Operator (a human platform administrator) provisions the pool's capacity by
# registering YouTube API_Keys, each belonging to a Google Cloud Project. Quota
# is tracked per Project (DAILY_QUOTA units/day), so registering the first key of
# a new Project adds DAILY_QUOTA to the pool's total daily budget, while
# registering an additional key for an already-represented Project only widens
# that Project's key set and leaves the total budget unchanged.
#
# This section models the registration *decision* as pure functions over an
# immutable ``KeyPool`` value: validate the submitted key as well-formed, then
# decide whether to add a new Project, associate the key with an existing
# Project, or reject the registration (missing, malformed, or duplicate value).
# Every decision is deterministic in (submitted key, current pool) and produces a
# NEW pool on success while leaving the input pool unchanged; on rejection the
# input pool is returned unchanged. No DB, clock, or global state is touched. The
# thin admin/I/O shell that calls these lives elsewhere. ASCII only.
#
# Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8, 7.9, 7.10, 6.9

#: Registration decision actions returned by ``register_key`` (Req 7.2, 7.3, 7.5-7.7).
ACTION_ADD: str = "add"               # well-formed key for a new Project (Req 7.2, 7.4)
ACTION_ASSOCIATE: str = "associate"   # well-formed key for an existing Project (Req 7.3)
ACTION_REJECT: str = "reject"         # invalid registration; pool unchanged (Req 7.5-7.7)

#: Rejection reasons paired with ``ACTION_REJECT`` (Req 7.5, 7.6, 7.7).
REASON_MISSING: str = "missing"       # null / empty / whitespace-only value (Req 7.5)
REASON_MALFORMED: str = "malformed"   # present but not well-formed (Req 7.6)
REASON_DUPLICATE: str = "duplicate"   # value already present in the pool (Req 7.7)

#: Well-formedness pattern for a YouTube Data API v3 / Google API key: the
#: literal prefix ``AIza`` followed by 35 URL-safe base64 characters (39 total).
#: Anchored, so any surrounding whitespace makes a present value malformed
#: (distinct from a whitespace-only value, which is treated as missing) (Req 7.6).
_API_KEY_RE = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")


@dataclass(frozen=True)
class KeyPool:
    """Immutable snapshot of registered API_Keys grouped by ``project_id``.

    Attributes:
        projects: Mapping from ``project_id`` to the frozen set of API_Keys
            registered for that Project. Each represented Project contributes
            ``DAILY_QUOTA`` to the pool's total daily budget regardless of how
            many keys it holds, because quota is per Project (Req 7.2, 7.3).

    The mapping is treated as read-only: every registration returns a NEW
    ``KeyPool`` rather than mutating an existing one, keeping the decision core
    pure (Req 7.10).
    """

    projects: Mapping[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class RegistrationResult:
    """Outcome of a single ``register_key`` decision.

    Attributes:
        action: One of ``ACTION_ADD``, ``ACTION_ASSOCIATE``, or ``ACTION_REJECT``.
        pool: The resulting ``KeyPool``. A new pool on add/associate; the
            unchanged input pool on reject (Req 7.5, 7.6, 7.7).
        reason: Rejection reason (``REASON_MISSING`` / ``REASON_MALFORMED`` /
            ``REASON_DUPLICATE``) when ``action == ACTION_REJECT``; otherwise None.
        new_project: True only when a previously unrepresented Project was added,
            i.e. when the total daily budget grew by exactly ``DAILY_QUOTA``
            (Req 7.2, 7.4). False for associate and reject (Req 7.3).
    """

    action: str
    pool: KeyPool
    reason: str | None = None
    new_project: bool = False

    @property
    def accepted(self) -> bool:
        """True iff the registration was accepted (added or associated)."""
        return self.action in (ACTION_ADD, ACTION_ASSOCIATE)


def empty_pool() -> KeyPool:
    """Return a fresh, empty ``KeyPool`` (no Projects, zero total budget).

    The pool the very first registration initializes (Req 7.4).

    Validates: Requirements 7.4
    """
    return KeyPool(projects={})


def is_present(api_key: str | None) -> bool:
    """Return True iff ``api_key`` is a non-empty, non-whitespace-only value.

    A null, empty, or whitespace-only value is treated as *missing* (Req 7.5).

    Validates: Requirements 7.5
    """
    return api_key is not None and api_key.strip() != ""


def is_well_formed(api_key: str | None) -> bool:
    """Return True iff ``api_key`` is present and matches the API_Key format.

    Well-formedness requires a present value (Req 7.5) that matches the anchored
    Google API key pattern (``AIza`` + 35 URL-safe chars). A present-but-
    non-matching value is *malformed* (Req 7.6). Pure; no side effects.

    Validates: Requirements 7.1, 7.6
    """
    if not is_present(api_key):
        return False
    return _API_KEY_RE.fullmatch(api_key) is not None


def registered_keys(pool: KeyPool) -> frozenset[str]:
    """Return the set of every API_Key currently registered across all Projects.

    Used for the exact-value duplicate check (Req 7.7). Pure; no side effects.

    Validates: Requirements 7.7
    """
    keys: set[str] = set()
    for project_keys in pool.projects.values():
        keys.update(project_keys)
    return frozenset(keys)


def is_duplicate_key(pool: KeyPool, api_key: str) -> bool:
    """Return True iff ``api_key`` exactly matches a key already in the pool.

    Pure; no side effects (Req 7.7).

    Validates: Requirements 7.7
    """
    return api_key in registered_keys(pool)


def project_count(pool: KeyPool) -> int:
    """Return the number of distinct Projects represented in the pool.

    Validates: Requirements 7.2, 7.3
    """
    return len(pool.projects)


def pool_total_budget(pool: KeyPool) -> int:
    """Return the pool's total daily Quota_Budget: ``DAILY_QUOTA`` per Project.

    Independent of how many keys each Project holds, so associating a key with an
    existing Project leaves this unchanged while adding a new Project increases it
    by exactly ``DAILY_QUOTA`` (Req 7.2, 7.3).

    Validates: Requirements 7.2, 7.3
    """
    return DAILY_QUOTA * project_count(pool)


def register_key(pool: KeyPool, project_id: str, api_key: str | None) -> RegistrationResult:
    """Decide how to register ``api_key`` for ``project_id`` against ``pool``.

    The decision is a pure function of the submitted key and the current pool
    contents, with no side effects (Req 7.10). Resolution order:

    1. Missing value (null / empty / whitespace) -> reject ``REASON_MISSING``,
       pool unchanged (Req 7.5).
    2. Present but not well-formed -> reject ``REASON_MALFORMED``, pool unchanged
       (Req 7.6).
    3. Value exactly matches a key already in the pool -> reject
       ``REASON_DUPLICATE``, pool unchanged (Req 7.7).
    4. Well-formed, new value, Project already represented -> associate the key
       with that Project; total budget unchanged (Req 7.3).
    5. Well-formed, new value, Project not yet represented (including the
       empty-pool case) -> add the Project with this key; total budget grows by
       exactly ``DAILY_QUOTA`` (Req 7.1, 7.2, 7.4).

    On accept a NEW ``KeyPool`` is returned and the input ``pool`` is left
    unchanged; on reject the input ``pool`` is returned unchanged so all existing
    entries and budget state are preserved (Req 7.5, 7.6, 7.7).

    Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.10
    """
    # 1. Missing value (Req 7.5).
    if not is_present(api_key):
        return RegistrationResult(action=ACTION_REJECT, pool=pool, reason=REASON_MISSING)

    # 2. Malformed value (Req 7.6). ``api_key`` is non-None past the present check.
    if not is_well_formed(api_key):
        return RegistrationResult(action=ACTION_REJECT, pool=pool, reason=REASON_MALFORMED)

    # 3. Duplicate value anywhere in the pool (Req 7.7).
    if is_duplicate_key(pool, api_key):
        return RegistrationResult(action=ACTION_REJECT, pool=pool, reason=REASON_DUPLICATE)

    # 4 & 5. Accept: build a new pool with the key added to its Project. The
    # mapping is copied (and the Project's key set rebuilt) so the input pool is
    # never mutated (Req 7.10).
    new_projects: dict[str, frozenset[str]] = dict(pool.projects)
    if project_id in new_projects:
        # Associate with an existing Project; total budget unchanged (Req 7.3).
        new_projects[project_id] = new_projects[project_id] | {api_key}
        new_pool = KeyPool(projects=new_projects)
        return RegistrationResult(action=ACTION_ASSOCIATE, pool=new_pool, new_project=False)

    # Add a new Project (also the empty-pool init); budget += DAILY_QUOTA
    # (Req 7.2, 7.4).
    new_projects[project_id] = frozenset({api_key})
    new_pool = KeyPool(projects=new_projects)
    return RegistrationResult(action=ACTION_ADD, pool=new_pool, new_project=True)
