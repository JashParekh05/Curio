# Requirements Document

Feature: Active-Learning Notes & Quiz

## Introduction

EduReel currently teaches by playing short clips. Watching is passive, and most
viewers never reach the end of a topic, so retention and actual learning are
both leaky. This feature adds active recall (the highest-leverage learning
technique) without breaking the addictive scroll, via a side "study panel" that
shows the plan/notes and an optional, gamified multiple-choice quiz per topic.

This is grounded in what already exists: the section planner produces titles and
descriptions per topic, clip transcripts are cached, the Plan panel overlay is
built, and we have an established LLM-as-judge quality-gate pattern. Mastery-
driven personalization is out of scope for v1 (it consumes the signal this
builds).

## Glossary

- **Study panel**: the existing in-feed Plan panel, extended to host notes and quiz.
- **Notes/outline**: the path's topics and their sections (what you'll learn).
- **MCQ**: a multiple-choice question with one correct option and 2-3 distractors.
- **Mastery**: per-topic correct rate; a topic is "mastered" past a threshold.
- **Points**: score awarded for first-time correct answers, shown in the panel.
- **Streak**: count of consecutive correct answers; drives a bonus multiplier and
  resets on a wrong answer.

## Requirements

### Requirement 1: Study panel as the notes/outline surface
**User Story:** As a learner, I want a side panel that shows what I'm learning
and lets me test myself, so I have one calm place to review and reinforce
without interrupting the feed.

#### Acceptance Criteria
1. The quiz and notes SHALL live in the side Plan panel, NEVER inline in the main
   clip feed.
2. The panel SHALL show the path's topics and their sections as the notes
   outline, reusing the existing plan tree.
3. WHEN section data is unavailable THEN the panel SHALL show a lightweight
   placeholder, never a blank or error state.
4. Opening or using the panel SHALL NOT block or interrupt clip playback.

### Requirement 2: Quiz question generation (multiple-choice only)
**User Story:** As a learner, I want to be quizzed on what a topic taught, so I
actually retain it instead of passively watching.

#### Acceptance Criteria
1. WHEN a topic has generated clips THEN the system SHALL generate multiple-
   choice questions for that topic from its section content and clip transcripts.
2. ALL questions SHALL be multiple-choice; the system SHALL NEVER produce free-
   text, true/false, or any non-MCQ format.
3. Each question SHALL have exactly one correct answer and 2-3 plausible
   distractors, with a one-line explanation of the correct answer.
4. Questions SHALL be generated in the background and cached per topic; repeated
   requests SHALL NOT regenerate them.
5. IF generation fails THEN the system SHALL skip quizzing for that topic and
   SHALL NOT block clip delivery.
6. Questions SHALL test understanding of the topic's substance, not trivia about
   the video.
7. Questions SHALL be phrased to spark curiosity (scenario- or "what happens
   if"-style where natural), not dry definitional recall, so answering feels
   like a fun challenge rather than a test.

### Requirement 3: Question quality gate
**User Story:** As a learner, I want quiz questions to be unambiguous and
correct, so I trust the app instead of getting frustrated by bad questions.

#### Acceptance Criteria
1. WHEN a question is generated THEN an LLM judge SHALL score it for correctness,
   single-unambiguous-answer, and distractor plausibility before it is stored.
2. IF a question fails the quality bar THEN it SHALL be regenerated or discarded,
   bounded by a maximum number of attempts.
3. The system SHALL never store a question the judge flags as having multiple
   correct answers or no correct answer.

### Requirement 4: Optional, gamified delivery in the study panel
**User Story:** As a learner, I want quizzing to feel like an optional game I can
dip into from the side, not a test that blocks my scroll.

#### Acceptance Criteria
1. Quiz questions for a topic SHALL be presented inside that topic's entry in the
   Plan panel, alongside or after its section outline.
2. Answering SHALL be entirely optional; the learner can ignore the quiz and keep
   watching with no penalty or gating.
3. The presentation SHALL feel like a game: tappable answer choices, immediate
   right/wrong reveal, and points awarded for correct answers.
4. A learner SHALL NOT be shown an already-answered question as unanswered;
   answered questions SHALL render in their revealed state when revisited.
5. The panel SHALL NOT auto-open or force the quiz in front of the learner.

### Requirement 5: Answer feedback, scoring, and grading
**User Story:** As a learner, I want instant feedback and points when I answer,
so it's satisfying and I know whether I understood it.

#### Acceptance Criteria
1. WHEN a learner selects an answer THEN the system SHALL immediately reveal
   correct/incorrect, highlight the correct choice, and show the explanation.
2. Grading SHALL be deterministic with no LLM call at answer time.
3. WHEN a learner answers correctly THEN points SHALL be awarded; the panel SHALL
   show a running score and per-topic progress.
4. WHEN a learner answers THEN the result SHALL be recorded (question, chosen
   answer, correctness, topic, timestamp, session/user); recording is best-effort
   and SHALL NOT block the feedback shown.
5. Re-answering an already-answered question SHALL NOT award points again.
6. The system SHALL track a streak of consecutive first-time correct answers and
   apply a bonus that grows with the streak; a wrong answer SHALL reset the
   streak to zero. The current streak SHALL be visible while answering.

### Requirement 6: Mastery and points storage
**User Story:** As a returning learner, I want the app to remember my points and
what I've mastered, so progress feels persistent and motivating.

#### Acceptance Criteria
1. The system SHALL persist per-topic quiz results and points for both guest and
   authenticated users, carried across the guest-to-account upgrade.
2. The system SHALL expose a per-topic mastery summary (questions answered,
   correct rate, points) via an API.
3. Stored results SHALL be additive and SHALL NOT overwrite prior attempts.
4. A topic SHALL be considered "mastered" when its correct rate meets a defined
   threshold, surfaced as a badge in the panel.

### Requirement 7: Non-functional
**User Story:** As a maintainer, I want the feature to be safe, cheap, and
testable, so it matches the rest of the codebase.

#### Acceptance Criteria
1. No quiz or notes operation SHALL block clip generation or playback; all are
   best-effort with graceful fallback.
2. Question generation SHALL be cached per topic so cost is a one-time background
   expense, not per-view.
3. New backend logic (validation, grading, mastery aggregation, quality gate)
   SHALL be pure, unit-testable functions with the test rigor used across the
   codebase.
4. Schema changes SHALL be additive and nullable, consistent with existing
   migration conventions.

### Requirement 8: Engagement and motivation
**User Story:** As a learner, I want quizzing to feel rewarding and game-like, so
I stay engaged and want to come back.

#### Acceptance Criteria
1. The panel SHALL show a running total score that updates immediately when a
   correct answer is given.
2. Consecutive correct answers within a panel session SHALL build a visible
   streak, and the streak SHALL reset on an incorrect answer.
3. An active streak SHALL be reflected with a visible, motivational flourish (a
   streak count/indicator), in-session, WHILE remaining calm and non-disruptive.
4. The panel SHALL show path-level progress (e.g., topics mastered out of total)
   so the learner sees momentum across the whole path.
5. Feedback on a correct answer SHALL be visibly satisfying (a brief points
   flourish), WHILE NOT disrupting the panel or the feed.
6. Engagement mechanics SHALL never gate or pressure the learner: no timers, no
   forced answers, and no penalty beyond a streak reset.

## Out of Scope (v1)

- Mastery-driven personalization (adjusting the feed/difficulty from quiz
  results) — a follow-up that consumes this signal.
- Free-text / open-ended answers and LLM grading at answer time.
- Spaced-repetition scheduling.
