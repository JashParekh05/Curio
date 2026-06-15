from app.services import story as st
from app.services.story import _valid_permutation, _clip_text, order_for_story


def _clips(n):
    return [
        {"section_index": i % 4, "title": f"T{i}", "description": f"D{i}", "transcript": f"x{i}"}
        for i in range(n)
    ]


def _verdict(order, score=0.9):
    return {
        "overall_score": score,
        "dimensions": {"cold_open": score},
        "order": order,
        "clips": [{"index": i, "issue": ""} for i in range(len(order) if order else 0)],
    }


class TestValidPermutation:
    def test_accepts_clean_permutation(self):
        assert _valid_permutation([2, 0, 1], 3) == [2, 0, 1]

    def test_accepts_string_ints(self):
        assert _valid_permutation(["1", "0"], 2) == [1, 0]

    def test_rejects_wrong_length(self):
        assert _valid_permutation([0, 1], 3) is None

    def test_rejects_duplicates(self):
        assert _valid_permutation([0, 0, 1], 3) is None

    def test_rejects_out_of_range(self):
        assert _valid_permutation([0, 1, 3], 3) is None
        assert _valid_permutation([-1, 0, 1], 3) is None

    def test_rejects_non_list_and_garbage(self):
        assert _valid_permutation("012", 3) is None
        assert _valid_permutation([0, "x", 2], 3) is None
        assert _valid_permutation(None, 3) is None


class TestClipText:
    def test_truncates_to_budget(self):
        long = "a" * 1000
        out = _clip_text({"transcript": long})
        assert len(out) <= st._TRANSCRIPT_BUDGET + 1  # +1 for the ellipsis
        assert out.endswith("…")

    def test_handles_missing_transcript(self):
        assert _clip_text({}) == ""

    def test_flattens_newlines(self):
        assert "\n" not in _clip_text({"transcript": "a\nb\nc"})


class TestOrderForStory:
    def test_reorders_within_a_beat(self, monkeypatch):
        # Two clips in the same section; the judge's intra-beat order is honored.
        clips = [
            {"section_index": 1, "title": "A", "description": "", "transcript": ""},
            {"section_index": 1, "title": "B", "description": "", "transcript": ""},
        ]
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([1, 0]))
        out, verdict = order_for_story("Topic", clips)
        assert [c["title"] for c in out] == ["B", "A"]
        assert verdict["overall_score"] == 0.9

    def test_never_reorders_across_the_arc(self, monkeypatch):
        # Judge tries to pull a §3 clip to the front; the arc clamp forbids it.
        clips = [
            {"section_index": 0, "title": "hook", "description": "", "transcript": ""},
            {"section_index": 1, "title": "what", "description": "", "transcript": ""},
            {"section_index": 3, "title": "outcome", "description": "", "transcript": ""},
        ]
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([2, 0, 1]))
        out, _ = order_for_story("Topic", clips)
        assert [c["section_index"] for c in out] == [0, 1, 3]

    def test_arc_clamp_preserves_judge_order_within_section(self, monkeypatch):
        # Mixed sections: arc order across, judge order within section 1.
        clips = [
            {"section_index": 1, "title": "1a", "description": "", "transcript": ""},
            {"section_index": 0, "title": "0a", "description": "", "transcript": ""},
            {"section_index": 1, "title": "1b", "description": "", "transcript": ""},
        ]
        # Judge wants 1b before 1a; 0a stays the hook.
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([2, 0, 1]))
        out, _ = order_for_story("Topic", clips)
        assert [c["title"] for c in out] == ["0a", "1b", "1a"]

    def test_under_two_clips_is_noop(self, monkeypatch):
        called = {"n": 0}
        monkeypatch.setattr(st, "_judge_story", lambda t, c: called.__setitem__("n", 1) or _verdict([0]))
        out, verdict = order_for_story("Topic", _clips(1))
        assert len(out) == 1 and verdict is None
        assert called["n"] == 0  # judge never called

    def test_judge_failure_keeps_original(self, monkeypatch):
        clips = _clips(4)
        def boom(t, c):
            raise RuntimeError("LLM down")
        monkeypatch.setattr(st, "_judge_story", boom)
        out, verdict = order_for_story("Topic", clips)
        assert [c["title"] for c in out] == ["T0", "T1", "T2", "T3"]
        assert verdict is None

    def test_invalid_order_falls_back_to_arc(self, monkeypatch):
        # Bad permutation -> keep arc order (still clamped), still return verdict.
        clips = [
            {"section_index": 2, "title": "two", "description": "", "transcript": ""},
            {"section_index": 0, "title": "zero", "description": "", "transcript": ""},
            {"section_index": 1, "title": "one", "description": "", "transcript": ""},
        ]
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([0, 0, 1]))  # dup
        out, verdict = order_for_story("Topic", clips)
        assert [c["section_index"] for c in out] == [0, 1, 2]
        assert verdict is not None

    def test_no_clips_lost_on_reorder(self, monkeypatch):
        clips = _clips(6)
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([5, 4, 3, 2, 1, 0]))
        out, _ = order_for_story("Topic", clips)
        assert {c["title"] for c in out} == {c["title"] for c in clips}
        assert len(out) == 6

    def test_none_sections_sink_to_end(self, monkeypatch):
        clips = [
            {"section_index": None, "title": "loose", "description": "", "transcript": ""},
            {"section_index": 0, "title": "hook", "description": "", "transcript": ""},
        ]
        monkeypatch.setattr(st, "_judge_story", lambda t, c: _verdict([0, 1]))
        out, _ = order_for_story("Topic", clips)
        assert [c["title"] for c in out] == ["hook", "loose"]
