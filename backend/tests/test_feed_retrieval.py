"""Fake-DB tests for _fetch_clips_for_slug and _fetch_discover_clips:
section sampling, seen filtering, fallback, arc/personalized ordering."""
from app.services.feed_retrieval import _fetch_clips_for_slug, _fetch_discover_clips


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
    def test_orders_by_score_when_no_canonical_arc(self):
        # Req 2.3: with no Canonical_Arc, the legacy section_index ordering path
        # is gone — clips are role-less and order by final_score (driven by
        # hook here) descending, so the highest-hook clip (section 2) leads.
        clips = [_clip("c2", 2, hook=0.99), _clip("c0", 0, hook=0.1), _clip("c1", 1, hook=0.5)]
        out = _fetch_clips_for_slug(_db(clips), "t")
        assert [c.section_index for c in out] == [2, 1, 0]

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

    def test_narrative_rank_no_longer_orders_within_beat(self):
        # Req 2.3: with no Canonical_Arc, narrative_rank is ignored and clips
        # order by final_score (hook) descending across the whole role-less set.
        clips = [
            _clip("hi", 1, hook=0.9, narrative_rank=1),
            _clip("lo", 1, hook=0.1, narrative_rank=0),
            _clip("c0", 0, hook=0.5),
        ]
        out = _fetch_clips_for_slug(_db(clips), "t")
        ids = [c.id for c in out]
        # score order: hi (0.9) > c0 (0.5) > lo (0.1); narrative_rank ignored.
        assert ids == ["hi", "c0", "lo"]

    def test_empty_topic_returns_empty(self):
        out = _fetch_clips_for_slug(_db([]), "t")
        assert out == []

    def test_all_clips_seen_returns_empty(self):
        clips = [_clip("c0", 0), _clip("c1", 1)]
        out = _fetch_clips_for_slug(_db(clips), "t", seen_ids={"c0", "c1"})
        assert out == []


class TestFetchDiscoverClips:
    """Discover composes ~70% broad ENGAGEMENT-ranked clips + ~30% PERSONALIZED
    (taste/interest) clips. Personalization gets a guaranteed reserved slice
    (it's never starved by high-hook content), while engagement drives the bulk
    of the feed."""

    def _dclip(self, cid, slug, hook=0.5, embedding=None):
        row = {
            "id": cid, "topic_slug": slug, "title": cid, "video_url": "u",
            "hook_score": hook, "source_url": f"src-{cid}",
        }
        if embedding is not None:
            row["embedding"] = embedding
        return row

    def test_70_30_split_engagement_bulk_personalization_reserved(self):
        # 7 high-hook off-taste clips + 3 low-hook perfect-taste clips. Spread
        # the 7 across two slugs because per-slug collection caps at 6, so all 10
        # enter the bounded candidate pool (deterministic split).
        taste = [1.0, 0.0]
        clips = [self._dclip(f"hookA{i}", "ha", hook=0.9, embedding=[0.0, 1.0]) for i in range(4)]
        clips += [self._dclip(f"hookB{i}", "hb", hook=0.9, embedding=[0.0, 1.0]) for i in range(3)]
        clips += [self._dclip(f"taste{i}", "ts", hook=0.05, embedding=[1.0, 0.0]) for i in range(3)]
        out = _fetch_discover_clips(
            _db(clips), relevant_slugs=["ha", "hb", "ts"], all_slugs=["ha", "hb", "ts"],
            seen_ids=set(), limit=10, taste_vector=taste,
        )
        ids = [c.id for c in out]
        assert len(ids) == 10
        # Personalization reserved its 30%: all 3 low-hook taste matches survive
        # despite losing on engagement (round(10*0.30) = 3).
        assert sum(1 for x in ids if x.startswith("taste")) == 3
        # Engagement is the primary 70% (the 7 high-hook clips).
        assert sum(1 for x in ids if x.startswith("hook")) == 7

    def test_taste_still_selects_which_clips_are_personalized(self):
        # The reserved personalized slot goes to the TASTE match, not a random
        # clip: with limit small enough for exactly one personalized pick, the
        # on-taste clip is guaranteed in the feed even though its hook is weakest.
        clips = [
            self._dclip("hooky", "a", hook=0.95, embedding=[0.0, 1.0]),
            self._dclip("on_taste", "b", hook=0.05, embedding=[1.0, 0.0]),
        ]
        out = _fetch_discover_clips(
            _db(clips), relevant_slugs=["a", "b"], all_slugs=["a", "b"],
            seen_ids=set(), limit=10, taste_vector=[1.0, 0.0],
        )
        assert "on_taste" in {c.id for c in out}

    def test_seen_clips_excluded(self):
        clips = [self._dclip("c0", "a"), self._dclip("c1", "a")]
        out = _fetch_discover_clips(
            _db(clips), relevant_slugs=["a"], all_slugs=["a"],
            seen_ids={"c0"}, limit=10,
        )
        assert {c.id for c in out} == {"c1"}

    def test_respects_limit(self):
        clips = [self._dclip(f"c{i}", "a") for i in range(20)]
        out = _fetch_discover_clips(
            _db(clips), relevant_slugs=["a"], all_slugs=["a"],
            seen_ids=set(), limit=5,
        )
        assert len(out) <= 5

    def test_empty_library_returns_empty(self):
        out = _fetch_discover_clips(
            _db([]), relevant_slugs=["a"], all_slugs=["a"],
            seen_ids=set(), limit=10,
        )
        assert out == []


class TestRatioInterleave:
    """The 70/30 merge: even spread of the smaller (personalized) list across
    the larger (engaging) one, preserving counts and per-list order."""

    def _seq(self, prefix, n):
        from app.models.schemas import Clip
        return [Clip(id=f"{prefix}{i}", topic_slug="t", title=f"{prefix}{i}", video_url="u") for i in range(n)]

    def test_preserves_counts_and_order(self):
        from app.services.feed_retrieval import _ratio_interleave
        primary, secondary = self._seq("e", 7), self._seq("p", 3)
        out = _ratio_interleave(primary, secondary)
        ids = [c.id for c in out]
        assert len(ids) == 10
        assert sum(1 for x in ids if x.startswith("e")) == 7
        assert sum(1 for x in ids if x.startswith("p")) == 3
        # Per-list relative order preserved.
        assert [x for x in ids if x.startswith("e")] == [f"e{i}" for i in range(7)]
        assert [x for x in ids if x.startswith("p")] == [f"p{i}" for i in range(3)]
        # Engagement-first: leads with a primary item.
        assert ids[0].startswith("e")
        # Personalization is spread, not blocked at the end.
        p_positions = [i for i, x in enumerate(ids) if x.startswith("p")]
        assert max(p_positions) - min(p_positions) >= 4

    def test_empty_inputs(self):
        from app.services.feed_retrieval import _ratio_interleave
        assert _ratio_interleave([], []) == []
        only_secondary = self._seq("p", 2)
        assert [c.id for c in _ratio_interleave([], only_secondary)] == ["p0", "p1"]
