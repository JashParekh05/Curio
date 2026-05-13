from pydantic import BaseModel
from typing import Literal


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
    summary: str


class Clip(BaseModel):
    id: str
    topic_slug: str
    title: str
    description: str | None
    video_url: str
    thumbnail_url: str | None
    duration_seconds: int | None
    transcript: str | None
    source_url: str | None
    source_platform: str | None


class TopicRequest(BaseModel):
    query: str
    session_id: str | None = None


class FeedResponse(BaseModel):
    topic_slug: str
    clips: list[Clip]
    processing: bool = False
