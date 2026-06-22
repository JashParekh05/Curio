"""Unit test for exact pedagogical-arc template constants.

Pins down the literal role lists returned by ``select_template`` for the two
non-default concept types so the templates can't silently drift.

Requirements 1.4, 1.5
"""
from app.agents.section_planner import select_template


def test_problem_solving_template_exact():
    # Req 1.4
    assert select_template("problem_solving") == [
        "problem_statement",
        "meaning",
        "visualization",
        "approach",
        "worked_example",
        "edge_cases",
    ]


def test_conceptual_template_exact():
    # Req 1.5
    assert select_template("conceptual") == [
        "definition",
        "motivation",
        "mechanism",
        "example",
        "common_misconception",
    ]
