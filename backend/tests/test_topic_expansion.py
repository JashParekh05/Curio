import time

from app.agents import section_planner as sp
from app.agents.section_planner import plan_extension_sections
from app.services import topic_expansion as te
from app.services.topic_expansion import (
    _is_expansion_candidate,
    EXPAND_WHEN_UNSEEN_AT_OR_BELOW,
    ENGAGED_COMPLETION,
)


class TestPlanExtensionSections:
    def test_continues_index_and_skips_duplicates(self, monkeypatch):
        monkeypatch.setattr(sp, "_extension_angles", lambda name, existing, count: [
            {"title": "Existing Angle", "description": "d", "search_query": "q"},  # dup -> skipped
            {"title": "Fresh Angle A", "description": "da", "search_query": "qa"},
            {"title": "Fresh Angle B", "description": "db", "search_query": "qb"},
        ])
        out = plan_extension_sections("Topic", existing_titles=["Existing Angle"], start_index=4, count=2)
        assert [s["section_index"] for s in out] == [4, 5]
        assert [s["title"] for s in out] == ["Fresh Angle A", "Fresh Angle B"]

    def test_backfills_from_defaults_when_llm_short(self, monkeypatch):
        monkeypatch.setattr(sp, "_extension_angles", lambda name, existing, count: [])
        out = plan_extension_sections("Photosynthesis", existing_titles=[], start_index=4, count=2)
        assert len(out) == 2
        assert all(s["search_query"] for s in out)
        assert [s["section_index"] for s in out] == [4, 5]

    def test_llm_failure_falls_back(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr(sp, "_extension_angles", boom)
        out = plan_extension_sections("Topic", existing_titles=[], start_index=6, count=2)
        assert len(out) == 2
        assert [s["section_index"] for s in out] == [6, 7]

    def test_no_duplicate_titles_in_output(self, monkeypatch):
        monkeypatch.setattr(sp, "_extension_angles", lambda name, existing, count: [
            {"title": "Same", "description": "", "search_query": "q"},
            {"title": "Same", "description": "", "search_query": "q"},
        ])
        out = plan_extension_sections("Topic", existing_titles=[], start_index=4, count=2)
        titles = [s["title"] for s in out]
        assert len(titles) == len(set(titles))


class TestShouldExpandTopic:
    def setup_method(self):
        te._expanding.clear()

    def test_first_call_allowed_then_throttled(self):
        assert te._should_expand_topic("s1", "slug") is True
        assert te._should_expand_topic("s1", "slug") is False  # within cooldown

    def test_independent_per_session_and_topic(self):
        assert te._should_expand_topic("s1", "a") is True
        assert te._should_expand_topic("s1", "b") is True   # different topic
        assert te._should_expand_topic("s2", "a") is True   # different session

    def test_allowed_again_after_cooldown(self, monkeypatch):
        assert te._should_expand_topic("s1", "slug") is True
        # simulate cooldown elapsing
        te._expanding["s1:slug"] = time.time() - te._EXPAND_COOLDOWN_S - 1
        assert te._should_expand_topic("s1", "slug") is True


class TestIsExpansionCandidate:
    # Baseline that SHOULD trigger: engaged + low but non-zero unseen + idle.
    def _eligible(self, **over):
        args = {"unseen_clip_count": 1, "completion_rate": ENGAGED_COMPLETION, "is_generating": False}
        args.update(over)
        return _is_expansion_candidate(**args)

    def test_eligible_baseline(self):
        assert self._eligible() is True

    def test_generating_blocks_expansion(self):
        # Even when engaged and low, a topic mid-generation must NOT expand.
        assert self._eligible(is_generating=True) is False

    def test_zero_unseen_does_not_expand(self):
        # Empty topic is self-heal's job, not expansion's.
        assert self._eligible(unseen_clip_count=0) is False

    def test_negative_unseen_does_not_expand(self):
        assert self._eligible(unseen_clip_count=-1) is False

    def test_at_unseen_threshold_expands(self):
        assert self._eligible(unseen_clip_count=EXPAND_WHEN_UNSEEN_AT_OR_BELOW) is True

    def test_above_unseen_threshold_does_not_expand(self):
        assert self._eligible(unseen_clip_count=EXPAND_WHEN_UNSEEN_AT_OR_BELOW + 1) is False

    def test_at_engagement_threshold_expands(self):
        assert self._eligible(completion_rate=ENGAGED_COMPLETION) is True

    def test_just_below_engagement_does_not_expand(self):
        assert self._eligible(completion_rate=ENGAGED_COMPLETION - 0.01) is False

    def test_unengaged_low_topic_does_not_expand(self):
        # Low on clips but the viewer isn't watching — don't waste generation.
        assert self._eligible(completion_rate=0.0) is False

    def test_decision_has_no_throttle_side_effect(self):
        # Pure predicate must not touch the throttle map.
        te._expanding.clear()
        _is_expansion_candidate(1, 1.0, False)
        assert te._expanding == {}


# --- _expand_topic orchestration (fake DB) ---------------------------------

class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Minimal chainable query stub for the calls _expand_topic makes."""
    def __init__(self, rows, upserts, fail_read=False):
        self._rows, self._upserts, self._fail_read = rows, upserts, fail_read
        self._op = "select"
        self._payload = None

    def select(self, *a, **k):
        self._op = "select"; return self

    def upsert(self, payload, **k):
        self._op = "upsert"; self._payload = payload; return self

    def eq(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def execute(self):
        if self._op == "upsert":
            self._upserts.append(self._payload)
            return _Result([])
        if self._fail_read:
            raise RuntimeError("db down")
        return _Result(self._rows)


class _DB:
    def __init__(self, rows, upserts, fail_read=False):
        self._rows, self._upserts, self._fail_read = rows, upserts, fail_read

    def table(self, name):
        return _Query(self._rows, self._upserts, self._fail_read)


def _patch_expand(monkeypatch, rows, upserts, new_sections, fail_read=False):
    monkeypatch.setattr(te, "get_client", lambda: _DB(rows, upserts, fail_read))
    calls = {"pipeline": [], "story": 0}
    monkeypatch.setattr("app.agents.section_planner.plan_extension_sections",
                        lambda name, existing, start, count: list(new_sections))
    monkeypatch.setattr("app.agents.pipeline_agent.run_pipeline",
                        lambda *a, **k: calls["pipeline"].append(a) or 1)
    monkeypatch.setattr("app.services.story.run_story_pass",
                        lambda *a, **k: calls.__setitem__("story", calls["story"] + 1))
    return calls


def _section(idx, title):
    return {"section_index": idx, "title": title, "description": "d", "search_query": f"q{idx}"}


class TestExpandTopic:
    def test_happy_path_appends_and_runs_story(self, monkeypatch):
        rows = [{"section_index": i, "title": f"S{i}"} for i in range(4)]  # 0..3
        upserts = []
        new = [_section(4, "Depth A"), _section(5, "Depth B")]
        calls = _patch_expand(monkeypatch, rows, upserts, new)
        te._expand_topic("slug", "Name")
        # both new beats upserted and piped, story pass run once
        assert [u["section_index"] for u in upserts] == [4, 5]
        assert [a[3] for a in calls["pipeline"]] == [4, 5]          # section_index arg
        assert all(a[4] is False for a in calls["pipeline"])         # clear_existing False
        assert calls["story"] == 1

    def test_arc_titles_include_existing_and_new(self, monkeypatch):
        rows = [{"section_index": i, "title": f"S{i}"} for i in range(4)]
        new = [_section(4, "Depth A"), _section(5, "Depth B")]
        calls = _patch_expand(monkeypatch, rows, [], new)
        te._expand_topic("slug", "Name")
        # arc_titles is the 8th positional arg (index 7) to run_pipeline
        arc = calls["pipeline"][0][7]
        assert arc == ["S0", "S1", "S2", "S3", "Depth A", "Depth B"]

    def test_at_max_depth_does_nothing(self, monkeypatch):
        rows = [{"section_index": i, "title": f"S{i}"} for i in range(te.MAX_SECTIONS)]
        calls = _patch_expand(monkeypatch, rows, [], [_section(99, "x")])
        te._expand_topic("slug", "Name")
        assert calls["pipeline"] == []
        assert calls["story"] == 0

    def test_no_new_sections_skips_pipeline_and_story(self, monkeypatch):
        rows = [{"section_index": i, "title": f"S{i}"} for i in range(4)]
        calls = _patch_expand(monkeypatch, rows, [], [])  # planner returns nothing
        te._expand_topic("slug", "Name")
        assert calls["pipeline"] == []
        assert calls["story"] == 0

    def test_read_failure_aborts_cleanly(self, monkeypatch):
        calls = _patch_expand(monkeypatch, [], [], [_section(4, "x")], fail_read=True)
        te._expand_topic("slug", "Name")  # must not raise
        assert calls["pipeline"] == []
        assert calls["story"] == 0

    def test_empty_topic_starts_at_index_4(self, monkeypatch):
        # No existing sections -> depth beats still start at 4, not 0/1.
        new = [_section(4, "Depth A"), _section(5, "Depth B")]
        calls = _patch_expand(monkeypatch, [], [], new)
        te._expand_topic("slug", "Name")
        assert [a[3] for a in calls["pipeline"]] == [4, 5]
