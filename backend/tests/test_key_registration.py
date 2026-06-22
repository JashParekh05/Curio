"""Unit tests for operator-provisioned key registration (Task 3.2).

Exercises the pure key-registration decision core in
``app.services.quota_pool``: ``register_key`` and its supporting predicates
(``is_present``, ``is_well_formed``, ``is_duplicate_key``), plus the budget /
counting accessors (``pool_total_budget``, ``project_count``) and the
``empty_pool`` initializer.

These are example-based tests (not property tests). They cover:

  - well-formed add for a NEW project (budget grows by exactly DAILY_QUOTA);
  - well-formed associate to an EXISTING project (budget unchanged);
  - empty-pool initialization on the first registration;
  - each rejection path -- missing (null / empty / whitespace), malformed, and
    duplicate -- each leaving the input pool's entries and budget unchanged;
  - per-project budget additivity across a sequence of registrations.

Well-formed keys use the real Google API key shape: the literal ``AIza``
prefix followed by 35 URL-safe characters (39 chars total).

Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7
"""
import pytest

from app.services.quota_pool import (
    ACTION_ADD,
    ACTION_ASSOCIATE,
    ACTION_REJECT,
    DAILY_QUOTA,
    REASON_DUPLICATE,
    REASON_MALFORMED,
    REASON_MISSING,
    KeyPool,
    empty_pool,
    is_duplicate_key,
    is_present,
    is_well_formed,
    pool_total_budget,
    project_count,
    register_key,
    registered_keys,
)

# ---------------------------------------------------------------------------
# Well-formed sample keys (AIza + 35 url-safe chars == 39 chars).
# ---------------------------------------------------------------------------
KEY_A = "AIza" + "A" * 35
KEY_B = "AIza" + "B" * 35
KEY_C = "AIza" + "0123456789" + "abcdefghijklmnopqrstuvw" + "_-"  # 4 + 35 mixed


def _len_ok(k: str) -> bool:
    return len(k) == 39


def test_sample_keys_are_well_formed():
    """Guard: the sample keys used by these tests are valid 39-char keys."""
    for k in (KEY_A, KEY_B, KEY_C):
        assert _len_ok(k)
        assert is_well_formed(k)


# ---------------------------------------------------------------------------
# Empty pool initialization (Req 7.4)
# ---------------------------------------------------------------------------

def test_empty_pool_has_no_projects_and_zero_budget():
    pool = empty_pool()
    assert project_count(pool) == 0
    assert pool_total_budget(pool) == 0
    assert registered_keys(pool) == frozenset()


def test_first_registration_initializes_empty_pool():
    """Req 7.4: first well-formed key inits the pool and sets budget to 10000."""
    pool = empty_pool()
    result = register_key(pool, "proj-1", KEY_A)

    assert result.action == ACTION_ADD
    assert result.new_project is True
    assert result.accepted is True
    assert result.reason is None
    assert project_count(result.pool) == 1
    assert pool_total_budget(result.pool) == DAILY_QUOTA
    assert KEY_A in registered_keys(result.pool)
    # Input pool left unchanged (purity).
    assert project_count(pool) == 0
    assert pool_total_budget(pool) == 0


# ---------------------------------------------------------------------------
# Well-formed add for a NEW project (Req 7.1, 7.2)
# ---------------------------------------------------------------------------

def test_add_new_project_increases_budget_by_daily_quota():
    """Req 7.2: adding a key for a NEW project grows budget by exactly 10000."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    before = pool_total_budget(pool)

    result = register_key(pool, "proj-2", KEY_B)

    assert result.action == ACTION_ADD
    assert result.new_project is True
    assert project_count(result.pool) == 2
    assert pool_total_budget(result.pool) == before + DAILY_QUOTA
    assert {KEY_A, KEY_B} <= registered_keys(result.pool)


# ---------------------------------------------------------------------------
# Well-formed associate to an EXISTING project (Req 7.3)
# ---------------------------------------------------------------------------

def test_associate_existing_project_leaves_budget_unchanged():
    """Req 7.3: a second key for the SAME project leaves budget unchanged."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    before = pool_total_budget(pool)

    result = register_key(pool, "proj-1", KEY_B)

    assert result.action == ACTION_ASSOCIATE
    assert result.new_project is False
    assert result.accepted is True
    assert project_count(result.pool) == 1
    assert pool_total_budget(result.pool) == before  # unchanged
    # Both keys now belong to the single project.
    assert {KEY_A, KEY_B} <= registered_keys(result.pool)
    # Input pool unchanged.
    assert registered_keys(pool) == frozenset({KEY_A})


