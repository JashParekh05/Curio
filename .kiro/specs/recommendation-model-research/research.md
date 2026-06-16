# Research Charter: Core Recommendation Model

Status: QUEUED (deep-dive, not yet started). This is a decision spike, not a
build spec. The output is a recommendation: keep the current heuristic, add a
lightweight learned re-ranker, or invest in a trained recommender.

## The question

Should EduReel train a recommendation model, or keep evolving the current
heuristic scoring? If a model, what kind, on what data, and when is it worth it?

## Current state (what we already have)

- Heuristic scoring in `_compute_scores` with surface profiles (LEARN_WEIGHTS,
  DISCOVER_WEIGHTS) over hook, population completion, duration, recency,
  interest, and semantic affinity.
- A behavioral taste vector (EMA of liked-clip embeddings) + per-topic interest
  vector, updated by `_event_delta`.
- Structural ordering (the section arc + story pass) on Learn; personalization-
  first ranking on Discover.
- Signals collected: `clip_events` (watch_ms, completed, replay, feedback), and
  soon quiz mastery + explicit interest-list edits.

## What to investigate

1. **Data readiness**: how many events/users/clips do we actually have? A learned
   ranker needs volume and interaction diversity; below some threshold a model
   overfits and loses to the heuristic. Quantify before deciding.
2. **Cold-start**: most users/clips are new. How would a model handle this vs the
   current embedding + interest fallback?
3. **Where a model would help most**: candidate *retrieval*, *ranking*, or
   *both*? Likely ranking on Discover first.
4. **Options and cost/ROI**:
   - A. Keep heuristic, keep tuning weights (cheapest; strong baseline).
   - B. Lightweight learned re-ranker (e.g., logistic / gradient-boosted model)
     over the existing features, trained on completion/quiz signals. Modest data
     needs; interpretable; cheap to serve.
   - C. Two-tower / embedding retrieval model (bigger data + infra; better at
     scale; overkill early).
5. **Offline evaluation**: define metrics (completion rate, watch-through,
   quiz-mastery lift, return rate) and a backtest harness so any model is judged
   against the heuristic on real logs before shipping.
6. **Online evaluation**: A/B framework to compare model vs heuristic safely.
7. **Operational cost**: training cadence, serving latency (must stay within the
   feed's low-latency budget), and failure fallback to the heuristic.

## Decision criteria

- Do we have enough labeled interaction data for the candidate model to beat the
  heuristic in an offline backtest? If not -> keep tuning the heuristic.
- Does the expected lift justify the serving/maintenance cost and added latency?
- Can it fall back to the heuristic cleanly on cold-start and failure?

## Likely prior (to validate, not assume)

For an early-stage app, training a full recommender is probably premature; the
highest-ROI path is usually: keep the heuristic, enrich features (quiz mastery,
explicit interests), and add a lightweight learned re-ranker only once a backtest
shows it beats the heuristic. The deep-dive should confirm or refute this with
the actual data.

## Inputs needed before starting

- Event volume snapshot (users, clips, clip_events, completion distribution).
- The new signals from the quiz + interest-list features (more labels).
- Agreement on the success metric the model must move.
