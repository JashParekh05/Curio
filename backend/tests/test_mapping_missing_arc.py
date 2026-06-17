"""Unit test for the Segment_Mapper missing-Planned_Arc branch.

When ``map_and_assemble`` is given a Planned_Arc with no roles (the arc was not
produced), it maps nothing, flags ``arc_missing=True``, and records a coherence
defect indicating the missing arc. The empty-arc branch short-circuits without
assembling or evaluating, so no segment is ever admitted.

Validates: Requirements 2.3
"""
from app.models.schemas import LearningAtom, PlannedArc
from app.services.segment_mapper import map_and_assemble


def _atom(i: int) -> LearningAtom:
    return LearningAtom(
        id=f"atom-{i}",
        topic_slug="topic",
        video_id="vid",
        source_url="https://example.com/v",
        role="definition",
        concept=f"concept-{i}",
        prior_knowledge=[],
        start=float(i),
        end=float(i) + 1.0,
    )


def test_missing_arc_maps_nothing_and_records_defect():
    # An empty Planned_Arc: no roles were produced for the topic.
    empty_arc = PlannedArc(
        topic_slug="topic",
        concept_type="default",
        template_empty=True,
        roles=[],
    )
    atoms = [_atom(0), _atom(1), _atom(2)]

    outcome = map_and_assemble(atoms, empty_arc)

    # The arc is flagged missing.
    assert outcome.arc_missing is True

    # Nothing is mapped, nothing assembled, nothing set aside.
    assert outcome.mapping.mapped == []
    assert outcome.mapping.unmapped == []
    assert outcome.mapping.unfilled_roles == []
    assert outcome.clips == []

    # A coherence defect is recorded for the missing arc (Req 2.3).
    assert len(outcome.coherence.defects) == 1
    defect = outcome.coherence.defects[0]
    assert defect.defect_type == "missing_piece"

    # Alignment is unresolved so admission is withheld downstream.
    assert outcome.alignment.aligned is False
    assert outcome.alignment.unresolved is True


def test_missing_arc_with_no_atoms_still_short_circuits():
    empty_arc = PlannedArc(topic_slug="topic", concept_type="default", roles=[])

    outcome = map_and_assemble([], empty_arc)

    assert outcome.arc_missing is True
    assert outcome.mapping.mapped == []
    assert outcome.clips == []
    assert len(outcome.coherence.defects) == 1
    assert outcome.coherence.defects[0].defect_type == "missing_piece"
