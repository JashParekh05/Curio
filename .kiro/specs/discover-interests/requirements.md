# Requirements Document

Feature: Discover Interest List (mutable watchlist)

## Introduction

The main feature is "I want to learn this specific topic" -> a structured path.
This is the complement on the Discover side: a mutable list of subjects a learner
wants to see more of, in their own words -- "the Bhagavad Gita", "dynamic
programming on LeetCode", "modern history of the Middle East". These naturally
bias the Discover feed toward what the person actually cares about, are editable
any time, and are surfaced on login so the list stays fresh. Every edit is also a
high-quality explicit signal fed back into personalization.

This is queued (requirements drafted; design and tasks to follow). It builds on
existing pieces: `user_profiles.interests`, the discover seeding + taste-vector
machinery, and the personalization vectors.

## Glossary

- **Interest entry**: a free-text subject/concept the user wants more of.
- **Interest list**: the user's mutable set of interest entries.
- **Discover bias**: using the interest list to seed and rank the Discover feed.

## Requirements

### Requirement 1: Manage the interest list
**User Story:** As a learner, I want to keep a personal list of subjects I'm into,
in my own words, so Discover shows me more of what I care about.

#### Acceptance Criteria
1. The system SHALL let a user add, edit, and remove free-text interest entries.
2. Entries SHALL accept natural phrasing (e.g., "the Gita", "DP on LeetCode"),
   not only predefined tags.
3. The list SHALL be persisted per user and reflected immediately after edits.
4. The system SHALL de-duplicate near-identical entries to avoid clutter.

### Requirement 2: Interests bias the Discover feed
**User Story:** As a learner, I want my listed interests to actually shape
Discover, so the feed feels like mine.

#### Acceptance Criteria
1. WHEN a user has interest entries THEN Discover SHALL bias topic selection and
   ranking toward content semantically related to those entries.
2. IF related content does not yet exist THEN the system SHALL seed/generate it
   in the background (reusing the existing seeding path), without blocking the
   feed.
3. The interest list SHALL complement, not replace, behavioral personalization
   (taste/interest vectors); both SHALL contribute.

### Requirement 3: Surfaced and editable on login
**User Story:** As a returning learner, I want to be reminded of and able to
tweak my interests when I come back, so the feed stays current with me.

#### Acceptance Criteria
1. WHEN a user logs in THEN the system SHALL prompt them to review/update their
   interest list, in a lightweight, dismissible way.
2. The prompt SHALL NOT block access to the app and SHALL be skippable.
3. A user SHALL be able to open and edit the interest list at any time, not only
   at login.

### Requirement 4: Interests as a personalization signal
**User Story:** As the system, I want explicit interest edits as signals, so
recommendations improve from clear intent, not just behavior.

#### Acceptance Criteria
1. WHEN the interest list changes THEN the system SHALL update the user's
   personalization signals (e.g., derive/refresh a taste vector from entries).
2. Explicit interest signals SHALL be weighted as strong, clear intent relative
   to ambiguous behavioral signals.

### Requirement 5: Persistence and carryover
**User Story:** As a guest who later signs up, I want my interest list to carry
over, so I don't lose my setup.

#### Acceptance Criteria
1. The interest list SHALL persist for both guest and authenticated users.
2. The list SHALL carry across the guest-to-account upgrade with no loss.

### Requirement 6: Non-functional
**User Story:** As a maintainer, I want this to be safe and cheap, matching the
codebase conventions.

#### Acceptance Criteria
1. Interest management and Discover biasing SHALL be best-effort and SHALL NOT
   block the Discover feed.
2. Background seeding triggered by interests SHALL be idempotent and cost-bounded.
3. New logic (matching, de-duplication, signal derivation) SHALL be pure and
   unit-testable per codebase conventions.

## Out of Scope (v1)

- Shared/collaborative interest lists.
- Auto-suggesting interests from external trends.
