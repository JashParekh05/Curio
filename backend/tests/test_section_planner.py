from app.agents import section_planner as sp
from app.agents.section_planner import (
    _normalize_sections,
    _sections_needing_revision,
    _merge_revisions,
    _default_sections,
)


def _canned_detailed():
    return [
        {"section_index": 0, "title": "T0", "description": "D0", "search_query": "Q0"},
        {"section_index": 1, "title": "T1", "description": "D1", "search_query": "Q1"},
        {"section_index": 2, "title": "T2", "description": "D2", "search_query": "Q2"},
        {"section_index": 3, "title": "T3", "description": "D3", "search_query": "Q3"},
    ]


def _all_ok():
    return {"overall_score": 0.9, "sections": [{"section_index": i, "ok": True, "issue": ""} for i in range(4)]}


def _weak(indices):
    return {
        "overall_score": 0.4,
        "sections": [
            {"section_index": i, "ok": i not in indices, "issue": "bad" if i in indices else ""}
            for i in range(4)
        ],
    }


class TestNormalize:
    def test_passthrough_four(self):
        out = _normalize_sections(_canned_detailed(), "Topic")
        assert [s["section_index"] for s in out] == [0, 1, 2, 3]
        assert out[2]["search_query"] == "Q2"

    def test_fills_missing_fields_and_count(self):
        out = _normalize_sections([{"section_index": 0, "title": "X"}], "Binary Search")
        assert len(out) == 4
        assert out[0]["title"] == "X"
        assert out[0]["search_query"]  # blank field filled from defaults
        assert out[3]["title"] == "Binary Search in Practice"  # whole section defaulted

    def test_reorders_and_dedupes(self):
        raw = [
            {"section_index": 3, "title": "three", "description": "d", "search_query": "q"},
            {"section_index": 0, "title": "zero", "description": "d", "search_query": "q"},
            {"section_index": 0, "title": "dup", "description": "d", "search_query": "q"},
        ]
        out = _normalize_sections(raw, "T")
        assert [s["section_index"] for s in out] == [0, 1, 2, 3]
        assert out[0]["title"] == "zero"  # first wins for a duplicate index
        assert out[3]["title"] == "three"

    def test_ignores_non_dict_entries(self):
        out = _normalize_sections(["junk", None, 5], "T")
        assert len(out) == 4  # all fall back to defaults


class TestNeedsRevision:
    def test_flags_not_ok(self):
        assert _sections_needing_revision(_weak([1, 3])) == [1, 3]

    def test_all_ok(self):
        assert _sections_needing_revision(_all_ok()) == []

    def test_missing_or_empty_judge(self):
        assert _sections_needing_revision(None) == []
        assert _sections_needing_revision({}) == []

    def test_missing_ok_field_treated_as_ok(self):
        assert _sections_needing_revision({"sections": [{"section_index": 0}]}) == []


class TestMergeRevisions:
    def test_replaces_only_revised(self):
        base = _normalize_sections(_canned_detailed(), "T")
        revised = [{"section_index": 2, "title": "NEW", "description": "ND", "search_query": "NQ"}]
        out = _merge_revisions(base, revised)
        assert len(out) == 4
        assert (out[2]["title"], out[2]["search_query"]) == ("NEW", "NQ")
        assert out[0]["title"] == "T0"  # untouched
        assert out[3]["title"] == "T3"


class TestPlanOrchestration:
    def _patch_generation(self, monkeypatch):
        monkeypatch.setattr(sp, "_generate_outline",
                            lambda *a, **k: [{"section_index": i, "title": f"T{i}"} for i in range(4)])
        monkeypatch.setattr(sp, "_detail_sections", lambda *a, **k: _canned_detailed())

    def test_happy_path_no_revision(self, monkeypatch):
        self._patch_generation(monkeypatch)
        calls = {"judge": 0, "revise": 0}
        monkeypatch.setattr(sp, "_judge_sections", lambda *a, **k: (calls.__setitem__("judge", calls["judge"] + 1) or _all_ok()))
        monkeypatch.setattr(sp, "_revise_sections", lambda *a, **k: (calls.__setitem__("revise", calls["revise"] + 1) or []))
        out = sp._plan_sections("Binary Search")
        assert [s["section_index"] for s in out] == [0, 1, 2, 3]
        assert out[2]["title"] == "T2"
        assert calls == {"judge": 1, "revise": 0}

    def test_revises_weak_then_passes(self, monkeypatch):
        self._patch_generation(monkeypatch)
        judge_results = [_weak([2]), _all_ok()]
        calls = {"judge": 0, "revise": 0}

        def judge(*a, **k):
            r = judge_results[calls["judge"]]
            calls["judge"] += 1
            return r

        def revise(topic, diff, sections, weak, j):
            calls["revise"] += 1
            assert weak == [2]
            return [{"section_index": 2, "title": "FIXED", "description": "FD", "search_query": "FQ"}]

        monkeypatch.setattr(sp, "_judge_sections", judge)
        monkeypatch.setattr(sp, "_revise_sections", revise)
        out = sp._plan_sections("Topic")
        assert calls == {"judge": 2, "revise": 1}
        assert (out[2]["title"], out[2]["search_query"]) == ("FIXED", "FQ")
        assert out[0]["title"] == "T0"  # untouched sections preserved

    def test_bounded_revisions(self, monkeypatch):
        self._patch_generation(monkeypatch)
        calls = {"judge": 0, "revise": 0}
        monkeypatch.setattr(sp, "_judge_sections", lambda *a, **k: (calls.__setitem__("judge", calls["judge"] + 1) or _weak([1])))
        monkeypatch.setattr(sp, "_revise_sections", lambda *a, **k: (calls.__setitem__("revise", calls["revise"] + 1) or [{"section_index": 1, "title": "R", "description": "R", "search_query": "R"}]))
        out = sp._plan_sections("Topic")
        assert calls["revise"] == sp.MAX_REVISIONS  # never loops forever
        assert calls["judge"] == sp.MAX_REVISIONS + 1  # initial judge + one re-judge per revision
        assert len(out) == 4

    def test_generation_failure_falls_back_to_defaults(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("LLM down")
        monkeypatch.setattr(sp, "_generate_outline", boom)
        assert sp._plan_sections("Binary Search") == _default_sections("Binary Search")

    def test_judge_failure_keeps_generated_plan(self, monkeypatch):
        self._patch_generation(monkeypatch)
        def boom(*a, **k):
            raise RuntimeError("judge down")
        monkeypatch.setattr(sp, "_judge_sections", boom)
        out = sp._plan_sections("Topic")
        assert out[1]["title"] == "T1"  # generated plan preserved, no crash
        assert len(out) == 4
