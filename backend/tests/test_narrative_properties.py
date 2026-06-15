"""Property-based + adversarial tests for the narrative-ordering and retrieval
changes. The bar here is intentionally high: invariants must hold across
thousands of randomized inputs, not just hand-picked happy paths.

Covers:
  - _popularity_bonus      (bounds, monotonicity, degenerate inputs)
  - _rank_candidates       (permutation, relevance dominance, oracle equality)
  - _order_by_arc          (arc monotonicity, beat-argmax, no clip loss)
"""
import math
import re

import pytest
from hypothesis import given, settings, strategies as st, assume, HealthCheck

import app.services.embeddings as embeddings
from app.agents import pipeline_agent
from app.agents.pipeline_agent import _popularity_bonus, _rank_candidates
from app.services.feed_retrieval import _order_by_arc
from tests.conftest import make_clip


# ---------------------------------------------------------------------------
# _popularity_bonus — must be a bounded, monotone, total function
# ---------------------------------------------------------------------------

class TestPopularityBonusProperties:
    @given(views=st.integers(min_value=-10**12, max_value=10**12))
    def test_always_within_bounds(self, views):
        b = _popularity_bonus(views)
        assert 0.0 <= b <= 0.1

    @given(
        a=st.integers(min_value=0, max_value=10**11),
        b=st.integers(min_value=0, max_value=10**11),
    )
    def test_monotone_non_decreasing(self, a, b):
        lo, hi = min(a, b), max(a, b)
        assert _popularity_bonus(lo) <= _popularity_bonus(hi) + 1e-12

    @given(views=st.integers(max_value=0))
    def test_non_positive_views_give_zero(self, views):
        assert _popularity_bonus(views) == 0.0

    def test_degenerate_inputs(self):
        assert _popularity_bonus(0) == 0.0
        assert _popularity_bonus(None) == 0.0  # type: ignore[arg-type]
        # viral cannot exceed the cap
        assert _popularity_bonus(10**9) <= 0.1
        assert _popularity_bonus(10**15) == 0.1

    def test_cap_never_exceeds_combined_with_caption(self):
        # caption (0.05) + popularity (<=0.10) = 0.15, the documented max swing.
        assert 0.05 + _popularity_bonus(10**15) <= 0.15 + 1e-12


# ---------------------------------------------------------------------------
# _rank_candidates — relevance is primary; tiebreakers are bounded
# ---------------------------------------------------------------------------

_VID_RE = re.compile(r"vid=(\d+)")
_SIM_MAP: dict[str, float] = {}


def _patch_sim_embeddings(monkeypatch):
    """Look up each candidate's exact similarity by video id (full precision, no
    rounding) so the function and the test oracle see identical sims.
    cosine_similarity([1.0], [s]) == s."""
    monkeypatch.setattr(embeddings, "embed_text", lambda text: [1.0])

    def fake_embed_texts(texts):
        out = []
        for t in texts:
            m = _VID_RE.search(t or "")
            out.append([_SIM_MAP[m.group(1)]] if m else [0.0])
        return out

    monkeypatch.setattr(embeddings, "embed_texts", fake_embed_texts)


def _cand(i, sim, caption=False, views=0):
    _SIM_MAP[str(i)] = sim
    return {
        "video_id": str(i),
        "title": f"vid={i}",
        "description": None,
        "has_caption": caption,
        "view_count": views,
    }


def _effective(v, sim):
    return sim + (0.05 if v.get("has_caption") else 0.0) + _popularity_bonus(v.get("view_count", 0))


