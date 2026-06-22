import json
from pydantic import BaseModel, Field, field_validator
from typing import Literal


ConceptType = Literal["problem_solving", "conceptual", "default"]

PedagogicalRole = Literal[
    # problem-solving arc
    "problem_statement", "meaning", "visualization", "approach",
    "worked_example", "edge_cases",
    # conceptual arc
    "definition", "motivation", "mechanism", "example", "common_misconception",
]

DefectType = Literal[
    "prerequisite_gap", "conceptual_jump", "contradiction",
    "redundancy", "unfilled_role", "circular_dependency", "missing_piece",
]


class Topic(BaseModel):
    slug: str
    name: str
    difficulty: Literal["beginner", "intermediate", "advanced"]
    prerequisites: list[str] = []
    rationale: str


class LearningPath(BaseModel):
    session_id: str
    user_query: str
    topics: list[Topic]
    summary: str = ""
    familiarity_prompt: str | None = None
    suggested_start_index: int = 0


class Clip(BaseModel):
    id: str
    topic_slug: str
    title: str
    description: str | None = None
    video_url: str
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    transcript: str | None = None
    source_url: str | None = None
    source_platform: str | None = None
    hook_score: float = 0.5
    final_score: float | None = None
    created_at: str | None = None
    section_index: int | None = None
    narrative_rank: int | None = None
    pedagogical_role: PedagogicalRole | None = None
    role_ordinal: int | None = None          # realized-arc position (1-based)
    concept_label: str | None = None
    engagement_score: float | None = None    # [0,1], tiebreaker only
    coherence_score: float | None = None     # topic-level, mirrored per clip like story_score
    content_level: str | None = None         # Content_Level; None for pre-feature clips
    embedding: list[float] | None = Field(default=None, exclude=True)

    @field_validator("embedding", mode="before")
    @classmethod
    def parse_embedding(cls, v):
        if isinstance(v, str):
            return json.loads(v)
        return v


GradeLevel = Literal["preschool", "elementary", "middle_school", "high_school", "college", "professional"]


class InterestsPayload(BaseModel):
    interests: list[str]
    grade_level: GradeLevel | None = None


class ClipEvent(BaseModel):
    session_id: str | None = None
    watch_ms: int
    completed: bool = False
    replay_count: int = 0
    feedback: Literal["want_more", "already_know"] | None = None


class Impression(BaseModel):
    id: str | None = None                      # Impression identifier (final journey tie-break)
    clip_id: str
    session_id: str | None = None              # null for discover (no session)
    user_id: str | None = None                 # null when learner unresolved (Req 1.9)
    feed_surface: Literal["discover", "learn_path"]
    feed_position: int                         # 0-based, consecutive within a serve (Req 1.4)
    pedagogical_role: str | None = None         # Served_Context snapshot (Req 1.3, 1.5)
    content_level: str | None = None
    source_platform: str | None = None
    topic_slug: str | None = None
    served_at: str                             # UTC ISO-8601 timestamp (Req 1.6)


class TopicRequest(BaseModel):
    query: str = Field(..., max_length=500)
    user_id: str | None = None


class CheckpointCard(BaseModel):
    """A soft, always-skippable checkpoint card woven inline into a topic's clip
    sequence (Phase 1, Req 1.5). Mirrors the Checkpoint_Placement core's
    ``CheckpointCard`` for transport. ``after_clip_index`` points within the
    topic's served ``clips`` list; the card is rendered between clips and never
    stops the feed advancing (``skippable`` is always True)."""
    stage: Literal["check", "post"]
    after_clip_index: int
    topic_slug: str
    section_index: int | None = None
    skippable: bool = True


class FeedLevel(BaseModel):
    """One Level of the serialized LeveledPath returned alongside the feed so the
    frontend can render the Level -> Topic -> Beat stepper (Req 1.1, 4.2). NULL
    ``learning_paths.levels`` degrades to a single implicit level."""
    ordinal: int
    name: str
    topic_slugs: list[str] = []


class FeedResponse(BaseModel):
    topic_slug: str
    clips: list[Clip]
    processing: bool = False
    failed: bool = False  # terminal: out of retry budget and still empty
    # Additive (Phase 1): soft inline checkpoint cards for this topic and the
    # leveled-path projection for the stepper. Both default empty so legacy
    # consumers and other endpoints returning FeedResponse are unaffected.
    checkpoints: list[CheckpointCard] = []
    levels: list[FeedLevel] = []


class DiscoverResponse(BaseModel):
    clips: list[Clip]
    processing: bool = False  # true when library empty + topup running (Req 5.6)


class TopicRecommendation(BaseModel):
    slug: str
    name: str
    difficulty: str
    clip_count: int
    rationale: str


class LearningAtom(BaseModel):
    id: str
    topic_slug: str
    video_id: str
    source_url: str
    role: PedagogicalRole
    concept: str                       # 1-200 chars, non-empty
    prior_knowledge: list[str] = []    # 0-50 distinct, none == concept
    start: float                       # >= 0
    end: float                         # > start, <= transcript duration
    transcript: str | None = None


class ArcRole(BaseModel):
    role: PedagogicalRole
    ordinal: int                       # consecutive from 1, matches template order


class PlannedArc(BaseModel):
    topic_slug: str
    concept_type: ConceptType
    default_applied: bool = False      # Req 1.7
    template_empty: bool = False       # Req 1.8 -> roles == []
    roles: list[ArcRole] = []


class CoherenceDefect(BaseModel):
    defect_type: DefectType
    clip_positions: list[int] = []     # 1-based ordinals of affected clips
    role: PedagogicalRole | None = None


class CoherenceResult(BaseModel):
    coherence_score: float             # [0,1], 2 dp
    defects: list[CoherenceDefect] = []
    round_index: int = 0


class ArcDiff(BaseModel):
    missing_roles: list[PedagogicalRole] = []
    order_mismatch_positions: list[int] = []
    aligned: bool


class AlignmentResult(BaseModel):
    aligned: bool
    diff: ArcDiff
    unresolved: bool = False
