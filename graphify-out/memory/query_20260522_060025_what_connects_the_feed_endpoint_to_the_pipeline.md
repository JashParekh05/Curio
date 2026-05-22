---
type: "query"
date: "2026-05-22T06:00:25.030288+00:00"
question: "what connects the feed endpoint to the pipeline?"
contributor: "graphify"
source_nodes: ["get_feed endpoint", "process_video (service)", "embed_texts()", "_cached_slug_embeddings()", "_seed_topics_bg"]
---

# Q: what connects the feed endpoint to the pipeline?

## Answer

The feed endpoint and pipeline are not directly wired. They connect through shared infrastructure: (1) the embed_texts() embeddings service, called by both _cached_slug_embeddings() in feed.py (to rank) and process_video() (to embed new clips); (2) the clips.embedding vector(384) Supabase column, written by the pipeline and read by the feed's _compute_scores/cosine_similarity ranking; (3) the cold-start _seed_topics_bg background trigger fired by get_feed/get_discover_feed when a slug has no clips. process_video is also semantically similar to run_pipeline (duplicated logic).

## Source Nodes

- get_feed endpoint
- process_video (service)
- embed_texts()
- _cached_slug_embeddings()
- _seed_topics_bg