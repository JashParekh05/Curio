"""Edge/example tests for ``deliver_node`` clip and transcript fallbacks.

These drive the real ``deliver_node`` orchestrator with the LLM functions
(``game.intuition``, ``game.clip_query``, ``game.generate_quiz``) and the
youtube leaf functions (``game.youtube.youtube_search``,
``game.youtube._fetch_transcript``) stubbed via ``unittest.mock.patch`` — no
real network or model call is made. ``deliver_node`` is otherwise exercised end
to end (``_safe_hook`` → ``_safe_clip_query`` → ``youtube_search`` →
``select_clip``/``to_embed_url`` → ``_fetch_transcript`` → ``generate_quiz``).

Three cases:

(a) **``youtube_search`` returns ``None`` (no project can afford a search).**
    ``deliver_node`` continues the node flow with ``clip = None`` (no
    ``video_url`` exposed) and a transcript-free quiz: ``generate_quiz`` is
    still called and is passed ``transcript=None``. No error is raised
    (Req 10.4).

(b) **A clip is selected but ``_fetch_transcript`` returns ``None``.** The clip
    is still exposed (with its canonical embed ``video_url``), but the quiz is
    grounded in model knowledge: ``generate_quiz`` is called with
    ``transcript=None`` (Req 11.2).

(c) **``generate_and_store_questions`` is never called in any path.** The
    DB-coupled orchestrator must never run during on-the-fly node delivery — it
    is patched and asserted ``not_called`` across the no-clip, clip-with-
    transcript, and clip-without-transcript paths (Req 11.5).

Validates: Requirements 10.4, 11.2, 11.5
"""
from unittest.mock import patch

from app.services import game


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------

def _quiz_questions() -> list[dict]:
    """A 3-question checkpoint quiz in the documented shape (one correct, one
    concept_tag each). ``generate_quiz`` is stubbed to return this verbatim."""
    return [
        {
            "question": f"Question {i}?",
            "options": [f"Right {i}", f"Wrong {i}", f"Other {i}"],
            "correct_index": 0,
            "explanation": f"Option 0 is correct for {i}.",
            "concept_tag": f"concept-{i}",
        }
        for i in range(3)
    ]


def _clip(video_id: str = "vid12345678") -> dict:
    """A raw youtube clip dict in the short-explainer ideal range so the real
    ``select_clip`` picks it deterministically."""
    return {
        "video_id": video_id,
        "title": "A Short Focused Explainer",
        "channel_title": "Some Channel",
        "duration_seconds": 120,  # ideal 60-180s range
        "has_caption": True,
        "view_count": 10_000,
        "thumbnail_url": "https://i.ytimg.com/vi/vid12345678/hq.jpg",
        "description": "A punchy explainer.",
    }


# ---------------------------------------------------------------------------
# (a) youtube_search returns None → graceful no-clip, transcript-free quiz
# ---------------------------------------------------------------------------

class TestYoutubeSearchNoneGracefulPath:
    def test_no_affordable_search_yields_no_clip_and_transcript_free_quiz(self):
        # youtube_search returns None when no project can afford a search. The
        # node flow continues with clip = None and a transcript-free quiz; no
        # error is raised (Req 10.4).
        questions = _quiz_questions()
        with (
            patch.object(game, "intuition", return_value={"hook": "A punchy hook."}),
            patch.object(game, "clip_query", return_value={"query": "recursion explained"}),
            patch.object(game.youtube, "youtube_search", return_value=None) as search,
            patch.object(game.youtube, "_fetch_transcript") as fetch,
            patch.object(game, "generate_quiz", return_value=questions) as gen_quiz,
        ):
            payload = game.deliver_node("recursion", "backtracking")

        # No clip exposed, so no video_url either (Req 10.4).
        assert payload.clip is None
        assert payload.hook == "A punchy hook."
        assert payload.quiz == questions

        # The search was attempted; the transcript fetch was never reached
        # because there is no clip.
        search.assert_called_once()
        fetch.assert_not_called()

        # The quiz is still generated, transcript-free (transcript=None).
        gen_quiz.assert_called_once()
        node_arg, transcript_arg = _quiz_call_args(gen_quiz)
        assert node_arg == "recursion"
        assert transcript_arg is None

    def test_empty_clip_list_also_yields_no_clip_and_transcript_free_quiz(self):
        # A successful but empty search behaves like None: clip = None, quiz is
        # transcript-free, no error (Req 10.6 mirrors 10.4).
        questions = _quiz_questions()
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=[]),
            patch.object(game.youtube, "_fetch_transcript") as fetch,
            patch.object(game, "generate_quiz", return_value=questions) as gen_quiz,
        ):
            payload = game.deliver_node("recursion", "backtracking")

        assert payload.clip is None
        fetch.assert_not_called()
        _, transcript_arg = _quiz_call_args(gen_quiz)
        assert transcript_arg is None


