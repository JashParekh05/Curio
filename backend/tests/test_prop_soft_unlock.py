"""Property-based test for the Soft_Unlock pure decision core.

# Feature: structured-learn-curriculum, Property 4: Checkpoints are soft (never block the scroll) (P3.7-P3.11)

*For any* combination of ``mastered`` / ``is_next_unmastered`` flags and any list
of per-topic mastered flags, Soft_Unlock returns only advisory, non-blocking
state: ``topic_unlock`` never returns a locked value -- its result always lies in
``{"available", "recommended", "mastered"}`` and a non-mastered, non-next topic
maps to ``available`` (P3.7); a mastered topic maps to ``mastered`` and
``recommended`` is produced only for the earliest unmastered topic (P3.8);
``level_progress.percent_complete`` is bounded in [0, 100] and equals 100 iff the
non-empty Level is fully mastered (P3.9); an empty Level yields percent 0 and
``all_mastered is False`` (P3.10); and both functions are deterministic and total
(P3.11).

Imports only the pure module under test, so it runs offline with no external
service.

Validates: Requirements 3.3, 3.4
"""
from __future__ import annotations

from hypothesis import given, settings, strategies as st

from app.services.soft_unlock import LevelProgress, level_progress, topic_unlock

# The only statuses Soft_Unlock may ever produce -- there is intentionally NO
# locked/blocking value in this set.
_ALLOWED_STATUSES = {"available", "recommended", "mastered"}

_ORDINALS = st.integers(min_value=1, max_value=12)
_FLAG_LISTS = st.lists(st.booleans(), max_size=20)


@given(mastered=st.booleans(), is_next_unmastered=st.booleans())
@settings(max_examples=100)
def test_topic_unlock_never_blocks(mastered: bool, is_next_unmastered: bool) -> None:
    result = topic_unlock(mastered, is_next_unmastered)

    # P3.7: result is always one of the three advisory statuses -- never locked.
    assert result in _ALLOWED_STATUSES

    # P3.8: mastered takes precedence; recommended only for the earliest
    # unmastered topic; everything else is available.
    if mastered:
        assert result == "mastered"
    elif is_next_unmastered:
        assert result == "recommended"
    else:
        # P3.7: a non-mastered, non-next topic is always navigable.
        assert result == "available"


@given(ordinal=_ORDINALS, flags=_FLAG_LISTS)
@settings(max_examples=100)
def test_level_progress_percent_bounds_and_completion(
    ordinal: int, flags: list[bool]
) -> None:
    result = level_progress(ordinal, flags)
    assert isinstance(result, LevelProgress)
    assert result.ordinal == ordinal

    # P3.9: percent_complete is always bounded in [0, 100].
    assert 0 <= result.percent_complete <= 100

    if not flags:
        # P3.10: empty level -> percent 0 and all_mastered is False.
        assert result.percent_complete == 0
        assert result.all_mastered is False
        return

    all_mastered = all(flags)
    # P3.9: percent_complete == 100 iff the non-empty level is fully mastered.
    assert (result.percent_complete == 100) == all_mastered
    assert result.all_mastered == all_mastered


@given(
    mastered=st.booleans(),
    is_next_unmastered=st.booleans(),
    ordinal=_ORDINALS,
    flags=_FLAG_LISTS,
)
@settings(max_examples=100)
def test_soft_unlock_is_deterministic_and_total(
    mastered: bool, is_next_unmastered: bool, ordinal: int, flags: list[bool]
) -> None:
    # P3.11: determinism and totality -- never raises and identical inputs yield
    # identical output. Also confirm the input list is not mutated.
    snapshot = list(flags)

    assert topic_unlock(mastered, is_next_unmastered) == topic_unlock(
        mastered, is_next_unmastered
    )
    assert level_progress(ordinal, flags) == level_progress(ordinal, flags)
    assert flags == snapshot