class TestRankCandidatesProperties:
    @settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        sims=st.lists(st.floats(min_value=-1, max_value=1, allow_nan=False, allow_infinity=False),
                      min_size=2, max_size=8),
        data=st.data(),
    )
    def test_is_a_permutation(self, monkeypatch, sims, data):
        _patch_sim_embeddings(monkeypatch)
        vids = []
        for i, s in enumerate(sims):
            vids.append(_cand(
                i, s,
                caption=data.draw(st.booleans()),
                views=data.draw(st.integers(min_value=0, max_value=10**9)),
            ))
        out = _rank_candidates(list(vids), "q")
        assert sorted(v["video_id"] for v in out) == sorted(v["video_id"] for v in vids)
        assert len(out) == len(vids)

    @settings(max_examples=300, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        sims=st.lists(st.floats(min_value=-1, max_value=1, allow_nan=False, allow_infinity=False),
                      min_size=2, max_size=8, unique=True),
        data=st.data(),
    )
    def test_matches_scoring_oracle(self, monkeypatch, sims, data):
        _patch_sim_embeddings(monkeypatch)
        vids = []
        for i, s in enumerate(sims):
            vids.append(_cand(
                i, s,
                caption=data.draw(st.booleans()),
                views=data.draw(st.integers(min_value=0, max_value=10**9)),
            ))
        sim_by_id = {v["video_id"]: s for v, s in zip(vids, sims)}
        out = _rank_candidates(list(vids), "q")
        # Independent oracle: stable sort by effective score desc.
        expected = sorted(vids, key=lambda v: _effective(v, sim_by_id[v["video_id"]]), reverse=True)
        assert [v["video_id"] for v in out] == [v["video_id"] for v in expected]

    @settings(max_examples=400, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        sim_winner=st.floats(min_value=-1, max_value=1, allow_nan=False),
        sim_loser=st.floats(min_value=-1, max_value=1, allow_nan=False),
        winner_caption=st.booleans(),
        winner_views=st.integers(min_value=0, max_value=10**9),
        loser_views=st.integers(min_value=0, max_value=10**9),
    )
    def test_relevance_dominates_when_gap_exceeds_max_bonus(
        self, monkeypatch, sim_winner, sim_loser, winner_caption, winner_views, loser_views
    ):
        # The max combined tiebreaker swing is 0.05 (caption) + 0.10 (views) = 0.15.
        # If the more-relevant video leads by MORE than that, it MUST rank first,
        # even when it is the LEAST captioned/popular and the loser is maximally so.
        assume(sim_winner - sim_loser > 0.15 + 1e-6)
        _patch_sim_embeddings(monkeypatch)
        winner = _cand(1, sim_winner, caption=winner_caption, views=winner_views)
        # Stack every tiebreaker in the loser's favor.
        loser = _cand(2, sim_loser, caption=True, views=max(loser_views, 10**9))
        out = _rank_candidates([loser, winner], "q")
        assert out[0]["video_id"] == "1"

    @settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(n=st.integers(min_value=2, max_value=10))
    def test_caption_then_views_fallback_when_no_embeddings(self, monkeypatch, n):
        # Embeddings unavailable -> deterministic, total ordering: captioned first,
        # then higher views. Still a permutation, never a crash.
        monkeypatch.setattr(embeddings, "embed_text", lambda text: None)
        vids = [_cand(i, 0.0, caption=(i % 2 == 0), views=i * 1000) for i in range(n)]
        out = _rank_candidates(list(vids), "q")
        assert sorted(v["video_id"] for v in out) == sorted(v["video_id"] for v in vids)
        keys = [(0 if v["has_caption"] else 1, -_popularity_bonus(v["view_count"])) for v in out]
        assert keys == sorted(keys)

    def test_noop_inputs_are_identity(self):
        single = [_cand(1, 0.9)]
        assert _rank_candidates(single, "q") is single
        empty_query = [_cand(1, 0.9), _cand(2, 0.1)]
        assert _rank_candidates(empty_query, "") is empty_query
        textless = [{"video_id": "1"}, {"video_id": "2"}]
        assert _rank_candidates(textless, "q") is textless

    def test_adversarial_viral_offtopic_loses_to_exact_obscure(self, monkeypatch):
        _patch_sim_embeddings(monkeypatch)
        exact = _cand(1, 0.95, caption=False, views=12)            # obscure, perfect match
        viral = _cand(2, 0.30, caption=True, views=200_000_000)    # viral, off-topic
        out = _rank_candidates([viral, exact], "q")
        assert [v["video_id"] for v in out] == ["1", "2"]

    @settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(n=st.integers(min_value=2, max_value=8))
    def test_total_ties_preserve_input_order(self, monkeypatch, n):
        # Identical sim, caption, and views across all candidates => the sort is
        # a no-op shuffle: input order must be preserved (stable, deterministic).
        _patch_sim_embeddings(monkeypatch)
        vids = [_cand(i, 0.5, caption=True, views=1000) for i in range(n)]
        out = _rank_candidates(list(vids), "q")
        assert [v["video_id"] for v in out] == [v["video_id"] for v in vids]


# ---------------------------------------------------------------------------
# _order_by_arc — the narrative spine. The arc must NEVER be violated.
# ---------------------------------------------------------------------------

_SECTIONS = st.sampled_from([0, 1, 2, 3, None])
_SOURCES = st.sampled_from(["a", "b", "c", "d"])


def _arc_clips(specs):
    """specs: list of (section_index, score, source). Returns Clip objects with
    final_score set, unique ids."""
    clips = []
    for i, (sec, score, src) in enumerate(specs):
        c = make_clip(section_index=sec, hook_score=0.5, source_url=src)
        c.final_score = score
        clips.append(c)
    return clips


def _beat_key(c):
    return c.section_index if c.section_index is not None else 1_000_000


def _eff(c):
    return c.final_score or c.hook_score


