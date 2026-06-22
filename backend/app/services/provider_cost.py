"""Pure per-provider cost-accounting core for the Content_Provider abstraction.

Every billable operation issued through a Content_Provider (a search, a metadata
fetch, and so on) charges a ``Cost_Unit`` against that provider's
``Spend_Budget`` for the current accounting window. So the system never
overspends any source, the charge is verified and applied as a single
verify-then-charge decision *before* the operation is initiated, and an
accounting outage halts spend rather than risking overspend.

This module is the *pure* decision core for that accounting, mirroring
``quota_pool.py``, ``coherence_budget.py``, and ``provider_dedup.py``: every
function is total and deterministic in its inputs, with no DB reads, no clock
reads, and no global mutation. The thin best-effort I/O shell
(``charge_before_call``) that reads and persists the ``SpendState`` and
delegates the ``youtube`` provider to the existing ``Key_Pool`` is a separate
concern appended later; this core never touches persistence.

The rules it enforces:

  - ``can_afford`` is true iff charging the ``Cost_Unit`` would not push spend
    past the budget (Req 7.3).
  - ``charge_decision`` is the atomic verify-then-charge step applied before any
    billable operation is initiated (Req 7.1):
      * a ``None`` state means the accounting record could not be read or its
        read timed out, so the decision fails closed with no charge
        (Req 7.4);
      * an over-budget charge is refused with the spend counters left unchanged
        (Req 7.3);
      * otherwise the charge is allowed and the new spend is ``spent + cost_unit``.
  - ``is_cached_free`` reports that an unexpired cache hit costs nothing, so the
    operation is not initiated and no ``Cost_Unit`` is charged (Req 7.6).

ASCII only.

Validates: Requirements 7.1, 7.3, 7.4, 7.6, 9.5
"""
from __future__ import annotations

from dataclasses import dataclass

# Stable reason labels returned by ``charge_decision``.
REASON_OK = "ok"
REASON_INSUFFICIENT_BUDGET = "insufficient_budget"
REASON_ACCOUNTING_UNAVAILABLE = "accounting_unavailable"

# ---------------------------------------------------------------------------
# Immutable value models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpendState:
    """The spend accounting for one Content_Provider's current window.

    Each provider's ``Spend_Budget`` is accounted independently of every other
    provider's (Req 7.2); a ``SpendState`` carries the snapshot for exactly one
    provider.

    Attributes:
        provider_id: Stable Provider_Id this accounting record belongs to.
        spent: Cost_Units already consumed in the current accounting window.
        budget: The provider's Spend_Budget for the current window.
    """

    provider_id: str
    spent: int
    budget: int


@dataclass(frozen=True)
class ChargeDecision:
    """The outcome of a verify-then-charge decision for one billable operation.

    Attributes:
        allowed: True iff the operation may be initiated; the caller charges
            ``new_spent`` BEFORE initiating the call (Req 7.1).
        new_spent: The spend to persist when ``allowed`` is True
            (``spent + cost_unit``); when the charge is refused the spend counters
            are left unchanged (Req 7.3), and when the accounting record is
            unavailable there is nothing to persist (reported as ``0``, Req 7.4).
        reason: One of ``'ok'``, ``'insufficient_budget'``, or
            ``'accounting_unavailable'``.
    """

    allowed: bool
    new_spent: int
    reason: str


# ---------------------------------------------------------------------------
# Pure cost-accounting decisions
# ---------------------------------------------------------------------------


def can_afford(state: SpendState, cost_unit: int) -> bool:
    """Return True iff charging ``cost_unit`` keeps spend within budget.

    True exactly when ``state.spent + cost_unit <= state.budget`` (Req 7.3).
    Pure and total.

    Args:
        state: The provider's current spend snapshot.
        cost_unit: The Cost_Unit the billable operation would charge.

    Returns:
        True iff the charge would not exceed the provider's Spend_Budget.

    Validates: Requirements 7.3
    """
    return state.spent + cost_unit <= state.budget


