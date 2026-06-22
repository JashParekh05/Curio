"""Backward-compatibility tests for the Clip model.

Verifies that a pre-feature Clip row — one persisted before the
learning-arc coherence columns existed (only id, topic_slug, title,
video_url) — still parses, and that all five new coherence fields
default to None.

_Requirements: 7.2_
"""

from app.models.schemas import Clip


# A row shaped like the database returned before the coherence feature
# added the new nullable columns. None of the new columns are present.
PRE_FEATURE_ROW = {
    "id": "clip-legacy-1",
    "topic_slug": "binary-search",
    "title": "Intro to Binary Search",
    "video_url": "https://example.com/video/legacy-1.mp4",
}


def test_pre_feature_row_still_parses():
    clip = Clip(**PRE_FEATURE_ROW)

    assert clip.id == "clip-legacy-1"
    assert clip.topic_slug == "binary-search"
    assert clip.title == "Intro to Binary Search"
    assert clip.video_url == "https://example.com/video/legacy-1.mp4"


def test_new_coherence_fields_default_to_none():
    clip = Clip(**PRE_FEATURE_ROW)

    assert clip.pedagogical_role is None
    assert clip.role_ordinal is None
    assert clip.concept_label is None
    assert clip.engagement_score is None
    assert clip.coherence_score is None