class TestOrderByArcProperties:
    @settings(max_examples=400)
    @given(specs=st.lists(
        st.tuples(_SECTIONS,
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        max_size=24,
    ))
    def test_output_is_a_permutation(self, specs):
        clips = _arc_clips(specs)
        out = _order_by_arc(list(clips))
        assert sorted(c.id for c in out) == sorted(c.id for c in clips)
        assert len(out) == len(clips)

    @settings(max_examples=400)
    @given(specs=st.lists(
        st.tuples(_SECTIONS,
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        min_size=1, max_size=24,
    ))
    def test_arc_is_non_decreasing_regardless_of_score(self, specs):
        # THE core invariant: no matter how scores fall, sections never go
        # backwards. A high-scoring outcomes clip can never jump ahead of a
        # low-scoring hook clip.
        clips = _arc_clips(specs)
        out = _order_by_arc(list(clips))
        beats = [_beat_key(c) for c in out]
        assert beats == sorted(beats)

    @settings(max_examples=400)
    @given(specs=st.lists(
        st.tuples(_SECTIONS,
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        min_size=1, max_size=24,
    ))
    def test_each_beat_leads_with_its_highest_score(self, specs):
        # Within every beat, the first clip delivered is the highest-scoring one
        # (the strongest hook leads the beat), even after source-spreading.
        clips = _arc_clips(specs)
        out = _order_by_arc(list(clips))
        from itertools import groupby
        for _, group in groupby(out, key=_beat_key):
            g = list(group)
            assert _eff(g[0]) == max(_eff(c) for c in g)

    @settings(max_examples=200)
    @given(specs=st.lists(
        st.tuples(_SECTIONS,
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        max_size=24,
    ))
    def test_deterministic(self, specs):
        clips = _arc_clips(specs)
        a = [c.id for c in _order_by_arc(list(clips))]
        b = [c.id for c in _order_by_arc(list(clips))]
        assert a == b

    @settings(max_examples=300)
    @given(specs=st.lists(
        st.tuples(st.integers(min_value=0, max_value=3),
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        min_size=2, max_size=24,
    ))
    def test_within_beat_distinct_sources_is_strict_score_order(self, specs):
        # When every clip in a beat has a distinct source, spreading is a no-op,
        # so the beat must be in strict descending score order.
        clips = _arc_clips(specs)
        out = _order_by_arc(list(clips))
        from itertools import groupby
        for _, group in groupby(out, key=_beat_key):
            g = list(group)
            if len({c.source_url for c in g}) == len(g):  # all distinct sources
                scores = [_eff(c) for c in g]
                assert scores == sorted(scores, reverse=True)

    # --- adversarial fixed cases ------------------------------------------

    def test_top_scoring_outcome_never_precedes_worst_hook(self):
        clips = _arc_clips([(3, 1.0, "a"), (0, 0.0001, "b")])
        out = _order_by_arc(clips)
        assert [c.section_index for c in out] == [0, 3]

    def test_none_section_clips_sink_to_the_end(self):
        clips = _arc_clips([(None, 0.99, "a"), (2, 0.01, "b"), (0, 0.5, "c")])
        out = _order_by_arc(clips)
        assert [c.section_index for c in out] == [0, 2, None]

    def test_all_same_section_is_pure_score_then_spread(self):
        clips = _arc_clips([(1, 0.2, "a"), (1, 0.9, "b"), (1, 0.5, "c")])
        out = _order_by_arc(clips)
        assert [c.final_score for c in out] == [0.9, 0.5, 0.2]

    def test_empty_input(self):
        assert _order_by_arc([]) == []

    def test_zero_final_score_falls_back_to_hook(self):
        # final_score == 0.0 is falsy; the impl uses `final_score or hook_score`.
        # Lock in that behavior so a 0.0 score doesn't silently outrank a hook.
        a = make_clip(section_index=0, hook_score=0.8, source_url="a"); a.final_score = 0.0
        b = make_clip(section_index=0, hook_score=0.1, source_url="b"); b.final_score = 0.0
        out = _order_by_arc([b, a])
        assert [c.id for c in out] == [a.id, b.id]  # ranked by hook fallback

    @settings(max_examples=300)
    @given(specs=st.lists(
        st.tuples(_SECTIONS,
                  st.floats(min_value=0, max_value=1, allow_nan=False),
                  _SOURCES),
        max_size=24,
    ))
    def test_idempotent(self, specs):
        # Ordering an already-ordered feed must not change it. Guards against any
        # hidden dependence on input order beyond the documented score/spread.
        clips = _arc_clips(specs)
        once = _order_by_arc(list(clips))
        twice = _order_by_arc(list(once))
        assert [c.id for c in once] == [c.id for c in twice]