def charge_decision(state: SpendState | None, cost_unit: int) -> ChargeDecision:
    """Decide whether a billable operation may be charged and initiated.

    The single atomic verify-then-charge step applied before any billable
    operation is initiated (Req 7.1):

      - ``state is None`` means the provider's Spend_Budget accounting record
        could not be read or its read timed out; the decision fails closed with
        ``allowed=False``, ``reason='accounting_unavailable'``, and ``new_spent=0``
        (there is no record to charge), so no billable operation is initiated
        (Req 7.4).
      - When ``state.spent + cost_unit`` would exceed ``state.budget`` the charge
        is refused with ``allowed=False``, ``reason='insufficient_budget'``, and
        ``new_spent == state.spent`` so the spend counters are left unchanged
        (Req 7.3).
      - Otherwise the charge is allowed with ``allowed=True``,
        ``new_spent = state.spent + cost_unit``, and ``reason='ok'``.

    Pure and total: it computes a decision only, mutating nothing. The shell
    persists ``new_spent`` BEFORE the provider call and never credits it back if
    the call then fails (Req 7.7).

    Args:
        state: The provider's current spend snapshot, or ``None`` when the
            accounting record is unreadable or its read timed out.
        cost_unit: The Cost_Unit the billable operation would charge.

    Returns:
        A ``ChargeDecision`` describing whether to initiate the operation and the
        spend to persist when it is allowed.

    Validates: Requirements 7.1, 7.3, 7.4
    """
    # Fail closed: an unreadable / timed-out accounting record is treated as no
    # remaining budget, so no billable operation is initiated (Req 7.4).
    if state is None:
        return ChargeDecision(
            allowed=False,
            new_spent=0,
            reason=REASON_ACCOUNTING_UNAVAILABLE,
        )

    # Refuse an over-budget charge, leaving the spend counters unchanged (Req 7.3).
    if not can_afford(state, cost_unit):
        return ChargeDecision(
            allowed=False,
            new_spent=state.spent,
            reason=REASON_INSUFFICIENT_BUDGET,
        )

    # Allow the charge: the new spend is what the shell persists before the call.
    return ChargeDecision(
        allowed=True,
        new_spent=state.spent + cost_unit,
        reason=REASON_OK,
    )


def is_cached_free(cache_hit: bool) -> bool:
    """Return True iff an unexpired cached result makes the operation free.

    A cache hit means the requested operation has an unexpired cached result, so
    the operation is not initiated and no Cost_Unit is charged (Req 7.6). Pure
    and total.

    Args:
        cache_hit: True iff the applicable cache holds an unexpired result for
            the requested operation.

    Returns:
        True iff the operation is served from cache and costs nothing.

    Validates: Requirements 7.6
    """
    return cache_hit


# ---------------------------------------------------------------------------
# Thin best-effort I/O shell: charge_before_call
# ---------------------------------------------------------------------------
#
# The pure core above decides *whether* a billable operation may be charged. This
# shell does the *I/O*: it reads the provider's ``SpendState`` for the current
# accounting window, applies ``charge_decision``, and persists the new spend
# BEFORE the caller initiates the provider call (charge-before-call ordering, so
# a recorded charge always precedes spend). It mirrors the convention of
# ``quota_store.py`` (pure decision core, tiny best-effort I/O entrypoint).
#
# Fail closed. Like ``quota_store``, a failure here must never silently permit
# spend: an unreadable / unwritable / slow accounting record yields a ``None``
# state, which ``charge_decision`` turns into ``allowed=False`` so the caller
# skips the call (Req 7.4). A charge that is allowed and persisted is never
# credited back on a later failure (Req 7.7) -- the persisted spend simply stays.
#
# Each provider's budget is read and written under its OWN ``(provider_id,
# window_key)`` key in ``provider_spend`` so providers never interfere (Req 7.2).
# The ``youtube`` provider is special: it reuses the existing per-project
# ``Key_Pool`` accounting (``project_quota_usage`` via ``quota_store`` /
# ``quota_pool``) and writes NO ``provider_spend`` row, so no quota counter is
# duplicated (Req 7.5).
#
# Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.7

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.services import quota_pool, quota_store
from app.services.content_provider import ProviderCapability

logger = logging.getLogger(__name__)

#: The Provider_Id whose Provider_Cost_Policy IS the existing Key_Pool (Req 7.5).
YOUTUBE_PROVIDER_ID: str = "youtube"

#: Per-provider spend accounting table (see scripts/migration_alt_streams.sql).
#: YouTube is NOT stored here; it reuses project_quota_usage (Req 7.2, 7.5).
_SPEND_TABLE: str = "provider_spend"

#: Operator-configured registry table holding each provider's Provider_Cost_Policy.
_REGISTRY_TABLE: str = "provider_registry"

#: A read or write that does not complete within this many seconds is treated as
#: a failure and fails closed (no remaining budget) (Req 7.4).
_ACCOUNTING_TIMEOUT_SECONDS: float = 5.0

#: Cost_Unit charged against the Key_Pool for each YouTube billable operation.
#: A transcript fetch is not a YouTube-quota operation, so it is absent here and
#: charged nothing (mirrors ``youtube._fetch_transcript``).
_YOUTUBE_OP_COST: dict[ProviderCapability, int] = {
    ProviderCapability.SEARCH: quota_pool.SEARCH_COST,
    ProviderCapability.FETCH_METADATA: quota_pool.METADATA_COST,
}


