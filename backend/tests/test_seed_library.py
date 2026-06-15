from scripts.seed_library import _select_slugs, LIBRARY


def test_no_args_returns_full_library():
    assert _select_slugs([]) == LIBRARY


def test_integer_arg_slices_first_n():
    assert _select_slugs(["6"]) == LIBRARY[:6]


def test_explicit_slugs_passthrough():
    assert _select_slugs(["black-holes", "the-cold-war"]) == ["black-holes", "the-cold-war"]


def test_single_non_digit_is_a_slug_not_a_count():
    assert _select_slugs(["black-holes"]) == ["black-holes"]


def test_returns_a_copy_not_the_library_reference():
    out = _select_slugs([])
    out.append("mutated")
    assert "mutated" not in LIBRARY


def test_library_is_nonempty_and_has_no_duplicates():
    assert len(LIBRARY) > 0
    assert len(LIBRARY) == len(set(LIBRARY))
