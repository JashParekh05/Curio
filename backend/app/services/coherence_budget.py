"""Cost/retry guard for model calls in the coherence pipeline.

Provides three pure-policy constructs that enforce the budget and retry
limits from Requirement 8.2 and 8.9.  All decision logic is implemented
as pure functions or a lightweight in-process counter with no I/O —
matching the pattern in ``self_heal_state.py``.

  - ``next_retry_decision``: determines whether another attempt is allowed
    given an attempt index and configured max retries (Req 8.2).
  - ``call_within_timeout``: runs a callable with a per-call timeout, raising
    ``TimeoutError`` (which counts as a failed attempt) when exceeded (Req 8.2).
  - ``ModelCallBudget``: per-topic-per-cycle call counter that denies once the
    configured maximum is exhausted (Req 8.9).

Best-effort throughout; reset on restart by design.  ASCII only.
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Policy constants (used as defaults; callers may override via arguments)
# ---------------------------------------------------------------------------

#: Default number of retry attempts before giving up (Req 8.2, range 0-5).
DEFAULT_MAX_RETRIES: int = 2

#: Default per-call timeout in seconds (Req 8.2, range 1-60).
DEFAULT_TIMEOUT_SECONDS: float = 10.0

#: Default maximum total model calls per topic per generation cycle (Req 8.9).
DEFAULT_MAX_CALLS: int = 20


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _clamp_int(value: int, lo: int, hi: int) -> int:
    """Return value clamped to the inclusive range [lo, hi]."""
    return max(lo, min(hi, value))


def _clamp_float(value: float, lo: float, hi: float) -> float:
    """Return value clamped to the inclusive range [lo, hi]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# next_retry_decision
# ---------------------------------------------------------------------------

def next_retry_decision(attempt: int, max_retries: int = DEFAULT_MAX_RETRIES) -> bool:
    """Return True when another attempt is allowed.

    Another attempt is allowed if and only if ``attempt`` is strictly less
    than the effective maximum, where the effective maximum is
    ``clamp(max_retries, 0, 5)``.

    ``attempt`` is the *current* zero-based attempt index: 0 means the first
    call has just been made (or is about to be made), so
    ``next_retry_decision(0, 2)`` returns True (attempts 1 and 2 remain).
    ``next_retry_decision(2, 2)`` returns False (budget exhausted).

    Args:
        attempt: Zero-based index of the attempt that was just attempted.
        max_retries: Maximum number of *retry* attempts (not counting the
            first call).  Clamped to [0, 5]; defaults to
            ``DEFAULT_MAX_RETRIES`` (2).

    Returns:
        True if another attempt is allowed, False otherwise.

    Validates: Requirements 8.2
    """
    effective_max = _clamp_int(max_retries, 0, 5)
    return attempt < effective_max


# ---------------------------------------------------------------------------
# call_within_timeout
# ---------------------------------------------------------------------------

def call_within_timeout(
    fn: Callable[..., Any],
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
    /,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run *fn* and return its result, raising ``TimeoutError`` if it exceeds
    the effective timeout.

    The effective timeout is ``clamp(timeout_s, 1.0, 60.0)``.  A timeout is
    treated by the caller as a failed attempt (Req 8.2).

    Uses ``concurrent.futures.ThreadPoolExecutor`` to run the callable in a
    thread and wait with a deadline.  The thread cannot be forcibly cancelled
    once the timeout fires, but the exception is raised to the caller
    immediately so it can record the failure and proceed.

    Args:
        fn: Callable to invoke.
        timeout_s: Per-call timeout in seconds.  Clamped to [1.0, 60.0];
            defaults to ``DEFAULT_TIMEOUT_SECONDS`` (10 s).
        *args: Positional arguments forwarded to *fn*.
        **kwargs: Keyword arguments forwarded to *fn*.

    Returns:
        The return value of *fn*.

    Raises:
        TimeoutError: If *fn* does not return within the effective timeout.
        Exception: Any exception raised by *fn* is propagated as-is.

    Validates: Requirements 8.2
    """
    effective_timeout = _clamp_float(float(timeout_s), 1.0, 60.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, *args, **kwargs)
        try:
            return future.result(timeout=effective_timeout)
        except concurrent.futures.TimeoutError:
            raise TimeoutError(
                f"call_within_timeout: {getattr(fn, '__name__', repr(fn))!r} "
                f"exceeded {effective_timeout:.1f}s timeout"
            )


# ---------------------------------------------------------------------------
# ModelCallBudget
# ---------------------------------------------------------------------------

class ModelCallBudget:
    """Per-topic-per-cycle counter that denies model calls once exhausted.

    Construct one instance per topic per generation cycle.  Call
    ``allow()`` before each model call; once the counter reaches the
    effective maximum it returns False for all subsequent calls.

    The effective maximum is ``clamp(max_calls, 1, 100)``.  Defaults to
    ``DEFAULT_MAX_CALLS`` (20).

    Mirrors the in-process guard pattern in ``self_heal_state.py``:
    pure policy state, no I/O, reset on restart by design.

    Example::

        budget = ModelCallBudget(max_calls=5)
        for _ in range(6):
            if not budget.allow():
                # fall back to best available result
                break
            result = call_model(...)

    Validates: Requirements 8.9
    """

    def __init__(self, max_calls: int = DEFAULT_MAX_CALLS) -> None:
        self._max_calls: int = _clamp_int(max_calls, 1, 100)
        self._used: int = 0

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def max_calls(self) -> int:
        """The effective (clamped) maximum number of model calls allowed."""
        return self._max_calls

    @property
    def used(self) -> int:
        """Number of model calls consumed so far in this cycle."""
        return self._used

    @property
    def remaining(self) -> int:
        """Number of model calls remaining before the budget is exhausted."""
        return max(0, self._max_calls - self._used)

    @property
    def exhausted(self) -> bool:
        """True once the budget has been fully consumed."""
        return self._used >= self._max_calls

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def allow(self) -> bool:
        """Consume one call from the budget and return True, or return False
        when the budget is already exhausted.

        Thread-safety: this class is intentionally single-threaded (one
        background task per cycle).  No locking is applied.

        Returns:
            True if the call is permitted (budget incremented), False if the
            budget is exhausted (budget unchanged).
        """
        if self._used >= self._max_calls:
            return False
        self._used += 1
        return True

    def reset(self) -> None:
        """Reset the counter to zero (useful in tests or between sub-phases)."""
        self._used = 0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ModelCallBudget(used={self._used}, "
            f"max_calls={self._max_calls}, "
            f"exhausted={self.exhausted})"
        )
