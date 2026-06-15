"""Tests for narrative-aware segmentation prompt construction (no LLM calls)."""
from app.services.pipeline import _build_segment_prompt, _ARC_ROLES


def _segs(n=3):
    return [{"start": float(i), "end": float(i) + 1, "text": f"tok-{i}"} for i in range(n)]


def _ctx(section_index, title="Beat Title", description="what it teaches", arc=None):
    return {
        "section_index": section_index,
        "title": title,
        "description": description,
        "arc_titles": arc if arc is not None else ["Hook T", "Define T", "Mechanics T", "Outcomes T"],
    }


class TestStandalonePrompt:
    def test_no_context_uses_standalone_wording(self):
        p = _build_segment_prompt(_segs(), "Photosynthesis", None)
        assert "single most hook-worthy moments" in p
        assert "CONNECTED sequence" not in p
        assert "THIS beat" not in p

    def test_includes_transcript_and_json_shape(self):
        p = _build_segment_prompt(_segs(), "Photosynthesis", None)
        assert "tok-0" in p
        assert '"hook_score"' in p


class TestNarrativePrompt:
    def test_hook_beat_uses_cold_open_language(self):
        p = _build_segment_prompt(_segs(), "Photosynthesis", _ctx(0))
        assert "CONNECTED sequence" in p
        assert "cold-open the entire lesson" in p
        assert "HOOK" in p
        assert "BRIDGE from the previous beat" not in p

    def test_later_beat_uses_bridge_language(self):
        p = _build_segment_prompt(_segs(), "Photosynthesis", _ctx(2))
        assert "BRIDGE from the previous beat" in p
        assert "MECHANICS" in p
        assert "cold-open the entire lesson" not in p

    def test_beat_title_and_description_present(self):
        p = _build_segment_prompt(_segs(), "T", _ctx(1, title="My Cool Title", description="teach the definition"))
        assert "My Cool Title" in p
        assert "teach the definition" in p

    def test_arc_block_marks_current_beat(self):
        p = _build_segment_prompt(_segs(), "T", _ctx(2))
        # the marker should sit on beat index 2 only
        lines = [ln for ln in p.splitlines() if "<-- THIS BEAT" in ln]
        assert len(lines) == 1
        assert "Mechanics T" in lines[0]

    def test_all_arc_titles_listed(self):
        p = _build_segment_prompt(_segs(), "T", _ctx(0))
        for t in ["Hook T", "Define T", "Mechanics T", "Outcomes T"]:
            assert t in p

    def test_unknown_index_falls_back_gracefully(self):
        p = _build_segment_prompt(_segs(), "T", _ctx(9, arc=[]))
        assert "one beat of the lesson" in p
        # no arc block when arc_titles empty
        assert "<-- THIS BEAT" not in p

    def test_serves_this_beat_guardrail_present(self):
        p = _build_segment_prompt(_segs(), "T", _ctx(1))
        assert "do NOT drift into other beats" in p
        assert "OPEN LOOP" in p


class TestTranscriptTruncation:
    def test_caps_at_300_entries(self):
        p = _build_segment_prompt(_segs(305), "T", None)
        assert "tok-299" in p      # 300th entry (0-indexed) included
        assert "tok-300" not in p  # 301st entry dropped


def test_arc_roles_cover_four_beats():
    assert set(_ARC_ROLES.keys()) == {0, 1, 2, 3}