# ---------------------------------------------------------------------------
# Rejection: missing value (Req 7.5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing", [None, "", "   ", "\t", "\n", " \t \n "])
def test_reject_missing_value_leaves_pool_unchanged(missing):
    """Req 7.5: null / empty / whitespace-only -> reject(missing), no change."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    before_keys = registered_keys(pool)
    before_budget = pool_total_budget(pool)

    result = register_key(pool, "proj-2", missing)

    assert result.action == ACTION_REJECT
    assert result.reason == REASON_MISSING
    assert result.accepted is False
    assert result.new_project is False
    # Pool state preserved.
    assert result.pool is pool
    assert registered_keys(result.pool) == before_keys
    assert pool_total_budget(result.pool) == before_budget


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_is_present_false_for_missing(missing):
    assert is_present(missing) is False


# ---------------------------------------------------------------------------
# Rejection: malformed value (Req 7.6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "malformed",
    [
        "not-a-key",
        "AIza" + "A" * 34,           # one char too short
        "AIza" + "A" * 36,           # one char too long
        "BIza" + "A" * 35,           # wrong prefix
        "AIza" + "A" * 34 + "!",     # invalid char (!)
        "  " + KEY_A,                # leading whitespace (present but malformed)
        KEY_A + "  ",                # trailing whitespace
        "AIza" + "A" * 33 + " B",    # embedded space
    ],
)
def test_reject_malformed_value_leaves_pool_unchanged(malformed):
    """Req 7.6: present but not well-formed -> reject(malformed), no change."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    before_keys = registered_keys(pool)
    before_budget = pool_total_budget(pool)

    # Sanity: these are present (so not 'missing') but not well-formed.
    assert is_present(malformed) is True
    assert is_well_formed(malformed) is False

    result = register_key(pool, "proj-2", malformed)

    assert result.action == ACTION_REJECT
    assert result.reason == REASON_MALFORMED
    assert result.accepted is False
    assert result.new_project is False
    assert result.pool is pool
    assert registered_keys(result.pool) == before_keys
    assert pool_total_budget(result.pool) == before_budget


# ---------------------------------------------------------------------------
# Rejection: duplicate value (Req 7.7)
# ---------------------------------------------------------------------------

def test_reject_duplicate_in_same_project_leaves_pool_unchanged():
    """Req 7.7: re-registering an existing key (same project) is rejected."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    before_keys = registered_keys(pool)
    before_budget = pool_total_budget(pool)

    assert is_duplicate_key(pool, KEY_A) is True

    result = register_key(pool, "proj-1", KEY_A)

    assert result.action == ACTION_REJECT
    assert result.reason == REASON_DUPLICATE
    assert result.accepted is False
    assert result.pool is pool
    assert registered_keys(result.pool) == before_keys
    assert pool_total_budget(result.pool) == before_budget


def test_reject_duplicate_across_projects_leaves_pool_unchanged():
    """Req 7.7: an exact-value match in ANOTHER project is still a duplicate."""
    pool = register_key(empty_pool(), "proj-1", KEY_A).pool
    pool = register_key(pool, "proj-2", KEY_B).pool
    before_keys = registered_keys(pool)
    before_budget = pool_total_budget(pool)

    # Submit KEY_A (already in proj-1) but for a different project id.
    result = register_key(pool, "proj-3", KEY_A)

    assert result.action == ACTION_REJECT
    assert result.reason == REASON_DUPLICATE
    assert result.pool is pool
    assert registered_keys(result.pool) == before_keys
    assert pool_total_budget(result.pool) == before_budget
    assert project_count(result.pool) == 2  # proj-3 was not created


# ---------------------------------------------------------------------------
# Budget additivity across a sequence of registrations (Req 7.2, 7.3)
# ---------------------------------------------------------------------------

def test_budget_additivity_over_mixed_sequence():
    """Budget grows only per NEW project; associates and rejects don't move it.

    Sequence:
      add KEY_A -> proj-1   (new)        budget 10000
      add KEY_B -> proj-1   (associate)  budget 10000
      add KEY_C -> proj-2   (new)        budget 20000
      dup KEY_A -> proj-2   (reject)     budget 20000
      bad ''    -> proj-3   (reject)     budget 20000
    """
    pool = empty_pool()

    pool = register_key(pool, "proj-1", KEY_A).pool
    assert pool_total_budget(pool) == DAILY_QUOTA
    assert project_count(pool) == 1

    pool = register_key(pool, "proj-1", KEY_B).pool  # associate
    assert pool_total_budget(pool) == DAILY_QUOTA
    assert project_count(pool) == 1

    pool = register_key(pool, "proj-2", KEY_C).pool  # new project
    assert pool_total_budget(pool) == 2 * DAILY_QUOTA
    assert project_count(pool) == 2

    r_dup = register_key(pool, "proj-2", KEY_A)       # duplicate -> reject
    assert r_dup.action == ACTION_REJECT
    assert pool_total_budget(r_dup.pool) == 2 * DAILY_QUOTA

    r_missing = register_key(pool, "proj-3", "")      # missing -> reject
    assert r_missing.action == ACTION_REJECT
    assert pool_total_budget(r_missing.pool) == 2 * DAILY_QUOTA
    assert project_count(r_missing.pool) == 2

    # Final accepted pool holds exactly the three accepted keys across 2 projects.
    assert registered_keys(pool) == frozenset({KEY_A, KEY_B, KEY_C})