def _window_key(now_utc: datetime | None) -> str:
    """Return the current accounting-window key (UTC calendar date).

    Each provider's spend is bounded within its current accounting window; the
    window key is the UTC date so spend rolls over daily. A naive datetime is
    assumed to be UTC. Pure helper (no I/O).
    """
    when = now_utc if now_utc is not None else datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).date().isoformat()


def _run_with_timeout(fn):
    """Run ``fn`` with the accounting I/O timeout, raising on timeout or error.

    The 5-second bound applies to a single read or write; exceeding it (or any
    other failure) propagates so the caller can fail closed (Req 7.4).
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(fn).result(timeout=_ACCOUNTING_TIMEOUT_SECONDS)


def _read_accounting(
    provider_id: str,
    window_key: str,
    op: ProviderCapability,
) -> tuple["SpendState | None", int | None]:
    """Read a non-YouTube provider's ``SpendState`` and the op's ``Cost_Unit``.

    Reads the provider's Provider_Cost_Policy from ``provider_registry`` (the
    ``Cost_Unit`` per op and the ``Spend_Budget``) and its consumed spend for the
    window from ``provider_spend`` under the provider's own
    ``(provider_id, window_key)`` key (Req 7.2). A provider with no spend row yet
    is at the start of a fresh window (``spent = 0``).

    Returns ``(SpendState, cost_unit)`` on success, or ``(None, None)`` when the
    provider is not registered, the policy lacks a cost for ``op``, or the policy
    declares no budget -- each of which makes the provider unspendable so the
    caller fails closed. Raises on a DB error so the caller's timeout/except path
    also fails closed.
    """
    from app.db.supabase import get_client

    db = get_client()

    reg = (
        db.table(_REGISTRY_TABLE)
        .select("cost_policy")
        .eq("provider_id", provider_id)
        .limit(1)
        .execute()
    )
    reg_rows = reg.data or []
    if not reg_rows:
        # Not registered -> treated as absent from the registry; nothing to spend.
        return None, None

    cost_policy = reg_rows[0].get("cost_policy") or {}
    cost_units = cost_policy.get("cost_units") or {}
    spend_budget = cost_policy.get("spend_budget")
    # jsonb keys are strings; ProviderCapability is a str enum so ``op.value`` is
    # the stored key (e.g. "search").
    cost_unit = cost_units.get(op.value, cost_units.get(str(op)))
    if cost_unit is None or spend_budget is None:
        return None, None

    spend = (
        db.table(_SPEND_TABLE)
        .select("spent, budget")
        .eq("provider_id", provider_id)
        .eq("window_key", window_key)
        .limit(1)
        .execute()
    )
    spend_rows = spend.data or []
    if spend_rows:
        spent = int(spend_rows[0].get("spent") or 0)
        row_budget = spend_rows[0].get("budget")
        budget = int(row_budget) if row_budget is not None else int(spend_budget)
    else:
        # Fresh window: no spend yet, budget from the provider's cost policy.
        spent = 0
        budget = int(spend_budget)

    state = SpendState(
        provider_id=provider_id,
        spent=max(0, spent),
        budget=max(0, budget),
    )
    return state, int(cost_unit)


def _persist_spend(
    provider_id: str,
    window_key: str,
    new_spent: int,
    budget: int,
) -> None:
    """Persist the charged spend under the provider's own key (Req 7.1, 7.2).

    Upserts the ``(provider_id, window_key)`` row so the charge is durable BEFORE
    the caller initiates the provider call. Raises on a DB error so the caller
    fails closed and does not spend.
    """
    from app.db.supabase import get_client

    get_client().table(_SPEND_TABLE).upsert(
        {
            "provider_id": provider_id,
            "window_key": window_key,
            "spent": int(new_spent),
            "budget": int(budget),
        },
        on_conflict="provider_id,window_key",
    ).execute()


def _charge_youtube_via_key_pool(
    op: ProviderCapability,
    now_utc: datetime | None,
) -> bool:
    """Charge a YouTube billable op against the existing Key_Pool (Req 7.5).

    Reuses the per-project quota accounting (``project_quota_usage`` via
    ``quota_store`` / ``quota_pool``) WITHOUT creating a ``provider_spend`` row,
    so no quota counter is duplicated. Selects the lowest-id affordable project
    over today's usage and persists the charge BEFORE returning ``True``,
    mirroring ``youtube.youtube_search``'s charge-before-call ordering. A
    transcript fetch is not a YouTube-quota operation, so it costs nothing.

    Follows ``quota_store``'s fail-closed convention: no affordable project, an
    unreadable usage table, or an unwritable charge all return ``False`` so the
    caller does not spend.
    """
    cost = _YOUTUBE_OP_COST.get(op)
    if cost is None:
        # Not a Key_Pool-charged operation (e.g. fetch_transcript): no charge.
        return True
    try:
        # ``load_today`` already fails closed (unreadable -> all projects
        # unaffordable), and ``select_project`` returns None when none can afford.
        projects = quota_store.load_today(now_utc)
        project_id = quota_pool.select_project(projects, cost)
        if project_id is None:
            logger.info(
                "[provider_cost] youtube op=%s: no project can afford %s units; "
                "failing closed",
                op.value,
                cost,
            )
            return False
        # Charge BEFORE the call via the Key_Pool; ``charge_and_persist`` returns
        # False on any write failure (fail closed, no spend).
        return quota_store.charge_and_persist(project_id, cost, now_utc=now_utc)
    except Exception as exc:
        logger.warning(
            "[provider_cost] youtube op=%s Key_Pool charge failed; failing "
            "closed: %s",
            op.value,
            exc,
        )
        return False


def charge_before_call(
    provider_id: str,
    op: ProviderCapability,
    *,
    cache_hit: bool,
    now_utc: datetime | None = None,
) -> bool:
    """Charge a provider's billable op before the call, returning whether to proceed.

    The thin best-effort I/O shell over the pure cost-accounting core. Order of
    operations:

    1. **Reuse before spend.** A cache hit costs nothing: short-circuit to
       ``True`` with no charge (Req 7.6).
    2. **YouTube** reuses the existing Key_Pool accounting via ``quota_store`` /
       ``quota_pool`` and writes no ``provider_spend`` row, so no quota counter is
       duplicated (Req 7.5).
    3. **Every other provider** is read and written under its OWN
       ``(provider_id, window_key)`` key so providers never interfere (Req 7.2).
       The provider's ``SpendState`` is read, ``charge_decision`` applied, and the
       new spend persisted BEFORE returning ``True`` (charge-before-call,
       Req 7.1).

    Fails closed: a read or write that fails or exceeds 5 seconds yields a
    ``None`` state, which ``charge_decision`` turns into ``allowed=False`` so the
    caller does not spend (Req 7.4). A persisted charge is never credited back if
    the operation later fails -- the spend simply stays (Req 7.7). Best-effort:
    all I/O is wrapped, failures are logged with ``provider_id`` and a reason, and
    nothing raises.

    Args:
        provider_id: The Provider_Id issuing the billable operation.
        op: The operation being charged (its Cost_Unit comes from the provider's
            Provider_Cost_Policy).
        cache_hit: True iff the applicable cache holds an unexpired result for the
            operation, in which case it is free.
        now_utc: Optional clock injection for the accounting window; defaults to
            the current UTC time.

    Returns:
        ``True`` iff the caller may initiate the operation (the charge, if any,
        has been persisted); ``False`` when the operation must not be initiated.

    Validates: Requirements 7.1, 7.2, 7.4, 7.5, 7.6, 7.7
    """
    # 1. Reuse before spend: a cache hit is free (Req 7.6).
    if is_cached_free(cache_hit):
        return True

    # 2. YouTube reuses the Key_Pool; no second counter / provider_spend row (Req 7.5).
    if provider_id == YOUTUBE_PROVIDER_ID:
        return _charge_youtube_via_key_pool(op, now_utc)

    # 3. Every other provider: read its own SpendState, decide, persist before call.
    window_key = _window_key(now_utc)

    # Read fails or exceeds 5s -> None state -> fail closed (Req 7.4).
    try:
        state, cost_unit = _run_with_timeout(
            lambda: _read_accounting(provider_id, window_key, op)
        )
    except Exception as exc:
        logger.warning(
            "[provider_cost] accounting read failed/timed out for provider=%s "
            "op=%s; failing closed: %s",
            provider_id,
            op.value,
            exc,
        )
        state, cost_unit = None, None

    # ``cost_unit`` is None exactly when ``state`` is None, so the 0 passed here is
    # only reached on the fail-closed path where ``charge_decision`` ignores it.
    decision = charge_decision(state, cost_unit if cost_unit is not None else 0)
    if not decision.allowed:
        logger.info(
            "[provider_cost] charge refused for provider=%s op=%s: %s",
            provider_id,
            op.value,
            decision.reason,
        )
        return False

    # Persist the charge BEFORE the call (Req 7.1); never credited back later (Req 7.7).
    # A write failure / timeout fails closed: the caller does not spend.
    assert state is not None  # allowed implies a readable state
    try:
        _run_with_timeout(
            lambda: _persist_spend(
                provider_id, window_key, decision.new_spent, state.budget
            )
        )
    except Exception as exc:
        logger.warning(
            "[provider_cost] charge could not be persisted for provider=%s "
            "op=%s; failing closed (no spend): %s",
            provider_id,
            op.value,
            exc,
        )
        return False

    return True
