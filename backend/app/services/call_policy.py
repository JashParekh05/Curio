"""Call_Policy pure decision core (Phase 2, Req 11).

This module holds the bounded-retry / per-call-timeout decision and the
best-available fallback resolution for every model or external call made off the
request path -- Arc_Backfill, reseeding, spine maintenance, Placement, and
Takeaway generation. The rest of Requirement 11 (work scheduled outside the
request/response path, spend only through the Key_Pool / Content_Provider, the
already-Arc_Complete no-op) is a property of *where* the shells schedule work,
not a decision this core makes; the shells own that and call into these pure
functions for the call bounds and the fallback flag.

It holds ONLY the pure decision logic -- no DB, no clock, no globals read --
mirroring the convention in ``reseed_prioritizer.py`` and ``content_provider.py``
(the matching best-effort behaviour lives in the shells). Every function here is
total and deterministic in its inputs, free of side effects, and never raises on
valid-typed input: it returns an explicit result value rather than signalling an
error, so a failed call can never propagate into a learner-facing request
(Req 11.4, 11.7).

``validate_call_policy`` clamps a requested retry count into the inclusive range
[1, 5] and a requested per-call timeout into the inclusive range [1000, 30000]
milliseconds (Req 11.3). ``resolve_outcome`` turns the terminal state of a call --
whether its retries were exhausted or its timeout exceeded -- into the best
available result (the last successfully computed or previously stored result) and
flags whether that result was a fallback, never signalling a learner-facing error
(Req 11.4).

ASCII only.

Validates: Requirements 11.3, 11.4, 11.7
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Policy bounds (Req 11.3)
# ---------------------------------------------------------------------------

#: Inclusive minimum number of attempts a bounded call may be configured for.
MIN_RETRIES: int = 1

#: Inclusive maximum number of attempts a bounded call may be configured for.
MAX_RETRIES: int = 5

#: Inclusive minimum per-call timeout, in milliseconds.
MIN_TIMEOUT_MS: int = 1000

#: Inclusive maximum per-call timeout, in milliseconds.
MAX_TIMEOUT_MS: int = 30000


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallPolicy:
    """A validated bound on a single model or external call (Req 11.3).

    Attributes:
        max_retries: The maximum number of attempts, clamped to the inclusive
            range [``MIN_RETRIES``, ``MAX_RETRIES``] = [1, 5].
        timeout_ms: The per-call timeout in milliseconds, clamped to the
            inclusive range [``MIN_TIMEOUT_MS``, ``MAX_TIMEOUT_MS``] =
            [1000, 30000].
    """

    max_retries: int
    timeout_ms: int


@dataclass(frozen=True)
class CallOutcome:
    """The resolved outcome of a bounded call (Req 11.4).

    Attributes:
        value: The best available result -- the last successfully computed or
            previously stored result for the operation -- or ``None`` when no
            such result exists. The caller is never handed a learner-facing
            error in its place.
        used_fallback: ``True`` when the call's retries were exhausted or its
            timeout was exceeded, so ``value`` is a fallback rather than a fresh
            success; ``False`` only when the call produced a fresh successful
            result without exhausting retries or timing out.
    """

    value: object | None
    used_fallback: bool


# ---------------------------------------------------------------------------
# Pure core: policy validation (Req 11.3, 11.7)
# ---------------------------------------------------------------------------

def _clamp_int(value: int, low: int, high: int) -> int:
    """Clamp ``value`` to the inclusive integer range [``low``, ``high``]."""
    if value < low:
        return low
    if value > high:
        return high
    return value


def validate_call_policy(max_retries: int, timeout_ms: int) -> CallPolicy:
    """Clamp a requested retry count and timeout into the valid ranges.

    The returned ``CallPolicy`` always carries a ``max_retries`` in the inclusive
    range [1, 5] and a ``timeout_ms`` in the inclusive range [1000, 30000]
    milliseconds, regardless of how far out of range the request was (Req 11.3).

    Args:
        max_retries: The requested maximum number of attempts.
        timeout_ms: The requested per-call timeout, in milliseconds.

    Returns:
        A ``CallPolicy`` with both fields clamped to their valid ranges.

    Pure and total: depends only on its inputs, performs no I/O, never raises.

    Validates: Requirements 11.3, 11.7
    """
    return CallPolicy(
        max_retries=_clamp_int(max_retries, MIN_RETRIES, MAX_RETRIES),
        timeout_ms=_clamp_int(timeout_ms, MIN_TIMEOUT_MS, MAX_TIMEOUT_MS),
    )


# ---------------------------------------------------------------------------
# Pure core: best-available fallback resolution (Req 11.4, 11.7)
# ---------------------------------------------------------------------------

def resolve_outcome(
    last_success: object | None,
    attempts_exhausted: bool,
    timed_out: bool,
) -> CallOutcome:
    """Resolve a call's terminal state into its best available result.

    When a call exhausts its configured retry attempts (``attempts_exhausted``)
    or exceeds its configured timeout (``timed_out``), the component falls back to
    the best available result -- the last successfully computed or previously
    stored result, passed in as ``last_success`` -- and flags that the fallback
    was used. When neither condition holds the call produced a fresh success and
    ``last_success`` is returned without the fallback flag. In every case the
    result is returned as a value: this function never signals a learner-facing
    error and never raises, so a failed call cannot fail any learner-facing
    request (Req 11.4).

    Args:
        last_success: The last successfully computed or previously stored result
            for the operation, or ``None`` when none exists.
        attempts_exhausted: ``True`` when every configured retry attempt failed.
        timed_out: ``True`` when the call exceeded its configured timeout.

    Returns:
        A ``CallOutcome`` whose ``value`` is ``last_success`` and whose
        ``used_fallback`` is ``True`` iff the call exhausted its retries or timed
        out.

    Pure and total: depends only on its inputs, performs no I/O, never raises.

    Validates: Requirements 11.4, 11.7
    """
    used_fallback = bool(attempts_exhausted or timed_out)
    return CallOutcome(value=last_success, used_fallback=used_fallback)
