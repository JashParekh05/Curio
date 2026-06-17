"""Budget & retry/timeout config for the deep-content-ingestion pipeline.

Pure-policy validators that bound the cost of the ingestion pipeline,
mirroring the conventions in ``coherence_budget.py``.  Every function here
is pure: deterministic in its inputs, with no DB, clock, or global reads
(Req 9.6).  The thin best-effort I/O shells that consume these validators
live elsewhere (``ingestion_pipeline.py`` / ``segment_judge.py``).

  - ``validate_model_budget``: clamp a configured per-topic-per-cycle model
    call budget to [1, 100], defaulting to 20 (Req 9.3).
  - ``within_model_budget``: decide whether another model call is allowed
    given the calls already made (Req 9.3).
  - ``validate_retries``: clamp a configured retry count to [0, 5],
    defaulting to 2 (Req 9.2).
  - ``validate_timeout``: clamp a configured per-call timeout to [1, 60]
    seconds, defaulting to 10 (Req 9.2).

ASCII only.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Policy constants (used as defaults when a configured value is missing/invalid)
# ---------------------------------------------------------------------------

#: Default maximum model calls per topic per generation cycle (Req 9.3, range 1-100).
DEFAULT_MODEL_BUDGET: int = 20

#: Default number of retry attempts before giving up (Req 9.2, range 0-5).
DEFAULT_RETRIES: int = 2

#: Default per-call timeout in seconds (Req 9.2, range 1-60).
DEFAULT_TIMEOUT_SECONDS: int = 10


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _coerce_int(raw: object) -> int | None:
    """Return ``raw`` as an int when it represents an integral value, else None.

    Accepts ``int`` (but not ``bool``), integral ``float`` values (e.g. ``5.0``),
    and numeric strings.  Anything else (``None``, non-numeric strings, NaN/inf,
    non-integral floats, arbitrary objects) yields None so the caller can fall
    back to its default.  Pure.
    """
    # bool is a subclass of int; reject it explicitly so True/False do not
    # masquerade as 1/0 budgets.
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        # Reject NaN/inf and non-integral floats.
        if raw != raw or raw in (float("inf"), float("-inf")):
            return None
        if raw != int(raw):
            return None
        return int(raw)
    if isinstance(raw, str):
        try:
            return int(raw.strip())
        except (ValueError, TypeError):
            return None
    return None


def _clamp_int(value: int, lo: int, hi: int) -> int:
    """Return ``value`` clamped to the inclusive range [lo, hi]. Pure."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# validate_model_budget
# ---------------------------------------------------------------------------

def validate_model_budget(raw: object) -> int:
    """Validate a configured model-call budget.

    A value that represents an integer is clamped to the inclusive range
    [1, 100].  When ``raw`` is missing, ``None``, or otherwise not a valid
    integer, the default of ``DEFAULT_MODEL_BUDGET`` (20) is returned.  Pure.

    Args:
        raw: The configured value (any type).

    Returns:
        An int in [1, 100].

    Validates: Requirements 9.3
    """
    value = _coerce_int(raw)
    if value is None:
        return DEFAULT_MODEL_BUDGET
    return _clamp_int(value, 1, 100)


# ---------------------------------------------------------------------------
# within_model_budget
# ---------------------------------------------------------------------------

def within_model_budget(calls_made: int, budget: int) -> bool:
    """Return True iff another model call is permitted.

    Another call is allowed if and only if ``calls_made`` is strictly less
    than ``budget``.  Once this returns False the pipeline stops issuing model
    calls for the Topic and finalizes with its best available result.  Pure.

    Args:
        calls_made: Number of model calls already issued this cycle.
        budget: The (already-validated) per-topic-per-cycle budget.

    Returns:
        True if ``calls_made < budget``, False otherwise.

    Validates: Requirements 9.3
    """
    return calls_made < budget


# ---------------------------------------------------------------------------
# validate_retries
# ---------------------------------------------------------------------------

def validate_retries(raw: object) -> int:
    """Validate a configured retry count.

    A value that represents an integer is clamped to the inclusive range
    [0, 5].  When ``raw`` is missing, ``None``, or otherwise not a valid
    integer, the default of ``DEFAULT_RETRIES`` (2) is returned.  Pure.

    Args:
        raw: The configured value (any type).

    Returns:
        An int in [0, 5].

    Validates: Requirements 9.2
    """
    value = _coerce_int(raw)
    if value is None:
        return DEFAULT_RETRIES
    return _clamp_int(value, 0, 5)


# ---------------------------------------------------------------------------
# validate_timeout
# ---------------------------------------------------------------------------

def validate_timeout(raw: object) -> int:
    """Validate a configured per-call timeout in seconds.

    A value that represents an integer is clamped to the inclusive range
    [1, 60] seconds.  When ``raw`` is missing, ``None``, or otherwise not a
    valid integer, the default of ``DEFAULT_TIMEOUT_SECONDS`` (10) is
    returned.  A call exceeding the resulting timeout counts as a failed
    attempt.  Pure.

    Args:
        raw: The configured value (any type).

    Returns:
        An int in [1, 60].

    Validates: Requirements 9.2
    """
    value = _coerce_int(raw)
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    return _clamp_int(value, 1, 60)
