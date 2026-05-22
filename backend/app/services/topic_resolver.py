"""Similar-topic resolver.

When the curriculum agent invents a fresh slug for a concept we already have
clips for (e.g. "binary-search" vs an existing "binary-search-algorithm"), we'd
otherwise run the whole pipeline again — burning YouTube quota and OpenAI calls
to rebuild content that already exists. This resolves a generated topic to an
existing, already-seeded topic when their names are semantically close, so the
path reuses cached clips instead of regenerating.

Conservative by design: only remaps on a high name-similarity match AND only
when the matched topic actually has clips.
"""
from __future__ import annotations

import logging

from app.services.embeddings import embed_text, embed_texts, cosine_similarity

logger = logging.getLogger(__name__)

# Name-embedding cosine above which two topics are treated as the same concept.
# High on purpose — a wrong merge serves the wrong content, which is worse than
# regenerating.
SIMILARITY_THRESHOLD = 0.84

# (slug, name, embedding) for every known topic. Built once per process.
_index: list[tuple[str, str, list[float]]] | None = None


def _build_index() -> list[tuple[str, str, list[float]]]:
    from app.db.supabase import get_client
    db = get_client()
    try:
        rows = db.table("topics").select("slug,name").limit(5000).execute().data
    except Exception as exc:
        logger.warning(f"[topic_resolver] failed to load topics: {exc}")
        return []
    names = [r.get("name") or r["slug"] for r in rows]
    embs = embed_texts(names)
    return [(r["slug"], r.get("name") or r["slug"], e) for r, e in zip(rows, embs) if e is not None]


def _get_index() -> list[tuple[str, str, list[float]]]:
    global _index
    if _index is None:
        _index = _build_index()
        logger.info(f"[topic_resolver] built index of {len(_index)} topics")
    return _index


def register_topic(slug: str, name: str) -> None:
    """Add a freshly created topic to the in-memory index so later queries in
    the same process can match against it."""
    idx = _get_index()
    if any(s == slug for s, _, _ in idx):
        return
    emb = embed_text(name or slug)
    if emb is not None:
        idx.append((slug, name or slug, emb))


def resolve_topic(slug: str, name: str) -> str | None:
    """Return an existing topic slug (with clips) that means the same thing as
    (slug, name), or None if there's no close match worth reusing."""
    from app.db.supabase import get_client

    qv = embed_text(name or slug)
    if qv is None:
        return None

    best_slug, best_score = None, 0.0
    for s, _, e in _get_index():
        if s == slug:
            continue
        score = cosine_similarity(qv, e)
        if score > best_score:
            best_slug, best_score = s, score

    if not best_slug or best_score < SIMILARITY_THRESHOLD:
        return None

    # Only reuse if the matched topic actually has clips — otherwise reusing it
    # buys nothing and could point at an empty topic.
    try:
        has_clips = (
            get_client().table("clips").select("id").eq("topic_slug", best_slug).limit(1).execute().data
        )
    except Exception as exc:
        logger.warning(f"[topic_resolver] clip check failed for {best_slug}: {exc}")
        return None
    if not has_clips:
        return None

    logger.info(f"[topic_resolver] reuse '{slug}' -> '{best_slug}' (sim={best_score:.3f})")
    return best_slug