# ---------------------------------------------------------------------------
# (b) Clip present but transcript None → model-knowledge quiz
# ---------------------------------------------------------------------------

class TestTranscriptNoneModelKnowledgeQuiz:
    def test_clip_selected_but_no_transcript_grounds_quiz_in_model_knowledge(self):
        # A clip is found and selected, but _fetch_transcript returns None.
        # The clip is still exposed (with its embed video_url), and the quiz is
        # generated from model knowledge: generate_quiz(node, transcript=None)
        # (Req 11.2).
        questions = _quiz_questions()
        clip = _clip()
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=[clip]),
            patch.object(game.youtube, "_fetch_transcript", return_value=None) as fetch,
            patch.object(game, "generate_quiz", return_value=questions) as gen_quiz,
        ):
            payload = game.deliver_node("recursion", "backtracking")

        # The clip is exposed with a canonical embed URL (Req 10.3).
        assert payload.clip is not None
        assert payload.clip["video_id"] == clip["video_id"]
        assert payload.clip["video_url"] == game.to_embed_url(clip["video_id"])

        # Transcript fetch was attempted on the selected clip's id and returned
        # None, so the quiz falls back to model knowledge (Req 11.2).
        fetch.assert_called_once_with(clip["video_id"])
        node_arg, transcript_arg = _quiz_call_args(gen_quiz)
        assert node_arg == "recursion"
        assert transcript_arg is None

    def test_clip_with_transcript_grounds_quiz_in_transcript(self):
        # Contrast case: when a transcript IS available it is joined and passed
        # to generate_quiz, confirming the None path above is the fallback and
        # not the default (Req 11.1).
        questions = _quiz_questions()
        clip = _clip()
        segments = [
            {"start": 0.0, "duration": 2.0, "text": "Recursion calls itself"},
            {"start": 2.0, "duration": 2.0, "text": "until a base case stops it"},
        ]
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=[clip]),
            patch.object(game.youtube, "_fetch_transcript", return_value=segments),
            patch.object(game, "generate_quiz", return_value=questions) as gen_quiz,
        ):
            game.deliver_node("recursion", "backtracking")

        _, transcript_arg = _quiz_call_args(gen_quiz)
        assert transcript_arg is not None
        assert "Recursion calls itself" in transcript_arg
        assert "until a base case stops it" in transcript_arg


# ---------------------------------------------------------------------------
# (c) generate_and_store_questions is NEVER called in any path (Req 11.5)
# ---------------------------------------------------------------------------

class TestNeverCallsGenerateAndStoreQuestions:
    def test_no_clip_path_does_not_call_generate_and_store_questions(self):
        questions = _quiz_questions()
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=None),
            patch.object(game.youtube, "_fetch_transcript", return_value=None),
            patch.object(game, "generate_quiz", return_value=questions),
            patch.object(game.quiz, "generate_and_store_questions") as store,
        ):
            game.deliver_node("recursion", "backtracking")

        store.assert_not_called()

    def test_clip_without_transcript_path_does_not_call_generate_and_store_questions(self):
        questions = _quiz_questions()
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=[_clip()]),
            patch.object(game.youtube, "_fetch_transcript", return_value=None),
            patch.object(game, "generate_quiz", return_value=questions),
            patch.object(game.quiz, "generate_and_store_questions") as store,
        ):
            game.deliver_node("recursion", "backtracking")

        store.assert_not_called()

    def test_clip_with_transcript_path_does_not_call_generate_and_store_questions(self):
        questions = _quiz_questions()
        segments = [{"start": 0.0, "duration": 2.0, "text": "Content"}]
        with (
            patch.object(game, "intuition", return_value={"hook": "Hook."}),
            patch.object(game, "clip_query", return_value={"query": "q"}),
            patch.object(game.youtube, "youtube_search", return_value=[_clip()]),
            patch.object(game.youtube, "_fetch_transcript", return_value=segments),
            patch.object(game, "generate_quiz", return_value=questions),
            patch.object(game.quiz, "generate_and_store_questions") as store,
        ):
            game.deliver_node("recursion", "backtracking")

        store.assert_not_called()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quiz_call_args(gen_quiz_mock) -> tuple[str, object]:
    """Return ``(node, transcript)`` from the single ``generate_quiz`` call,
    tolerating either positional or keyword passing of ``transcript``."""
    args, kwargs = gen_quiz_mock.call_args
    node = args[0] if args else kwargs.get("node")
    if len(args) > 1:
        transcript = args[1]
    else:
        transcript = kwargs.get("transcript")
    return node, transcript
