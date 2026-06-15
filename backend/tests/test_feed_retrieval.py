"""Fake-DB tests for _fetch_clips_for_slug: section sampling, seen filtering,
fallback, and that delivery comes out arc-ordered."""
from app.services.feed_retrieval import _fetch_clips_for_slug


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table, store):
        self.table, self.store = table, store
        self.cols = "*"
        self.filters = {}
        self.inq = None
        self.limitn = None

    def select(self, cols="*"):
        self.cols = cols; return self

    def eq(self, col, val):
        self.filters[col] = val; return self

    def in_(self, col, vals):
        self.inq = (col, list(vals)); return self

    def order(self, *a, **k):
        return self

    def limit(self, n):
        self.limitn = n; return self

    def execute(self):
        if self.table == "clips":
            rows = [r for r in self.store["clips"] if r.get("topic_slug") == self.filters.get("topic_slug")]
            if "section_index" in self.filters:
                rows = [r for r in rows if r.get("section_index") == self.filters["section_index"]]
            if self.cols == "section_index":
                return _Result([{"section_index": r.get("section_index")} for r in rows])
            rows = sorted(rows, key=lambda r: r.get("hook_score", 0.5), reverse=True)
            if self.limitn is not None:
                rows = rows[:self.limitn]
            return _Result([dict(r) for r in rows])
        if self.table == "clip_events":
            col, vals = self.inq
            return _Result([e for e in self.store["clip_events"] if e.get(col) in vals])
        return _Result([])


class _DB:
    def __init__(self, store):
        self.store = store

    def table(self, name):
        return _Query(name, self.store)


def _clip(cid, section, hook=0.5, **extra):
    row = {
        "id": cid, "topic_slug": "t", "title": cid, "video_url": "u",
        "hook_score": hook, "section_index": section, "duration_seconds": 60,
        "source_url": f"src-{cid}",
    }
    row.update(extra)
    return row


def _db(clips, events=None):
    return _DB({"clips": clips, "clip_events": events or []})


class TestFetchClipsForSlug:
    def test_returns_arc_ordered_across_sections(self):
        # Section 2 clip has the highest hook; arc must still lead with section 0.
        clips = [_clip("c2", 2, hook=0.99), _clip("c0", 0, hook=0.1), _clip("c1", 1, hook=0.5)]
        out = _fetch_clips_for_slug(_db(clips), "t")
        assert [c.section_index for c in out] == [0, 1, 2]

    def test_seen_clips_are_filtered_out(self):
        clips = [_clip("c0", 0), _clip("c1", 1), _clip("c2", 2)]
        out = _fetch_clips_for_slug(_db(clips), "t", seen_ids={"c1"})
        ids = {c.id for c in out}
        assert "c1" not in ids and {"c0", "c2"} <= ids

    def test_fallback_when_no_section_index(self):
        # All clips lack section_index -> section sampling yields nothing, the
        # fallback query returns them anyway.
        clips = [_clip("c0", None, hook=0.2), _clip("c1", None, hook=0.9)]
        out = _fetch_clips_for_slug(_db(clips), "t")
        assert {c.id for c in out} == {"c0", "c1"}

    def test_narrative_rank_orders_within_beat(self):
        # Two clips in section 1; narrative_rank should drive their order.
        clips = [
            _clip("hi", 1, hook=0.9, narrative_rank=1),
            _clip("lo", 1, hook=0.1, narrative_rank=0),
            _clip("c0", 0, hook=0.5),
        ]
        out = _fetch_clips_for_slug(_db(clips), "t")
        ids = [c.id for c in out]
        assert ids[0] == "c0"
        assert ids.index("lo") < ids.index("hi")  # narrative_rank 0 before 1

    def test_empty_topic_returns_empty(self):
        out = _fetch_clips_for_slug(_db([]), "t")
        assert out == []

    def test_all_clips_seen_returns_empty(self):
        clips = [_clip("c0", 0), _clip("c1", 1)]
        out = _fetch_clips_for_slug(_db(clips), "t", seen_ids={"c0", "c1"})
        assert out == []
