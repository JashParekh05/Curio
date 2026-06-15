Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.
Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.
1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.
Before implementing:
State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them. Don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.
3. Surgical Changes
Touch only what you must. Clean up only your own mess.
When editing existing code:
Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it. Don't delete it.
When your changes create orphans:
Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: every changed line should trace directly to the user's request.
4. Goal-Driven Execution
Define success criteria. Loop until verified.
Transform tasks into verifiable goals:
"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:
[Step] → verify: [check]
[Step] → verify: [check]
[Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes

## Project-Specific Notes

- Before changing pipeline.py, run a single isolated test of the new approach. Don't iterate inside the pipeline.
- When a YouTube/external API call fails, identify whether it's IP-based, auth-based, or content-based BEFORE trying variants.

## Architecture Decisions

### Personalization vs. structure (the two surfaces)
The product has two feed surfaces with deliberately different philosophies:

- **Learn page (path feed)** — structure-first, personalization minimal. The
  4-beat arc (hook → what → how → outcomes) and the story pass own ordering.
  Two learners get the *same arc with different clips/styles filling each beat*,
  never a reordered or different structure. Prerequisite-safety is the hard
  constraint (never show mechanics before the concept is defined).
- **Discover page** — personalization-first. Taste vector + interest vector
  drive ranking, with room for exploration. This is where "people learn
  differently" is expressed (subject, format, pace).

Implementation: `_compute_scores` takes a weight profile. `LEARN_WEIGHTS` keeps
personalization modest (~18%); `DISCOVER_WEIGHTS` makes taste/interest dominant.

## Planned Features

### Active-learning notes & quiz overlay
Turn passive watching into active recall (the highest-leverage learning
technique) and use the otherwise-dead plan-generation latency productively.

- **Notes overlay during plan generation**: while the path is being built, show
  a live outline of *why you're learning this and what it means*, walking the
  path the same way the plan is assembled. Derived from the planner's existing
  section titles/descriptions (near-free byproduct).
- **Quiz checkpoints**: generate questions from section content + clip
  transcripts to quiz the learner. Start with **multiple-choice** (cheap,
  reliable to grade — no fuzzy free-text eval). Gate question quality with the
  same LLM-as-judge pattern used for sections/story.
- **Placement matters**: checkpoints at topic/beat boundaries or opt-in, NOT
  jammed between every clip (that turns the scroll into homework).
- **New signal**: quiz results measure what was actually *learned* (not just
  watched) — a higher-quality input for personalization and for deciding what to
  reinforce. Layer mastery-based personalization in later.
- **v1 scope**: notes outline during generation + one MCQ checkpoint per topic,
  results stored. Mastery-driven personalization is a follow-up.